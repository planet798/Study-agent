"""Persistence for explicit Task Workspace bindings; no filesystem access."""

from __future__ import annotations

import sqlite3

from ..utils.date_utils import now_iso


class TaskWorkspaceRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_task(self, task_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, task_id, kind, local_path, created_at, updated_at "
            "FROM task_workspaces WHERE task_id = ?", (int(task_id),)
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("id", "task_id", "kind", "local_path", "created_at", "updated_at"), row))

    def upsert(self, task_id: int, kind: str, local_path: str = "") -> dict:
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO task_workspaces (task_id, kind, local_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
            "kind = excluded.kind, local_path = excluded.local_path, "
            "updated_at = excluded.updated_at",
            (int(task_id), kind, local_path, ts, ts),
        )
        self.conn.commit()
        return self.get_by_task(task_id)

    def delete_for_task(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM task_workspaces WHERE task_id = ?", (int(task_id),))
        self.conn.commit()

    def count_by_kind(self) -> dict[str, int]:
        counts = {"managed": 0, "local": 0}
        for kind, count in self.conn.execute(
            "SELECT kind, COUNT(*) FROM task_workspaces GROUP BY kind"
        ):
            counts[kind] = int(count)
        return counts
