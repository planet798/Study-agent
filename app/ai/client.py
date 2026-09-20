"""OpenAI-compatible AIClient 实现（使用标准库 urllib，无额外依赖）。

- 两种客户端：
  * :class:`DeepSeekClient` —— 从环境变量读取配置（legacy）；
  * :class:`AdaptiveAIClient` —— 每次请求向 AIConfigService 解析“当前 Profile”，
    切换 API 配置 / 修改 Key / 修改 Model 后无需重启，下一次请求立即生效。
- 绝不把 Key 写进代码 / 日志 / 异常信息；错误信息统一 sanitize。
- 所有失败（未配置 / 网络 / HTTP / timeout / JSON / envelope / 内容）
  统一抛 AIServiceError。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .interface import AIClient, AIServiceError

ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT = 30.0
# 测试连接：只发极小请求，限制 token 与超时
TEST_TIMEOUT = 10.0
TEST_MAX_TOKENS = 5

# 错误信息中可能出现的 Key 占位（绝不泄漏真实 Key）
_MASK = "***"


def sanitize_text(text: str, api_key: Optional[str] = None) -> str:
    """从任意文本中移除 API Key（错误信息 / 日志安全）。

    只替换非空 Key；同时把常见 ``Bearer xxx`` 形式遮蔽。
    """
    if not text:
        return ""
    out = str(text)
    if api_key:
        key = str(api_key).strip()
        if key:
            out = out.replace(key, _MASK)
    # 兜底：遮蔽 Bearer 后面的值（即使没有传入 key）
    import re

    out = re.sub(r"(?i)(bearer\s+)\S+", r"\1" + _MASK, out)
    return out


def _build_payload(
    model: str,
    system_prompt: str,
    user_prompt: str,
    json_mode: bool,
    temperature: float,
    max_tokens: Optional[int],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    # json_object 模式：仅当调用方要求结构化输出时启用。
    # 注意 DeepSeek 要求 prompt 中出现 "json" 字样，否则返回 HTTP 400。
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def send_chat_request(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = True,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
    urlopen: Callable[..., Any] | None = None,
) -> str:
    """发送一条 OpenAI-compatible chat 请求，返回 assistant content 字符串。

    这是 DeepSeekClient / AdaptiveAIClient 共用的底层实现，便于统一错误处理
    与 Key sanitize。
    """
    opened = urlopen or urllib.request.urlopen
    endpoint = f"{(base_url or DEFAULT_BASE_URL).strip().rstrip('/')}/chat/completions"
    payload = _build_payload(
        model, system_prompt, user_prompt, json_mode, temperature, max_tokens
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with opened(request, timeout=timeout) as resp:
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

    return _extract_content(body, api_key)


def _extract_content(body: str, api_key: Optional[str] = None) -> str:
    """解析 OpenAI-compatible 响应信封，返回 assistant 的 content 字符串。"""
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
        content = choices[0]["message"]["content"]
    except AIServiceError:
        raise
    except (KeyError, IndexError, TypeError) as e:
        raise AIServiceError(f"AI 返回结构不完整: {e}") from e

    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("AI 返回内容为空")
    return content.strip()


class DeepSeekClient(AIClient):
    """从环境变量读取配置的客户端（legacy 兼容，保持旧行为）。"""

    def __init__(
        self,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] | None = None,
    ):
        """
        :param env: 环境变量字典，缺省读取 os.environ（便于测试注入）
        :param timeout: 请求超时（秒）
        :param urlopen: 底层 HTTP 函数，默认 urllib.request.urlopen（便于测试替换）
        """
        self._env = env if env is not None else os.environ
        self.timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen

    # ---------- 配置 ----------

    @property
    def api_key(self) -> str:
        return self._env.get(ENV_API_KEY, "").strip()

    @property
    def base_url(self) -> str:
        return (self._env.get(ENV_BASE_URL, "") or DEFAULT_BASE_URL).strip().rstrip("/")

    @property
    def model(self) -> str:
        return self._env.get(ENV_MODEL, "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    @property
    def endpoint(self) -> str:
        """OpenAI-compatible 的 chat/completions 地址。"""
        return f"{self.base_url}/chat/completions"

    # ---------- 调用 ----------

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        **kwargs: Any,
    ) -> str:
        """发送一条对话，返回模型输出的字符串内容。"""
        if not self.is_configured():
            raise AIServiceError(
                f"AI 未配置：请设置环境变量 {ENV_API_KEY} / {ENV_BASE_URL} / {ENV_MODEL}"
            )
        return send_chat_request(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=json_mode,
            temperature=kwargs.get("temperature", 0.3),
            max_tokens=kwargs.get("max_tokens"),
            timeout=kwargs.get("timeout", self.timeout),
            urlopen=self._urlopen,
        )

    def _extract_content(self, body: str) -> str:
        """兼容旧测试/调用：解析响应信封。"""
        return _extract_content(body, self.api_key)


class AdaptiveAIClient(AIClient):
    """每次请求都解析“当前 AI 配置”的客户端。

    :param config_provider: 无参可调用对象，返回带 ``api_key`` / ``base_url`` /
        ``model`` / ``is_configured`` 属性的运行时配置对象（通常是
        :meth:`AIConfigService.get_runtime_config`）。
    :param urlopen: 可注入底层 HTTP 函数（测试用）。

    设计目的：切换 API Profile / 修改 Key / 修改 Model 后**无需重启**，
    下一次请求立即使用新配置（不做不可变 Client 缓存）。
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

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        **kwargs: Any,
    ) -> str:
        try:
            cfg = self.current_config()
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 配置读取失败: {e}") from e

        if not getattr(cfg, "is_configured", False):
            raise AIServiceError(
                getattr(cfg, "error_message", "") or
                "AI 未配置：请在「AI 设置 → 模型 / API」中添加并启用配置"
            )
        return send_chat_request(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=json_mode,
            temperature=kwargs.get("temperature", 0.3),
            max_tokens=kwargs.get("max_tokens"),
            timeout=kwargs.get("timeout", self.timeout),
            urlopen=self._urlopen,
        )
