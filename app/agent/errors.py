"""Finite, content-free error classification for the Agent UI boundary."""
from __future__ import annotations

from dataclasses import dataclass

from ..ai.interface import AIServiceError
from .memory.compactor import AgentContextTooLargeError

SAFE_MESSAGES = {
    "model_not_configured": "AI 模型尚未配置。",
    "model_connection_failed": "模型连接失败，请检查当前 AI 配置和网络后重试。",
    "context_too_large": "当前会话内容过长，本轮无法安全发送给模型。完整历史仍保存在本地。",
    "runtime_failed": "本轮学习暂未完成，请稍后重试。",
}


@dataclass(frozen=True)
class SafeAgentError:
    code: str
    message: str


def map_agent_error(error: BaseException, *, model_configured: bool | None = None,
                    user_message_persisted: bool = False) -> SafeAgentError:
    """Do not inspect or echo untrusted exception text, URLs or credentials."""
    if isinstance(error, AgentContextTooLargeError):
        code = "context_too_large"
    elif model_configured is False:
        code = "model_not_configured"
    elif isinstance(error, AIServiceError):
        code = "model_connection_failed"
    else:
        code = "runtime_failed"
    message = SAFE_MESSAGES[code]
    if user_message_persisted:
        message += "你的消息已保存，但本轮没有生成回答。"
    return SafeAgentError(code, message)
