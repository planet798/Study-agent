"""Persistence for session-scoped derived Agent Memory.

Unlike ``agent_messages``, a memory row is a rolling cache and may be updated.
It is deliberately excluded from immutable conversation-history fingerprints.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


class AgentMemoryRepository:
    """Read and atomically advance a Session's derived summary prefix."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_for_session(self, session_id: int) -> dict | None:
        sid = int(session_id)
        row = self.conn.execute(
            "SELECT * FROM agent_session_memory WHERE session_id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            return None
        owner = self.conn.execute(
            "SELECT session_id FROM agent_messages WHERE id = ?",
            (int(row["through_message_id"]),),
        ).fetchone()
        if owner is None or int(owner[0]) != sid:
            raise ValueError("stored memory boundary does not belong to session_id")
        return dict(row)

    def upsert(
        self,
        session_id: int,
        through_message_id: int,
        source_message_count: int,
        summary: str,
        format_version: int = 1,
    ) -> dict:
        """Create or advance memory after enforcing Session/prefix ownership.

        ``created_at`` is stable after initial creation. A rolling update may only
        move the represented prefix forward; original message rows are untouched.
        """
        sid = self._positive_int(session_id, "session_id")
        through_id = self._positive_int(through_message_id, "through_message_id")
        count = self._positive_int(source_message_count, "source_message_count")
        version = self._positive_int(format_version, "format_version")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be non-empty text")

        session_exists = self.conn.execute(
            "SELECT 1 FROM agent_sessions WHERE id = ?", (sid,)
        ).fetchone()
        if session_exists is None:
            raise ValueError("session does not exist")
        message = self.conn.execute(
            "SELECT session_id FROM agent_messages WHERE id = ?",
            (through_id,),
        ).fetchone()
        if message is None or int(message[0]) != sid:
            raise ValueError("through_message_id must belong to session_id")
        existing = self.get_for_session(sid)
        now = self._next_timestamp(existing.get("updated_at") if existing else None)
        if existing is None:
            self.conn.execute(
                "INSERT INTO agent_session_memory "
                "(session_id, through_message_id, source_message_count, summary, "
                "format_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, through_id, count, summary.strip(), version, now, now),
            )
        else:
            if through_id < int(existing["through_message_id"]):
                raise ValueError("through_message_id cannot move backwards")
            if count < int(existing["source_message_count"]):
                raise ValueError("source_message_count cannot move backwards")
            cursor = self.conn.execute(
                "UPDATE agent_session_memory SET through_message_id = ?, "
                "source_message_count = ?, summary = ?, format_version = ?, "
                "updated_at = ? WHERE session_id = ? "
                "AND through_message_id <= ? AND source_message_count <= ?",
                (through_id, count, summary.strip(), version, now, sid, through_id, count),
            )
            if cursor.rowcount != 1:
                raise ValueError("memory prefix cannot move backwards")
        self.conn.commit()
        return self.get_for_session(sid)

    @staticmethod
    def _positive_int(value, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a positive integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{field} must be a positive integer") from None
        if parsed <= 0 or str(value).strip() != str(parsed):
            raise ValueError(f"{field} must be a positive integer")
        return parsed

    @staticmethod
    def _next_timestamp(previous: str | None) -> str:
        now = datetime.now()
        if previous:
            try:
                old = datetime.fromisoformat(previous)
                if now <= old:
                    now = old + timedelta(microseconds=1)
            except ValueError:
                pass
        return now.isoformat(timespec="microseconds")
