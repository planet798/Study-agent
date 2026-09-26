"""Agent Session / Message 数据访问层（Agent-1）。

只做 persistence：不调 AI、不决定 prompt、不操作 Mastery / Capability。
所有 agent_sessions / agent_messages SQL 集中在此，Service / Runtime 不写 SQL。

语义：
- 一个 Task 同时最多一个 ``status='active'`` session（partial unique index 保证）；
- message 创建后不可编辑 / 不可删除（历史交互可审计）。
"""

from __future__ import annotations

import sqlite3

from ..utils.date_utils import now_iso

STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"

VALID_ROLES = ("system", "user", "assistant", "tool")


class AgentRepository:
    """agent_sessions / agent_messages 的 SQL 封装。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- session ----------

    @staticmethod
    def _session_from_row(row) -> dict | None:
        return dict(row) if row is not None else None

    def get_session(self, session_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (int(session_id),)
        ).fetchone()
        return self._session_from_row(row)

    def get_active_for_task(self, task_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_sessions WHERE task_id = ? AND status = ?"
            " ORDER BY id DESC LIMIT 1",
            (int(task_id), STATUS_ACTIVE),
        ).fetchone()
        return self._session_from_row(row)

    def list_sessions_for_task(self, task_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM agent_sessions WHERE task_id = ? ORDER BY id",
            (int(task_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_session(self, task_id: int, title: str = "") -> dict:
        """创建 active session。同一 Task 已有 active session 时抛 sqlite3.IntegrityError。"""
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO agent_sessions (task_id, title, status, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?)",
            (int(task_id), title or "", STATUS_ACTIVE, ts, ts),
        )
        self.conn.commit()
        return self.get_session(int(cur.lastrowid))

    def touch_session(self, session_id: int) -> None:
        """更新 updated_at（消息追加后调用）。"""
        self.conn.execute(
            "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
            (now_iso(), int(session_id)),
        )
        self.conn.commit()

    def close_session(self, session_id: int) -> dict | None:
        """status='closed' + closed_at/updated_at。已完成 session 保持原值（幂等）。"""
        ts = now_iso()
        self.conn.execute(
            "UPDATE agent_sessions SET status = ?, closed_at = COALESCE(closed_at, ?),"
            " updated_at = ? WHERE id = ?",
            (STATUS_CLOSED, ts, ts, int(session_id)),
        )
        self.conn.commit()
        return self.get_session(session_id)

    # ---------- message（immutable：无 update / delete） ----------

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str = "",
        tool_call_id: str = "",
        tool_name: str = "",
        tool_calls_json: str = "",
        metadata_json: str = "{}",
    ) -> dict:
        if role not in VALID_ROLES:
            raise ValueError(f"非法 agent message role: {role!r}")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, tool_call_id,"
            " tool_name, tool_calls_json, metadata_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(session_id),
                role,
                content or "",
                tool_call_id or "",
                tool_name or "",
                tool_calls_json or "",
                metadata_json or "{}",
                ts,
            ),
        )
        self.conn.commit()
        return self.get_message(int(cur.lastrowid))

    def get_message(self, message_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_messages WHERE id = ?", (int(message_id),)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_messages(self, session_id: int) -> list[dict]:
        """按 message id 升序返回会话全部消息（多轮顺序稳定）。"""
        rows = self.conn.execute(
            "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY id",
            (int(session_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_messages(self, session_id: int) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM agent_messages WHERE session_id = ?",
                (int(session_id),),
            ).fetchone()[0]
        )
