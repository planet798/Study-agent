"""Agent model protocol（provider-independent，Agent-1）。

与 legacy :class:`app.ai.interface.AIClient` 明确区分：

- ``AIClient.chat(system_prompt, user_prompt)`` 服务 Planner / Assessment /
  JD parse / TaskReview / Route Builder，**保持不变**；
- ``AgentModelClient.complete(ModelRequest)`` 服务 Agent multi-turn 会话，
  接收完整 message 数组，并可表达 OpenAI-compatible tool calls。

Agent-1 只建立结构、不执行工具：Runtime 不发送 tools、不执行 tool_calls。
tool 字段现在保留，是为了 Agent-2 建设 Tool Registry 时无需推翻本 API。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelToolCall:
    """模型请求调用某个工具。``arguments`` 保留 raw JSON string。"""

    id: str
    name: str
    arguments: str = ""


@dataclass(frozen=True)
class ModelMessage:
    """一条 provider-independent 的会话消息。

    ``tool_calls`` / ``tool_call_id`` / ``name`` 仅为表达 OpenAI-compatible
    tool 协议；Agent-1 生产路径只写 ``user`` / ``assistant`` 文本消息。
    """

    role: str
    content: str = ""
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()

    def to_payload(self) -> dict:
        """转成 OpenAI-compatible message dict（省略空的可选字段）。"""
        payload: dict = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return payload


@dataclass(frozen=True)
class ModelRequest:
    """一次模型调用请求（完整消息历史 + 可选工具声明）。"""

    messages: tuple[ModelMessage, ...]
    tools: tuple[dict, ...] = ()
    temperature: float = 0.3
    max_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    """模型返回：自然语言内容 + 可选 tool calls + 元信息。"""

    content: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish_reason: str = ""
    model: str = ""
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)


class AgentModelClient(ABC):
    """Agent multi-turn 模型客户端抽象接口（与 ``AIClient`` 独立）。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """是否具备调用所需全部配置（API Key / Base URL / Model）。"""

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        """发送完整消息历史，返回 :class:`ModelResponse`。

        实现方需自行处理 network / http / timeout / json / envelope 错误，
        并把所有失败统一转成 :class:`AIServiceError`。
        """
