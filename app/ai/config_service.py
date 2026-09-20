"""统一 AI 配置入口（AIConfigService）。

职责：
- 管理 AI Provider Profile（增 / 删 / 改 / 重命名 / 切换当前）；
- 管理 API Key（只通过 :class:`SecretStore` 写入系统 keyring，绝不进 DB / 日志）；
- 提供线程安全的 :meth:`get_runtime_config`：所有 AI Client 在**每次请求**时
  解析“当前配置”，因此切换 / 修改后无需重启即生效；
- 兼容 legacy 环境变量（DEEPSEEK_*）：数据库没有任何 Profile 时回退；
- 提供 :meth:`test_connection`（极小请求，后台执行，不泄漏 Key）。

线程安全：生产环境通过 ``db_path`` 打开**每次调用独立**的 SQLite 连接，
不把主线程 connection / repository 传进 worker（避免 SQLite thread affinity）。
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from .ai_profiles import (
    PROVIDER_TYPE_OPENAI_COMPATIBLE,
    AIProfile,
    AIProfileRepository,
)
from .client import (
    DEFAULT_BASE_URL,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    TEST_MAX_TOKENS,
    TEST_TIMEOUT,
    sanitize_text,
    send_chat_request,
)
from .interface import AIServiceError
from .secrets import SecretStore, SecretStoreError, make_secret_ref

# 运行时配置来源
SOURCE_PROFILE = "profile"
SOURCE_ENV = "env"
SOURCE_NONE = "none"


@dataclass
class RuntimeAIConfig:
    """一次 AI 请求实际使用的配置（Key 只存在于内存，绝不写库/日志）。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    provider_type: str = PROVIDER_TYPE_OPENAI_COMPATIBLE
    source: str = SOURCE_NONE
    profile_id: Optional[int] = None
    profile_name: str = ""
    error_message: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    model: str = ""
    latency_ms: int = 0
    source: str = ""


def classify_connection_error(message: str) -> str:
    """把底层错误信息转成用户可读的分类提示（不包含 Key）。"""
    text = message or ""
    if "HTTP 错误 401" in text or "HTTP 错误 403" in text:
        return "认证失败：API Key 无效或无权限"
    if "HTTP 错误 404" in text:
        return "接口地址错误或模型不存在（HTTP 404）"
    if "HTTP 错误 400" in text:
        return "请求被拒绝（HTTP 400），请检查 Base URL / Model"
    if "HTTP 错误 429" in text:
        return "请求过于频繁（HTTP 429），请稍后重试"
    if "HTTP 错误 5" in text:
        return "服务器不可用（HTTP 5xx）"
    if "超时" in text:
        return "连接超时"
    if "网络错误" in text:
        return "无法连接服务器"
    return text or "未知错误"


