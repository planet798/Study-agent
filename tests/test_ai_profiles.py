"""AI Profile / AIConfigService / SecretStore 测试（fake keyring，不接触真实凭据）。

覆盖需求：创建 / 重命名 / 改 Base URL / 改 Model / 写 keyring / DB 不含 Key /
删除同时删 secret / active 切换 / 删除 active 的安全行为 / legacy 兼容 /
无 profile 时友好报错 / 日志不泄露 Key / test connection 分类。
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.ai.ai_profiles import AIProfileRepository
from app.ai.client import AdaptiveAIClient, ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, sanitize_text
from app.ai.config_service import (
    AIConfigService,
    SOURCE_ENV,
    SOURCE_NONE,
    SOURCE_PROFILE,
    classify_connection_error,
    run_connection_test,
)
from app.ai.interface import AIServiceError
from app.ai.secrets import SecretStore, SecretStoreError, make_secret_ref


class TestSecretStore:
    def test_set_get_delete(self, fake_keyring):
        store = SecretStore(keyring_module=fake_keyring)
        store.set("ai-profile/1", "sk-test-abc")
        assert store.get("ai-profile/1") == "sk-test-abc"
        store.delete("ai-profile/1")
        assert store.get("ai-profile/1") is None

    def test_set_failure_raises(self, fake_keyring):
        fake_keyring.fail_on_set = True
        store = SecretStore(keyring_module=fake_keyring)
        with pytest.raises(SecretStoreError):
            store.set("ai-profile/1", "sk-test-abc")

    def test_get_failure_returns_none(self, fake_keyring):
        def boom(*a, **k):
            raise RuntimeError("no backend")

        store = SecretStore(keyring_module=boom)
        assert store.get("ai-profile/1") is None

    def test_make_secret_ref(self):
        assert make_secret_ref(3) == "ai-profile/3"


class TestProfileRepository:
    def test_create_and_get(self, conn):
        repo = AIProfileRepository(conn)
        p = repo.create("USTC DeepSeek", "https://api.llm.ustc.edu.cn/v1",
                        "deepseek-v4-flash-ascend", secret_ref="ai-profile/1")
        assert p.id > 0
        assert repo.get(p.id).display_name == "USTC DeepSeek"
        assert repo.count() == 1

    def test_set_active_only_one(self, conn):
        repo = AIProfileRepository(conn)
        a = repo.create("A", "https://a", "m")
        b = repo.create("B", "https://b", "m")
        repo.set_active(a.id)
        assert repo.get_active().id == a.id
        repo.set_active(b.id)
        assert repo.get_active().id == b.id
        actives = [p for p in repo.list_all() if p.is_active]
        assert len(actives) == 1

    def test_schema_has_no_api_key_column(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_profiles)")}
        assert "api_key" not in cols
        assert "secret_ref" in cols


class TestAIConfigServiceCRUD:
    def test_create_profile_writes_keyring_and_db_has_no_key(
        self, ai_config_service, fake_keyring, conn
    ):
        p = ai_config_service.create_profile(
            "USTC DeepSeek", "https://api.llm.ustc.edu.cn/v1",
            "deepseek-v4-flash-ascend", api_key="sk-test-secret-123",
        )
        # Key 写入 keyring
        assert fake_keyring.store[("study-agent", p.secret_ref)] == "sk-test-secret-123"
        # DB 中绝不出现 Key 明文
        rows = conn.execute("SELECT * FROM ai_profiles").fetchall()
        for row in rows:
            for value in tuple(row):
                assert "sk-test-secret-123" not in str(value)
        # 第一个 profile 自动 active
        assert ai_config_service.get_active_profile().id == p.id
        assert ai_config_service.has_api_key(p.id) is True

    def test_rename_keeps_key(self, ai_config_service, fake_keyring):
        p = ai_config_service.create_profile(
            "USTC API", "https://x/v1", "m", api_key="sk-test-1"
        )
        ai_config_service.rename_profile(p.id, "学校 DeepSeek Flash")
        assert ai_config_service.get_profile(p.id).display_name == "学校 DeepSeek Flash"
        assert fake_keyring.store[("study-agent", p.secret_ref)] == "sk-test-1"

    def test_update_base_url_and_model(self, ai_config_service):
        p = ai_config_service.create_profile("A", "https://old/v1", "m1",
                                             api_key="sk-test-1")
        ai_config_service.update_profile(p.id, base_url="https://new/v1", model="m2")
        got = ai_config_service.get_profile(p.id)
        assert got.base_url == "https://new/v1"
        assert got.model == "m2"

    def test_set_api_key_updates_keyring_only(self, ai_config_service, fake_keyring, conn):
        p = ai_config_service.create_profile("A", "https://x/v1", "m")
        ai_config_service.set_api_key(p.id, "sk-test-new")
        assert fake_keyring.store[("study-agent", p.secret_ref)] == "sk-test-new"
        rows = conn.execute("SELECT * FROM ai_profiles").fetchall()
        for row in rows:
            assert "sk-test-new" not in " ".join(str(v) for v in tuple(row))

    def test_delete_profile_removes_secret(self, ai_config_service, fake_keyring):
        p = ai_config_service.create_profile("A", "https://x/v1", "m",
                                             api_key="sk-test-1")
        ref = p.secret_ref
        ai_config_service.delete_profile(p.id)
        assert ai_config_service.get_profile(p.id) is None
        assert fake_keyring.store.get(("study-agent", ref)) is None

    def test_switch_active(self, ai_config_service):
        a = ai_config_service.create_profile("A", "https://a/v1", "m", api_key="k")
        b = ai_config_service.create_profile("B", "https://b/v1", "m", api_key="k")
        assert ai_config_service.get_active_profile().id == a.id
        ai_config_service.set_active(b.id)
        assert ai_config_service.get_active_profile().id == b.id

    def test_delete_active_with_reassign(self, ai_config_service):
        a = ai_config_service.create_profile("A", "https://a/v1", "m", api_key="k")
        b = ai_config_service.create_profile("B", "https://b/v1", "m", api_key="k")
        ai_config_service.delete_profile(a.id, reassign_to=b.id)
        assert ai_config_service.get_active_profile().id == b.id

    def test_delete_active_without_reassign_leaves_none(self, ai_config_service):
        a = ai_config_service.create_profile("A", "https://a/v1", "m", api_key="k")
        b = ai_config_service.create_profile("B", "https://b/v1", "m", api_key="k")
        ai_config_service.delete_profile(a.id)
        assert ai_config_service.get_active_profile() is None
        cfg = ai_config_service.get_runtime_config()
        assert cfg.source == SOURCE_NONE
        assert cfg.is_configured is False

    def test_create_rolls_back_when_keyring_fails(self, ai_config_service, fake_keyring):
        fake_keyring.fail_on_set = True
        with pytest.raises(SecretStoreError):
            ai_config_service.create_profile("A", "https://a/v1", "m", api_key="k")
        assert ai_config_service.profile_count() == 0


class TestRuntimeConfig:
    def test_from_active_profile(self, ai_config_service):
        p = ai_config_service.create_profile(
            "USTC", "https://api.llm.ustc.edu.cn/v1", "deepseek-v4-flash-ascend",
            api_key="sk-test-1",
        )
        cfg = ai_config_service.get_runtime_config()
        assert cfg.source == SOURCE_PROFILE
        assert cfg.api_key == "sk-test-1"
        assert cfg.model == "deepseek-v4-flash-ascend"
        assert cfg.profile_id == p.id

    def test_missing_key_is_not_configured(self, ai_config_service):
        p = ai_config_service.create_profile("A", "https://a/v1", "m")
        cfg = ai_config_service.get_runtime_config()
        assert cfg.source == SOURCE_PROFILE
        assert cfg.is_configured is False
        assert "API Key" in cfg.error_message

    def test_legacy_env_fallback_when_no_profiles(self, conn, fake_keyring):
        svc = AIConfigService(
            conn=conn,
            secret_store=SecretStore(keyring_module=fake_keyring),
            env={
                ENV_API_KEY: "sk-env-1",
                ENV_BASE_URL: "https://env.example.com/v1",
                ENV_MODEL: "env-model",
            },
        )
        cfg = svc.get_runtime_config()
        assert cfg.source == SOURCE_ENV
        assert cfg.api_key == "sk-env-1"
        assert cfg.model == "env-model"

    def test_import_legacy_profile(self, conn, fake_keyring):
        svc = AIConfigService(
            conn=conn,
            secret_store=SecretStore(keyring_module=fake_keyring),
            env={ENV_API_KEY: "sk-env-2", ENV_BASE_URL: "https://e/v1",
                 ENV_MODEL: "m-env"},
        )
        p = svc.import_legacy_profile()
        assert p.display_name == "Legacy / 当前配置"
        cfg = svc.get_runtime_config()
        assert cfg.source == SOURCE_PROFILE
        assert cfg.api_key == "sk-env-2"
        assert svc.get_active_profile().id == p.id

    def test_no_profiles_no_env_not_configured(self, conn, fake_keyring):
        svc = AIConfigService(conn=conn,
                              secret_store=SecretStore(keyring_module=fake_keyring),
                              env={})
        cfg = svc.get_runtime_config()
        assert cfg.source == SOURCE_ENV
        assert cfg.is_configured is False


class FakeResp:
    def __init__(self, content: str):
        body = {"choices": [{"message": {"content": content}}]}
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestTestConnection:
    def test_success(self):
        def urlopen(request, timeout=None):
            return FakeResp("OK")

        result = run_connection_test("https://x/v1", "m", "sk-test-1",
                                        urlopen=urlopen)
        assert result.ok is True
        assert result.message == "连接成功"
        assert result.model == "m"

    def test_auth_failure(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "unauth", {}, None)

        result = run_connection_test("https://x/v1", "m", "sk-test-1",
                                        urlopen=urlopen)
        assert result.ok is False
        assert "认证失败" in result.message
        # 绝不回显 Key
        assert "sk-test-1" not in result.message

    def test_model_not_found(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, None)

        result = run_connection_test("https://x/v1", "m", "sk-test-1",
                                        urlopen=urlopen)
        assert "模型不存在" in result.message or "接口地址" in result.message

    def test_timeout(self):
        import socket

        def urlopen(request, timeout=None):
            raise socket.timeout("timed out")

        result = run_connection_test("https://x/v1", "m", "sk-test-1",
                                        urlopen=urlopen)
        assert result.ok is False
        assert "超时" in result.message

    def test_incomplete_config(self):
        result = run_connection_test("", "", "")
        assert result.ok is False
        assert "不完整" in result.message

    def test_service_test_connection_reads_keyring(self, ai_config_service):
        p = ai_config_service.create_profile("A", "https://x/v1", "m",
                                             api_key="sk-test-1")

        def urlopen(request, timeout=None):
            return FakeResp("OK")

        result = ai_config_service.test_connection(
            p.base_url, p.model, profile_id=p.id, urlopen=urlopen
        )
        assert result.ok is True

    def test_classify(self):
        assert "认证失败" in classify_connection_error("AI HTTP 错误 401: x")
        assert "服务器不可用" in classify_connection_error("AI HTTP 错误 503: x")


class TestSecretSafety:
    def test_sanitize_removes_key(self):
        msg = "error Bearer sk-test-secret-123 and sk-test-secret-123 again"
        out = sanitize_text(msg, "sk-test-secret-123")
        assert "sk-test-secret-123" not in out
        assert out.count("***") >= 1

    def test_adaptive_client_error_does_not_leak_key(self):
        class Cfg:
            api_key = "sk-test-secret-999"
            base_url = "https://x/v1"
            model = "m"
            is_configured = True
            error_message = ""

        class FP:
            def read(self):
                return b"bad key sk-test-secret-999"

            def close(self):
                return None

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 401, "unauthorized", {}, FP(),
            )

        client = AdaptiveAIClient(lambda: Cfg(), urlopen=urlopen)
        with pytest.raises(AIServiceError) as exc:
            client.chat("s", "u")
        assert "sk-test-secret-999" not in str(exc.value)


class TestAdaptiveClientNoKeyInLogs:
    def test_is_configured_and_switch_takes_effect_immediately(self):
        state = {"model": "m1", "key": "k1"}

        class Cfg:
            base_url = "https://x/v1"
            is_configured = True
            error_message = ""

            @property
            def model(self):
                return state["model"]

            @property
            def api_key(self):
                return state["key"]

        captured = []

        def urlopen(request, timeout=None):
            captured.append(json.loads(request.data.decode())["model"])
            return FakeResp('{"a":1}')

        client = AdaptiveAIClient(lambda: Cfg(), urlopen=urlopen)
        client.chat("s", "u")
        state["model"] = "m2"
        client.chat("s", "u")
        assert captured == ["m1", "m2"]

    def test_not_configured_raises_friendly(self):
        class Cfg:
            is_configured = False
            error_message = "未选择当前 AI 配置"
            base_url = ""
            model = ""
            api_key = ""

        client = AdaptiveAIClient(lambda: Cfg())
        assert client.is_configured() is False
        with pytest.raises(AIServiceError) as exc:
            client.chat("s", "u")
        assert "未选择当前 AI 配置" in str(exc.value)
