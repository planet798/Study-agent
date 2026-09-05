"""AI 客户端抽象接口。

设计目标（面向未来替换）：
- DeepSeek / OpenAI / Gemini / USTC 等 OpenAI-compatible API 都可实现同一接口；
- 上层（TaskReviewService / GUI）只依赖本接口，不依赖任何具体实现；
- 所有 AI 相关失败统一抛 AIServiceError，绝不向上层泄漏底层异常。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIServiceError(Exception):
    """AI 调用/解析相关的统一异常。

    包装：网络错误、HTTP 错误、timeout、JSON 解析失败、结构校验失败等。
    编辑器（GUI）捕获该类后降级为本地功能。
    """


class AIClient(ABC):
    """OpenAI-compatible 聊天客户端抽象接口。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """是否具备调用所需全部配置（API Key / Base URL / Model）。"""

    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        **kwargs: Any,
    ) -> str:
        """发送一条对话，返回模型输出的字符串内容。

        实现方需自行处理 network/http/json/envelope 错误，
        并将所有失败统一转成 AIServiceError。
        """
