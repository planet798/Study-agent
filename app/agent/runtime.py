"""AgentRuntime（Agent-1，no-tool）。

    Task → Session → Messages → Model

职责：
- 把会话历史（按 message id 顺序）构造成 multi-turn :class:`ModelRequest`；
- 调用 :class:`AgentModelClient`；
- 持久化 assistant 回复。

边界（Agent-1 明确不做）：
- 不直接访问 SQLite / AgentRepository（一切持久化经 AgentSessionService）；
- 不发送 tools、不执行 tool_calls；
- 不写 Mastery / Capability / Evidence，也不完成 Task。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ai.agent_protocol import (
    AgentModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from .session import AgentSessionService

AGENT_SYSTEM_PROMPT = (
    "你是 Study-Agent 的学习会话助手。\n"
    "你的目标是围绕当前学习任务帮助用户理解、练习和推进学习。\n"
    "\n"
    "你当前没有执行工具或修改应用状态的能力。"
    "不要声称已经完成任务、修改 Mastery、修改 Capability、"
    "创建 Evidence 或操作项目。"
)


class AgentRuntimeError(Exception):
    """Agent runtime 领域错误（如模型在无工具会话中返回 tool_calls）。"""


@dataclass(frozen=True)
class AgentTurnResult:
    """一次完整 turn 的结果（不含任何 GUI 类型）。"""

    session_id: int
    user_message: dict
    assistant_message: dict
    model_response: ModelResponse


class AgentRuntime:
    """Agent-1 核心运行时：multi-turn 会话 + 模型调用 + 消息持久化。"""

    def __init__(
        self,
        session_service: AgentSessionService,
        model_client: AgentModelClient,
        system_prompt: str = AGENT_SYSTEM_PROMPT,
    ):
        self.session_service = session_service
        self.model_client = model_client
        self.system_prompt = system_prompt

    # ---------- prompt ----------

    def build_system_message(self, session: dict) -> ModelMessage:
        """基础 system prompt + 当前学习任务标题（Agent-3 再做完整 TaskContext）。"""
        title = (session.get("title") or "").strip()
        content = self.system_prompt
        if title:
            content = f"{content}\n\n当前学习任务：{title}"
        return ModelMessage(role="system", content=content)

    def build_request(self, session: dict) -> ModelRequest:
        """按 message id 顺序构造完整 multi-turn 请求（含 system）。

        Agent-1 完整加载 session history；不做 token counting / compaction
        （属于后续 Agent Memory 阶段）。TODO(agent-memory): context compaction.
        """
        history = self.session_service.messages(int(session["id"]))
        messages = [self.build_system_message(session)]
        messages.extend(self._to_model_message(row) for row in history)
        return ModelRequest(messages=tuple(messages))

    # ---------- turn ----------

    def send_message(self, session_id: int, user_text: str) -> AgentTurnResult:
        """执行一个完整 turn。

        顺序：校验 session → 持久化 user message → 重载历史 → 构造请求 →
        model.complete() → 持久化 assistant message → 返回结果。

        模型调用失败时 user message 保留、assistant message 不写，
        并向上抛 :class:`AIServiceError`（不无限重试）。
        """
        session = self.session_service.get(int(session_id))
        user_message = self.session_service.append_user_message(
            int(session_id), user_text
        )

        request = self.build_request(session)
        response = self.model_client.complete(request)

        if response.tool_calls:
            # Agent-1 不执行工具、不伪造 tool result：fail safe。
            # user message 已持久化，assistant message 不写。
            raise AgentRuntimeError(
                "Agent-1 会话不支持工具调用；模型返回了 tool_calls。"
                "请勿执行或伪造工具结果。"
            )

        assistant_message = self.session_service.append_assistant_message(
            int(session_id),
            response.content,
            metadata=self._build_metadata(response),
        )
        return AgentTurnResult(
            session_id=int(session_id),
            user_message=user_message,
            assistant_message=assistant_message,
            model_response=response,
        )

    # ---------- helpers ----------

    @staticmethod
    def _to_model_message(row: dict) -> ModelMessage:
        return ModelMessage(role=str(row.get("role") or ""),
                            content=str(row.get("content") or ""))

    @staticmethod
    def _build_metadata(response: ModelResponse) -> dict:
        """只记录安全元信息：绝不保存 API key / Authorization / RuntimeAIConfig。"""
        metadata: dict[str, Any] = {
            "model": response.model,
            "finish_reason": response.finish_reason,
            "usage": response.usage or {},
        }
        return metadata