def run_connection_test(
    base_url: str,
    model: str,
    api_key: str,
    timeout: float = TEST_TIMEOUT,
    urlopen=None,
) -> ConnectionTestResult:
    """用普通值（base_url / model / api_key）测试连接。

    不依赖任何 DB / repository，因此可安全地放在 QThread worker 中执行。
    只发送极小请求，绝不回显 Key。
    """
    key = (api_key or "").strip()
    model = (model or "").strip()
    base_url = (base_url or "").strip().rstrip("/")

    if not base_url or not model or not key:
        return ConnectionTestResult(
            ok=False,
            message="配置不完整：Base URL / Model / API Key 均不能为空",
            model=model,
        )

    started = time.monotonic()
    try:
        content = send_chat_request(
            api_key=key,
            base_url=base_url,
            model=model,
            system_prompt="You are a connectivity tester.",
            user_prompt="reply OK",
            json_mode=False,
            temperature=0.0,
            max_tokens=TEST_MAX_TOKENS,
            timeout=timeout,
            urlopen=urlopen,
        )
    except AIServiceError as e:
        return ConnectionTestResult(
            ok=False,
            message=classify_connection_error(sanitize_text(str(e), key)),
            model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as e:  # noqa: BLE001 - 任何异常都转友好提示
        return ConnectionTestResult(
            ok=False,
            message=classify_connection_error(sanitize_text(str(e), key)),
            model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    latency = int((time.monotonic() - started) * 1000)
    return ConnectionTestResult(
        ok=True,
        message="连接成功",
        model=model,
        latency_ms=latency,
        source=(content or "").strip()[:50],
    )


class AIConfigService:
    """AI Profile + Secret + 运行时配置的统一入口。"""

    def __init__(
        self,
        db_path: str | None = None,
        conn: sqlite3.Connection | None = None,
        secret_store: SecretStore | None = None,
        env: dict | None = None,
    ):
        if db_path is None and conn is None:
            raise ValueError("AIConfigService 需要 db_path 或 conn")
        self.db_path = str(db_path) if db_path is not None else None
        # conn 仅用于测试 / 单线程；生产传 db_path（每次调用独立连接）
        self._conn = conn
        self._secrets = secret_store or SecretStore()
        self._env = env if env is not None else os.environ

    # ================= 内部连接 =================

    @contextmanager
    def _repo(self) -> Iterator[AIProfileRepository]:
        if self._conn is not None:
            yield AIProfileRepository(self._conn)
            return
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield AIProfileRepository(c)
        finally:
            c.close()

    @property
    def secrets(self) -> SecretStore:
        return self._secrets

    # ================= Profile 查询 =================

    def list_profiles(self) -> list[AIProfile]:
        with self._repo() as repo:
            return repo.list_all()

    def get_profile(self, profile_id: int) -> Optional[AIProfile]:
        with self._repo() as repo:
            return repo.get(profile_id)

    def get_active_profile(self) -> Optional[AIProfile]:
        with self._repo() as repo:
            return repo.get_active()

    def profile_count(self) -> int:
        with self._repo() as repo:
            return repo.count()

    def has_api_key(self, profile_id: int) -> bool:
        profile = self.get_profile(profile_id)
        if profile is None:
            return False
        value = self._secrets.get(profile.secret_ref)
        return bool(value)

    # ================= Profile 写入 =================

    def create_profile(
        self,
        display_name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        provider_type: str = PROVIDER_TYPE_OPENAI_COMPATIBLE,
        make_active: bool = False,
    ) -> AIProfile:
        """创建 Profile；api_key 非空时写入 keyring（不写 DB）。

        第一个 Profile 自动设为当前；后续创建默认不切换当前
        （用户可在 UI 中显式【设为当前】）。
        """
        with self._repo() as repo:
            make_active = make_active or repo.count() == 0
            profile = repo.create(
                display_name=display_name,
                base_url=base_url,
                model=model,
                provider_type=provider_type,
                is_active=False,
            )
            secret_ref = make_secret_ref(profile.id)
            profile = repo.update(profile.id, secret_ref=secret_ref)
            if make_active:
                repo.set_active(profile.id)
                profile = repo.get(profile.id) or profile
        if api_key is not None and api_key != "":
            try:
                self._secrets.set(profile.secret_ref, api_key)
            except SecretStoreError:
                # 写 Key 失败：回滚已创建的 Profile，避免“有配置但没 Key”的假象
                with self._repo() as repo:
                    repo.delete(profile.id)
                raise
        return profile

    def update_profile(
        self,
        profile_id: int,
        *,
        display_name: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider_type: str | None = None,
    ) -> AIProfile:
        with self._repo() as repo:
            return repo.update(
                profile_id,
                display_name=display_name,
                base_url=base_url,
                model=model,
                provider_type=provider_type,
            )

    def rename_profile(self, profile_id: int, display_name: str) -> AIProfile:
        """只改名称，绝不触碰 Key。"""
        return self.update_profile(profile_id, display_name=display_name)

    def set_api_key(self, profile_id: int, api_key: str) -> None:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"AI 配置不存在: id={profile_id}")
        self._secrets.set(profile.secret_ref, api_key)

    def clear_api_key(self, profile_id: int) -> None:
        profile = self.get_profile(profile_id)
        if profile is None:
            return
        self._secrets.delete(profile.secret_ref)

    def set_active(self, profile_id: int) -> AIProfile:
        with self._repo() as repo:
            repo.set_active(profile_id)
            profile = repo.get(profile_id)
        assert profile is not None
        return profile

    def delete_profile(self, profile_id: int, reassign_to: int | None = None) -> None:
        """删除 Profile 数据 + keyring secret。

        删除当前 active 配置时：
        - 传 ``reassign_to`` → 先把指定 Profile 设为当前，再删除；
        - 不传 → 删除后允许“当前无选中配置”（由 UI 明确提示，不静默切换）。
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            return
        if profile.is_active and reassign_to is not None:
            if reassign_to == profile_id:
                raise ValueError("不能把被删除的配置指定为新的当前配置")
            self.set_active(reassign_to)
        # 先删 DB，再尽力删 secret（即使 secret 删除失败也不阻断）
        with self._repo() as repo:
            repo.delete(profile_id)
        self._secrets.delete(profile.secret_ref)

    # ================= Legacy 环境变量兼容 =================

    def legacy_env_config(self) -> RuntimeAIConfig:
        """读取当前环境变量配置（源=env）。"""
        api_key = (self._env.get(ENV_API_KEY, "") or "").strip()
        base_url = (
            self._env.get(ENV_BASE_URL, "") or DEFAULT_BASE_URL
        ).strip().rstrip("/")
        model = (self._env.get(ENV_MODEL, "") or "").strip()
        cfg = RuntimeAIConfig(
            base_url=base_url,
            model=model,
            api_key=api_key,
            source=SOURCE_ENV,
            profile_name="环境变量配置",
        )
        if not cfg.is_configured:
            cfg.error_message = (
                f"当前使用环境变量配置，但 {ENV_API_KEY} / {ENV_MODEL} 未设置完整。"
            )
        return cfg

    def is_legacy_configured(self) -> bool:
        return self.legacy_env_config().is_configured

    def import_legacy_profile(
        self, display_name: str = "Legacy / 当前配置"
    ) -> AIProfile:
        """把当前环境变量配置保存为一个 Profile（Key 写入 keyring）。

        绝不打印 Key；写 keyring 失败则回滚 Profile 并抛 SecretStoreError。
        """
        cfg = self.legacy_env_config()
        if not cfg.api_key:
            raise ValueError(
                f"当前环境变量中没有 {ENV_API_KEY}，无法保存为配置。"
            )
        return self.create_profile(
            display_name=display_name,
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            make_active=True,
        )

    # ================= 运行时配置（每次请求解析） =================

    def resolve_profile_config(self, profile_id: int) -> RuntimeAIConfig:
        """解析指定 Profile 的运行时配置（Key 从 keyring 读取）。"""
        profile = self.get_profile(profile_id)
        if profile is None:
            return RuntimeAIConfig(
                source=SOURCE_NONE,
                error_message=f"AI 配置不存在: id={profile_id}",
            )
        api_key = self._secrets.get(profile.secret_ref) or ""
        cfg = RuntimeAIConfig(
            base_url=(profile.base_url or "").strip().rstrip("/"),
            model=(profile.model or "").strip(),
            api_key=api_key,
            provider_type=profile.provider_type,
            source=SOURCE_PROFILE,
            profile_id=profile.id,
            profile_name=profile.display_name,
        )
        if not api_key:
            cfg.error_message = (
                f"配置「{profile.display_name}」缺少 API Key（系统凭据存储中不存在）。"
            )
        elif not cfg.model:
            cfg.error_message = f"配置「{profile.display_name}」缺少 Model。"
        elif not cfg.base_url:
            cfg.error_message = f"配置「{profile.display_name}」缺少 Base URL。"
        return cfg

    def get_runtime_config(self) -> RuntimeAIConfig:
        """返回当前实际使用的 AI 配置（线程安全，每次请求调用）。

        规则：
        1. 存在 active Profile → 使用该 Profile（Key 从 keyring 读取）；
        2. 数据库没有任何 Profile → 回退 legacy 环境变量；
        3. 有 Profile 但都未选中 → 明确报“未选择当前配置”，不静默用其它来源。
        """
        with self._repo() as repo:
            total = repo.count()
            active = repo.get_active()
        if active is not None:
            return self.resolve_profile_config(active.id)
        if total == 0:
            return self.legacy_env_config()
        return RuntimeAIConfig(
            source=SOURCE_NONE,
            error_message="未选择当前 AI 配置，请在「AI 设置 → 模型 / API」中设为当前。",
        )

    # ================= 测试连接 =================

    def test_connection(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        profile_id: int | None = None,
        timeout: float = TEST_TIMEOUT,
        urlopen=None,
    ) -> ConnectionTestResult:
        """发送极小请求验证连通性（只返回状态，绝不回显 Key）。"""
        key = api_key
        if key is None and profile_id is not None:
            profile = self.get_profile(profile_id)
            key = self._secrets.get(profile.secret_ref) if profile else None
        return run_connection_test(
            base_url=base_url,
            model=model,
            api_key=key or "",
            timeout=timeout,
            urlopen=urlopen,
        )
