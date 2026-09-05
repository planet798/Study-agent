"""client.py 测试：使用假 urlopen，不依赖真实 DeepSeek API。

重点覆盖：正常返回 / HTTP 错误 / 网络错误 / timeout /
非法 JSON / envelope 结构错误 / API Key 缺失。
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from app.ai.client import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    DeepSeekClient,
)
from app.ai.interface import AIServiceError


class FakeResponse:
    """模拟 urllib 的 response 对象（支持上下文管理器）。"""

    def __init__(self, body: bytes | str):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    fp = io.BytesIO(body.encode("utf-8"))
    return urllib.error.HTTPError(
        url="https://api.deepseek.com/chat/completions",
        code=code,
        msg="error",
        hdrs={},
        fp=fp,
    )


def _fake_urlopen(body=None, exc=None):
    """构造一个假 urlopen：返回 body 或抛出 exc。"""

    def fn(_request, timeout=None):  # noqa: ARG001
        if exc is not None:
            raise exc
        return FakeResponse(body)

    return fn


def _make_valid_response(content: str) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})


def _configured_env() -> dict:
    return {
        ENV_API_KEY: "test-key-123",
        ENV_BASE_URL: "https://api.deepseek.com",
        ENV_MODEL: "deepseek-chat",
    }


class TestConfiguration:
    def test_missing_api_key_not_configured(self):
        client = DeepSeekClient(env={ENV_MODEL: "deepseek-chat"})
        assert client.is_configured() is False

    def test_missing_model_not_configured(self):
        client = DeepSeekClient(env={ENV_API_KEY: "k"})
        assert client.is_configured() is False

    def test_fully_configured(self):
        client = DeepSeekClient(env=_configured_env())
        assert client.is_configured() is True

    def test_base_url_default_when_unset(self):
        client = DeepSeekClient(env={ENV_API_KEY: "k", ENV_MODEL: "m"})
        assert client.endpoint == "https://api.deepseek.com/chat/completions"

    def test_custom_base_url(self):
        env = {ENV_API_KEY: "k", ENV_MODEL: "m", ENV_BASE_URL: "https://x.example.com/v1"}
        client = DeepSeekClient(env=env)
        assert client.endpoint == "https://x.example.com/v1/chat/completions"

    def test_read_environment(self):
        client = DeepSeekClient(env=_configured_env())
        assert client.api_key == "test-key-123"
        assert client.model == "deepseek-chat"


class TestChatSuccess:
    def test_valid_response(self):
        content = json.dumps(
            {"reasonable": True, "score": 0.9, "should_postpone": True,
             "suggested_date": "2026-09-06", "analysis": "a", "suggestion": "b"}
        )
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(body=_make_valid_response(content)),
        )
        out = client.chat("sys", "user")
        assert json.loads(out)["should_postpone"] is True

    def test_content_trimmed(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(body=_make_valid_response('  {"a":1}  ')),
        )
        assert client.chat("sys", "user") == '{"a":1}'

    def test_model_and_key_sent(self):
        captured = {}

        def fn(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            body = json.loads(request.data.decode("utf-8"))
            captured["model"] = body["model"]
            return FakeResponse(_make_valid_response('{"a":1}'))

        client = DeepSeekClient(env=_configured_env(), urlopen=fn)
        client.chat("sys", "user")
        assert captured["url"] == "https://api.deepseek.com/chat/completions"
        assert captured["auth"] == "Bearer test-key-123"
        assert captured["model"] == "deepseek-chat"

    def test_json_mode_default_sends_response_format(self):
        # 默认 json_mode=True：发送 response_format=json_object
        captured = {}

        def fn(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(_make_valid_response('{"a":1}'))

        client = DeepSeekClient(env=_configured_env(), urlopen=fn)
        client.chat("sys", "user")
        assert captured["body"]["response_format"] == {"type": "json_object"}

    def test_json_mode_false_omits_response_format(self):
        # json_mode=False 时不应发送 response_format
        captured = {}

        def fn(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(_make_valid_response('{"a":1}'))

        client = DeepSeekClient(env=_configured_env(), urlopen=fn)
        client.chat("sys", "user", json_mode=False)
        assert "response_format" not in captured["body"]


class TestChatFailures:
    def test_api_key_missing(self):
        client = DeepSeekClient(env={ENV_MODEL: "m"})
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "AI 未配置" in str(exc.value)

    def test_http_error(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(exc=_make_http_error(401, "unauthorized")),
        )
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "HTTP" in str(exc.value)

    def test_network_error(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(exc=urllib.error.URLError("connection refused")),
        )
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "网络" in str(exc.value)

    def test_timeout(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(exc=socket.timeout("timed out")),
        )
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "超时" in str(exc.value)

    def test_invalid_json_body(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(body="<html>oops</html>"),
        )
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "JSON" in str(exc.value)

    def test_envelope_missing_choices(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(body=json.dumps({"error": "nope"})),
        )
        with pytest.raises(AIServiceError) as exc:
            client.chat("sys", "user")
        assert "choices" in str(exc.value)

    def test_empty_content(self):
        client = DeepSeekClient(
            env=_configured_env(),
            urlopen=_fake_urlopen(body=_make_valid_response("  ")),
        )
        with pytest.raises(AIServiceError):
            client.chat("sys", "user")
