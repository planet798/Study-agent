"""OpenAI-compatible Agent 模型客户端（Agent-1）。

- :class:`AdaptiveAgentModelClient` 每次请求向 ``AIConfigService`` 解析“当前
  Profile”，与 :class:`app.ai.client.AdaptiveAIClient` 共用同一套 API 设置
  （API Key / Base URL / Model / active profile），**不新建第二套配置**。
- 与 ``AIClient.chat()`` 的关键区别：Agent 会话是自然语言，因此
  **不**默认 ``response_format=json_object``；接受完整 message 数组。
- 不泄漏 Key：复用 :func:`app.ai.client.sanitize_text`，所有失败统一
  :class:`AIServiceError`。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .agent_protocol import (
    AgentModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from .client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, sanitize_text
from .interface import AIServiceError


def build_agent_payload(model: str, request: ModelRequest) -> dict[str, Any]:
    """构造 Agent chat payload。

    - ``messages`` 为完整历史（system / user / assistant ...）；
    - ``tools`` 仅在非空时出现（OpenAI-compatible 原样发送）；
    - 绝不注入 ``response_format``。
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [m.to_payload() for m in request.messages],
        "temperature": request.temperature,
    }
    if request.max_tokens is not None:
        payload["max_tokens"] = int(request.max_tokens)
    if request.tools:
        payload["tools"] = list(request.tools)
    return payload


def _parse_tool_calls(raw: Any, api_key: Optional[str] = None) -> tuple[ModelToolCall, ...]:
    """解析 ``message.tool_calls``；``arguments`` 保留 raw JSON string。"""
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise AIServiceError("AI 返回 tool_calls 结构非法")
    calls: list[ModelToolCall] = []
    for item in raw:
        try:
            function = item["function"]
            calls.append(
                ModelToolCall(
                    id=str(item.get("id", "")),
                    name=str(function.get("name", "")),
                    arguments=str(function.get("arguments", "") or ""),
                )
            )
        except (KeyError, TypeError, AttributeError) as e:
            raise AIServiceError(
                f"AI 返回 tool_calls 结构不完整: {e}"
            ) from e
    return tuple(calls)


def parse_agent_response(body: str, api_key: Optional[str] = None) -> ModelResponse:
    """解析 OpenAI-compatible 响应信封为 :class:`ModelResponse`。"""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise AIServiceError(
            sanitize_text(f"AI 返回非法 JSON: {e}", api_key)
        ) from e

    try:
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) == 0:
            raise AIServiceError("AI 返回中缺少 choices")
        choice = choices[0]
        message = choice["message"]
    except AIServiceError:
        raise
    except (KeyError, IndexError, TypeError) as e:
        raise AIServiceError(f"AI 返回结构不完整: {e}") from e

    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise AIServiceError("AI 返回 content 类型非法")

    tool_calls = _parse_tool_calls(message.get("tool_calls"), api_key)
    if not content.strip() and not tool_calls:
        raise AIServiceError("AI 返回内容为空")

    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = None

    return ModelResponse(
        content=content.strip(),
        tool_calls=tool_calls,
        finish_reason=str(choice.get("finish_reason") or ""),
        model=str(data.get("model") or ""),
        usage=usage,
    )


def send_agent_request(
    *,
    api_key: str,
    base_url: str,
    model: str,
    request: ModelRequest,
    timeout: float = DEFAULT_TIMEOUT,
    urlopen: Callable[..., Any] | None = None,
) -> ModelResponse:
    """发送一条 OpenAI-compatible Agent chat 请求，返回 :class:`ModelResponse`。"""
    opened = urlopen or urllib.request.urlopen
    endpoint = f"{(base_url or DEFAULT_BASE_URL).strip().rstrip('/')}/chat/completions"
    payload = build_agent_payload(model, request)
    http_request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with opened(http_request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise AIServiceError(
            sanitize_text(f"AI HTTP 错误 {e.code}: {detail}", api_key)
        ) from e
    except urllib.error.URLError as e:
        raise AIServiceError(
            sanitize_text(f"AI 网络错误: {e.reason}", api_key)
        ) from e
    except socket.timeout as e:
        raise AIServiceError("AI 请求超时") from e
    except TimeoutError as e:
        raise AIServiceError("AI 请求超时") from e
    except OSError as e:
        raise AIServiceError(sanitize_text(f"AI 网络错误: {e}", api_key)) from e

    return parse_agent_response(body, api_key)


class AdaptiveAgentModelClient(AgentModelClient):
    """每次请求都解析“当前 AI 配置”的 Agent 模型客户端。

    :param config_provider: 无参可调用对象，返回带 ``api_key`` / ``base_url`` /
        ``model`` / ``is_configured`` 的运行时配置（通常是
        :meth:`AIConfigService.get_runtime_config`）。
    :param urlopen: 可注入底层 HTTP 函数（测试用）。
    """

    def __init__(
        self,
        config_provider: Callable[[], Any],
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] | None = None,
    ):
        self._config_provider = config_provider
        self.timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen

    def current_config(self) -> Any:
        return self._config_provider()

    def is_configured(self) -> bool:
        try:
            cfg = self.current_config()
        except Exception:  # noqa: BLE001 - 配置解析失败视为未配置
            return False
        return bool(getattr(cfg, "is_configured", False))

    def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            cfg = self.current_config()
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 配置读取失败: {e}") from e

        if not getattr(cfg, "is_configured", False):
            raise AIServiceError(
                getattr(cfg, "error_message", "")
                or "AI 未配置：请在「AI 设置 → 模型 / API」中添加并启用配置"
            )
        return send_agent_request(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            request=request,
            timeout=getattr(self, "timeout", DEFAULT_TIMEOUT),
            urlopen=self._urlopen,
        )


__all__ = [
    "AdaptiveAgentModelClient",
    "ModelMessage",
    "ModelRequest",
    "ModelResponse",
    "build_agent_payload",
    "parse_agent_response",
    "send_agent_request",
]
