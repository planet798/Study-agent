"""AgentModelClient 测试（Agent-1）。

重点：
- multi-message request / model 字段 / 无 response_format；
- tools 为空时 payload 不含 tools，非空时原样序列化；
- content / tool_calls / finish_reason / usage 解析；
- envelope 非法 / HTTP / timeout / API Key sanitize；
- 动态 active Profile 生效；
- 旧 AdaptiveAIClient payload 行为不变。
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from app.ai.agent_client import (
    AdaptiveAgentModelClient,
    build_agent_payload,
    send_agent_request,
)
from app.ai.agent_protocol import ModelMessage, ModelRequest, ModelToolCall
from app.ai.client import AdaptiveAIClient
from app.ai.interface import AIServiceError


class FakeResponse:
    def __init__(self, body: bytes | str):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.example.com/chat/completions",
        code=code,
        msg="error",
        hdrs={},
        fp=io.BytesIO(body.encode("utf-8")),
    )


class _Recorder:
    """记录最后一次请求 payload，并返回固定响应。"""

    def __init__(self, body: str):
        self.body = body
        self.payload: dict | None = None

    def __call__(self, request, timeout=None):  # noqa: ARG002
        self.payload = json.loads(request.data.decode("utf-8"))
        return FakeResponse(self.body)


class _Config:
    def __init__(self, *, configured: bool = True, api_key: str = "sk-secret",
                 base_url: str = "https://api.example.com", model: str = "deepseek-chat"):
        self.is_configured = configured
        self.api_key = api_key
        self.base_url = base_url
        self.model = model


def _envelope(**overrides) -> str:
    message = {"role": "assistant", "content": "你好"}
    message.update(overrides.pop("message", {}))
    data = {
        "model": "deepseek-chat",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }
    data.update(overrides)
    return json.dumps(data)


# ---------- payload ----------

def test_payload_sends_full_multi_turn_and_no_response_format():
    request = ModelRequest(messages=(
        ModelMessage(role="system", content="sys"),
        ModelMessage(role="user", content="u1"),
        ModelMessage(role="assistant", content="a1"),
        ModelMessage(role="user", content="u2"),
    ), temperature=0.2, max_tokens=128)
    payload = build_agent_payload("m1", request)
    assert payload["model"] == "m1"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 128
    assert [m["role"] for m in payload["messages"]] == [
        "system", "user", "assistant", "user"
    ]
    assert [m["content"] for m in payload["messages"]] == ["sys", "u1", "a1", "u2"]
    assert "response_format" not in payload


def test_payload_omits_tools_when_empty():
    payload = build_agent_payload("m", ModelRequest(
        messages=(ModelMessage(role="user", content="hi"),)))
    assert "tools" not in payload


def test_payload_serializes_tools_when_present():
    tools = ({"type": "function", "function": {"name": "t", "parameters": {}}},)
    payload = build_agent_payload("m", ModelRequest(
        messages=(ModelMessage(role="user", content="hi"),), tools=tools))
    assert payload["tools"] == list(tools)


def test_message_tool_call_payload_shape():
    message = ModelMessage(
        role="assistant", content="",
        tool_calls=(ModelToolCall(id="c1", name="t", arguments='{"a":1}'),),
    )
    payload = message.to_payload()
    assert payload["tool_calls"] == [{
        "id": "c1", "type": "function",
        "function": {"name": "t", "arguments": '{"a":1}'},
    }]


# ---------- response parsing ----------

def test_send_agent_request_parses_content_finish_reason_usage():
    recorder = _Recorder(_envelope())
    response = send_agent_request(
        api_key="sk-secret", base_url="https://api.example.com",
        model="deepseek-chat",
        request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
        urlopen=recorder,
    )
    assert response.content == "你好"
    assert response.finish_reason == "stop"
    assert response.model == "deepseek-chat"
    assert response.usage == {"prompt_tokens": 5, "completion_tokens": 7}
    assert response.tool_calls == ()
    # 真实发送的是完整 messages + model
    assert recorder.payload["model"] == "deepseek-chat"
    assert recorder.payload["messages"] == [{"role": "user", "content": "hi"}]


def test_send_agent_request_parses_tool_calls_raw_arguments():
    body = _envelope(message={
        "content": "",
        "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "lookup", "arguments": '{"q": "x"}'},
        }],
    }, )
    response = send_agent_request(
        api_key="k", base_url="https://api.example.com", model="m",
        request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
        urlopen=_Recorder(body),
    )
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "call_1" and call.name == "lookup"
    # arguments 保持 raw JSON string，不在 transport 层解析
    assert call.arguments == '{"q": "x"}'


def test_tool_only_response_is_valid():
    body = json.dumps({"choices": [{
        "message": {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "1", "function": {"name": "n", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]})
    response = send_agent_request(
        api_key="k", base_url="https://api.example.com", model="m",
        request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
        urlopen=_Recorder(body),
    )
    assert response.content == ""
    assert response.finish_reason == "tool_calls"


# ---------- errors ----------

def test_invalid_envelope_raises_ai_service_error():
    for body in ("<html>", json.dumps({"error": "nope"}), json.dumps({"choices": []})):
        with pytest.raises(AIServiceError):
            send_agent_request(
                api_key="k", base_url="https://api.example.com", model="m",
                request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
                urlopen=_Recorder(body),
            )


def test_empty_content_raises_ai_service_error():
    with pytest.raises(AIServiceError):
        send_agent_request(
            api_key="k", base_url="https://api.example.com", model="m",
            request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
            urlopen=_Recorder(_envelope(message={"content": "   "})),
        )


def test_http_timeout_and_network_errors_are_ai_service_error():
    def raising(exc):
        def fn(_request, timeout=None):  # noqa: ARG001
            raise exc
        return fn

    cases = [
        _http_error(401, "unauthorized"),
        urllib.error.URLError("connection refused"),
        socket.timeout("timed out"),
        TimeoutError("timed out"),
    ]
    for exc in cases:
        with pytest.raises(AIServiceError):
            send_agent_request(
                api_key="k", base_url="https://api.example.com", model="m",
                request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
                urlopen=raising(exc),
            )


def test_api_key_is_sanitized_in_error_message():
    with pytest.raises(AIServiceError) as info:
        send_agent_request(
            api_key="sk-super-secret", base_url="https://api.example.com", model="m",
            request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
            urlopen=_Recorder("<html>sk-super-secret</html>"),
        )
    assert "sk-super-secret" not in str(info.value)


def test_http_error_body_key_is_sanitized():
    with pytest.raises(AIServiceError) as info:
        send_agent_request(
            api_key="sk-super-secret", base_url="https://api.example.com", model="m",
            request=ModelRequest(messages=(ModelMessage(role="user", content="hi"),)),
            urlopen=lambda *a, **k: (_ for _ in ()).throw(
                _http_error(500, '{"error":"Bearer sk-super-secret"}')),
        )
    text = str(info.value)
    assert "sk-super-secret" not in text
    assert "***" in text


# ---------- Adaptive client ----------

def test_adaptive_agent_client_uses_current_config_and_reflects_profile_change():
    config = {"value": _Config(model="first")}
    recorder = _Recorder(_envelope())
    client = AdaptiveAgentModelClient(lambda: config["value"], urlopen=recorder)
    assert client.is_configured() is True
    client.complete(ModelRequest(messages=(ModelMessage(role="user", content="hi"),)))
    assert recorder.payload["model"] == "first"

    # 切换 active Profile 后，无需重建 client 即生效
    config["value"] = _Config(model="second")
    client.complete(ModelRequest(messages=(ModelMessage(role="user", content="hi"),)))
    assert recorder.payload["model"] == "second"


def test_adaptive_agent_client_unconfigured_raises():
    client = AdaptiveAgentModelClient(lambda: _Config(configured=False), urlopen=_Recorder(_envelope()))
    assert client.is_configured() is False
    with pytest.raises(AIServiceError):
        client.complete(ModelRequest(messages=(ModelMessage(role="user", content="hi"),)))


def test_adaptive_agent_client_config_read_failure_is_not_configured():
    def boom():
        raise RuntimeError("no db")
    client = AdaptiveAgentModelClient(boom, urlopen=_Recorder(_envelope()))
    assert client.is_configured() is False


# ---------- legacy AIClient untouched ----------

def test_legacy_adaptive_ai_client_payload_unchanged():
    recorder = _Recorder(_envelope(message={"content": '{"ok": true}'}))
    client = AdaptiveAIClient(lambda: _Config(), urlopen=recorder)
    client.chat("SYSTEM", "USER", json_mode=True)
    payload = recorder.payload
    assert payload["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload

    client.chat("SYSTEM", "USER", json_mode=False)
    assert "response_format" not in recorder.payload
