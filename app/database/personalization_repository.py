"""Local mutable personalization persistence; no prompts or model calls."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


class PersonalizationRepository:
    """Construct with the caller/worker-owned SQLite connection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _timestamp(previous: str = "") -> str:
        now = datetime.now()
        if previous:
            now = max(now, datetime.fromisoformat(previous) + timedelta(microseconds=1))
        return now.isoformat(timespec="microseconds")

    @staticmethod
    def _dict(cursor) -> dict | None:
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip((column[0] for column in cursor.description), row))

    def get_settings(self) -> dict:
        return self._dict(self.conn.execute(
            "SELECT * FROM agent_personalization_settings WHERE id = 1"
        ))

    def _set_setting(self, column: str, value) -> dict:
        # column is selected only by the three fixed public methods below.
        now = self._timestamp(self.get_settings()["updated_at"])
        self.conn.execute(
            f"UPDATE agent_personalization_settings SET {column} = ?, updated_at = ? WHERE id = 1",
            (value, now),
        )
        self.conn.commit()
        return self.get_settings()

    def set_instructions(self, text: str) -> dict:
        return self._set_setting("instructions", text)

    def set_memory_enabled(self, enabled: bool) -> dict:
        return self._set_setting("memory_enabled", enabled)

    def set_auto_memory_enabled(self, enabled: bool) -> dict:
        return self._set_setting("auto_memory_enabled", enabled)

    def list_memories(self, include_disabled: bool = True) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM agent_personal_memories "
            + ("" if include_disabled else "WHERE enabled = 1 ")
            + "ORDER BY id ASC"
        )
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def get_memory(self, memory_id: int) -> dict | None:
        return self._dict(self.conn.execute(
            "SELECT * FROM agent_personal_memories WHERE id = ?", (memory_id,)
        ))

    def create_memory(self, content: str, source_type: str = "manual",
                      source_session_id: int | None = None,
                      source_message_id: int | None = None) -> dict:
        if source_type == "manual":
            if source_session_id is not None or source_message_id is not None:
                raise ValueError("manual memory cannot have Session provenance")
        elif source_type == "session":
            if source_session_id is None or self.conn.execute(
                "SELECT 1 FROM agent_sessions WHERE id = ?", (source_session_id,)
            ).fetchone() is None:
                raise ValueError("source session does not exist")
            if source_message_id is not None and self.conn.execute(
                "SELECT 1 FROM agent_messages WHERE id = ? AND session_id = ?",
                (source_message_id, source_session_id),
            ).fetchone() is None:
                raise ValueError("source message must exist and belong to source session")
        else:
            raise ValueError("invalid source_type")
        now = self._timestamp()
        cursor = self.conn.execute(
            "INSERT INTO agent_personal_memories "
            "(content, source_type, source_session_id, source_message_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (content, source_type, source_session_id, source_message_id, now, now),
        )
        self.conn.commit()
        return self.get_memory(cursor.lastrowid)

    def _update_memory(self, memory_id: int, column: str, value) -> dict:
        existing = self.get_memory(memory_id)
        if existing is None:
            raise ValueError("personal memory does not exist")
        self.conn.execute(
            f"UPDATE agent_personal_memories SET {column} = ?, updated_at = ? WHERE id = ?",
            (value, self._timestamp(existing["updated_at"]), memory_id),
        )
        self.conn.commit()
        return self.get_memory(memory_id)

    def update_memory(self, memory_id: int, content: str) -> dict:
        return self._update_memory(memory_id, "content", content)

    def set_memory_enabled_state(self, memory_id: int, enabled: bool) -> dict:
        return self._update_memory(memory_id, "enabled", enabled)

    def delete_memory(self, memory_id: int) -> None:
        self.conn.execute("DELETE FROM agent_personal_memories WHERE id = ?", (memory_id,))
        self.conn.commit()
