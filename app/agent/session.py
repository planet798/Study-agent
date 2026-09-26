"""AgentSessionService（Agent-1）。

Task-bound 学习会话的领域服务：

    Task → Agent Study Session

- 依赖 :class:`AgentRepository`（persistence）与 ``TaskService``（Task 存在性校验）；
- 不直接查询 tasks raw SQL，也不依赖 TaskRepository；
- 不操作 Mastery / Capability，也不完成 Task。

关闭 session ≠ 完成学习任务：Task 完成仍走既有 ``TaskService``。
"""

from __future__ import annotations

from typing import Any

from ..database.agent_repository import AgentRepository
from ..database.repository import Task

BLANK_MESSAGE_ERROR = "消息内容不能为空"


class AgentSessionError(Exception):
    """Agent session 领域错误基类。"""


class SessionNotFoundError(AgentSessionError):
    """session 不存在。"""


class SessionClosedError(AgentSessionError):
    """session 已关闭，不能继续发送消息。"""


def _is_blank(text: str | None) -> bool:
    return not (text or "").strip()


class AgentSessionService:
    """Agent Study Session 的创建 / 恢复 / 关闭与消息持久化。"""

    def __init__(self, agent_repo: AgentRepository, task_service: Any):
        self.repo = agent_repo
        # 领域访问统一走 Service（domain access → Service）
        self.task_service = task_service

    # ---------- 查询 ----------

    def get(self, session_id: int) -> dict:
        session = self.repo.get_session(int(session_id))
        if session is None:
            raise SessionNotFoundError(f"Agent session 不存在: id={session_id}")
        return session

    def get_active_for_task(self, task_id: int) -> dict | None:
        return self.repo.get_active_for_task(int(task_id))

    def list_sessions_for_task(self, task_id: int) -> list[dict]:
        return self.repo.list_sessions_for_task(int(task_id))

    def messages(self, session_id: int) -> list[dict]:
        self.get(session_id)  # 校验存在
        return self.repo.list_messages(int(session_id))

    def count_messages(self, session_id: int) -> int:
        return self.repo.count_messages(int(session_id))

    # ---------- 创建 / 恢复 ----------

    def start_or_resume(self, task_id: int) -> dict:
        """返回该 Task 的 active session；没有则创建（title 默认 task.title）。

        重复调用不会重复创建 session。
        """
        task: Task = self.task_service.get_task(int(task_id))
        existing = self.repo.get_active_for_task(task.id)
        if existing is not None:
            return existing
        try:
            return self.repo.create_session(task.id, title=task.title or "")
        except Exception:
            # 并发下可能已被其他调用创建：回退到已有 active session。
            fallback = self.repo.get_active_for_task(task.id)
            if fallback is not None:
                return fallback
            raise

    # ---------- 消息 ----------

    def append_user_message(self, session_id: int, content: str) -> dict:
        self._require_open(session_id)
        if _is_blank(content):
            raise AgentSessionError(BLANK_MESSAGE_ERROR)
        message = self.repo.add_message(int(session_id), "user", content.strip())
        self.repo.touch_session(int(session_id))
        return message

    def append_assistant_message(
        self,
        session_id: int,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        import json

        self._require_open(session_id)
        if _is_blank(content):
            raise AgentSessionError(BLANK_MESSAGE_ERROR)
        message = self.repo.add_message(
            int(session_id),
            "assistant",
            content.strip(),
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        )
        self.repo.touch_session(int(session_id))
        return message

    # ---------- 关闭 ----------

    def close(self, session_id: int) -> dict:
        """关闭 session：status='closed'。不完成 Task、不改 Mastery / Capability。"""
        self.get(session_id)
        return self.repo.close_session(int(session_id))

    # ---------- internal ----------

    def _require_open(self, session_id: int) -> dict:
        session = self.get(session_id)
        if session.get("status") != "active":
            raise SessionClosedError(
                f"Agent session 已关闭，不能继续对话: id={session_id}"
            )
        return session
