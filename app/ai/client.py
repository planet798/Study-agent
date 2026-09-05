"""DeepSeek AIClient 实现（OpenAI-compatible，使用标准库 urllib，无额外依赖）。

- 通过环境变量读取配置：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
- 绝不把 Key 写进代码 / 日志
- 所有失败（未配置 / 网络 / HTTP / timeout / JSON / envelope / 内容）统一抛 AIServiceError
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Callable

from .interface import AIClient, AIServiceError

ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT = 30.0


class DeepSeekClient(AIClient):
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
        """发送一条对话，返回模型输出的字符串内容。

        :param json_mode: 是否请求模型输出 JSON 对象（response_format=json_object）。
            默认为 True，保持与 AI Planner / Task Review / Summary 等结构化
            场景兼容；通用文本对话可传 False。
        :param kwargs: 其它可选参数（如 temperature）。
        """
        if not self.is_configured():
            raise AIServiceError(
                f"AI 未配置：请设置环境变量 {ENV_API_KEY} / {ENV_BASE_URL} / {ENV_MODEL}"
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": kwargs.get("temperature", 0.3),
        }
        # json_object 模式：仅当调用方要求结构化输出时启用。
        # 注意 DeepSeek 要求 prompt 中出现 "json" 字样，否则返回 HTTP 400。
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        # 统一捕获网络层错误
        try:
            with self._urlopen(request, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            raise AIServiceError(f"AI HTTP 错误 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise AIServiceError(f"AI 网络错误: {e.reason}") from e
        except socket.timeout as e:
            raise AIServiceError("AI 请求超时") from e
        except TimeoutError as e:
            raise AIServiceError("AI 请求超时") from e
        except OSError as e:
            raise AIServiceError(f"AI 网络错误: {e}") from e

        return self._extract_content(body)

    def _extract_content(self, body: str) -> str:
        """解析 OpenAI-compatible 响应信封，返回 assistant 的 content 字符串。"""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise AIServiceError(f"AI 返回非法 JSON: {e}") from e

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
