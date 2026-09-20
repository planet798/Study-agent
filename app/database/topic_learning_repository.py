"""Topic Learning Component 数据访问层（Phase 2）。

`topic_learning_components`：一个 Topic 计划采用哪些学习活动。
所有 SQL 集中在此，UI / Planner / Service 不直接写 SQL。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..services.learning_activity import (  # noqa: F401 (re-export for callers)
    ACTIVITY_CODE_READING,
    ACTIVITY_EXPERIMENT,
    ACTIVITY_INTERVIEW,
    ACTIVITY_PRACTICE,
    ACTIVITY_THEORY,
    ALL_ACTIVITY_KINDS,
    activity_order,
    is_valid_activity_kind,
)
from ..utils.date_utils import now_iso


class TopicLearningComponentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 转换 ----------

    @staticmethod
    def _from_row(row) -> dict:
        d = dict(row)
        d["enabled"] = bool(d.get("enabled"))
        d["required"] = bool(d.get("required"))
        return d

    # ---------- 写 ----------

    def create(
        self,
        topic_id: int,
        activity_kind: str,
        enabled: bool = True,
        required: bool = True,
        order_index: int | None = None,
    ) -> dict:
        if not is_valid_activity_kind(activity_kind):
            raise ValueError(f"非法 activity_kind: {activity_kind!r}")
        if order_index is None:
            order_index = activity_order(activity_kind) + 1
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO topic_learning_components "
            "(topic_id, activity_kind, enabled, required, order_index,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(topic_id), activity_kind, int(bool(enabled)),
             int(bool(required)), int(order_index), ts, ts),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, component_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM topic_learning_components WHERE id = ?",
            (int(component_id),),
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_topic_and_kind(
        self, topic_id: int, activity_kind: str
    ) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM topic_learning_components "
            "WHERE topic_id = ? AND activity_kind = ?",
            (int(topic_id), activity_kind),
        ).fetchone()
        return self._from_row(row) if row else None

    # ---------- 读 ----------

    def list_by_topic(
        self, topic_id: int, include_disabled: bool = True
    ) -> list[dict]:
        sql = (
            "SELECT * FROM topic_learning_components WHERE topic_id = ?"
        )
        args: list = [int(topic_id)]
        if not include_disabled:
            sql += " AND enabled = 1"
        sql += " ORDER BY order_index ASC, id ASC"
        rows = self.conn.execute(sql, tuple(args)).fetchall()
        return [self._from_row(r) for r in rows]

    def has_profile(self, topic_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM topic_learning_components WHERE topic_id = ? LIMIT 1",
            (int(topic_id),),
        ).fetchone()
        return row is not None

    def count_by_topic(self, topic_id: int) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM topic_learning_components WHERE topic_id = ?",
            (int(topic_id),),
        ).fetchone()[0])

    # ---------- 更新 ----------

    def update(self, component_id: int, **fields) -> Optional[dict]:
        allowed = {"enabled", "required", "order_index"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get(component_id)
        if "enabled" in sets:
            sets["enabled"] = int(bool(sets["enabled"]))
        if "required" in sets:
            sets["required"] = int(bool(sets["required"]))
        sets["updated_at"] = now_iso()
        clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE topic_learning_components SET {clause} WHERE id = ?",
            (*sets.values(), int(component_id)),
        )
        self.conn.commit()
        return self.get(component_id)

    def set_enabled(self, component_id: int, enabled: bool) -> Optional[dict]:
        return self.update(component_id, enabled=enabled)

    def set_required(self, component_id: int, required: bool) -> Optional[dict]:
        return self.update(component_id, required=required)

    def set_order(self, component_id: int, order_index: int) -> Optional[dict]:
        return self.update(component_id, order_index=int(order_index))

    def delete(self, component_id: int) -> bool:
        """仅在没有任何关联 task 时允许物理删除（保护历史关系）。"""
        if self.has_linked_tasks(component_id):
            raise ValueError("该学习活动已有关联任务，不能删除（请改为停用）")
        cur = self.conn.execute(
            "DELETE FROM topic_learning_components WHERE id = ?",
            (int(component_id),),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- 关联 task ----------

    def has_linked_tasks(self, component_id: int) -> bool:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE component_id = ?",
            (int(component_id),),
        ).fetchone()[0]) > 0

    def has_active_linked_tasks(self, component_id: int) -> bool:
        """是否存在未完成任务（active / not_done）。done/cancelled 不算。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE component_id = ? "
            "AND status IN ('active','not_done')",
            (int(component_id),),
        ).fetchone()[0]) > 0

    def list_component_tasks(self, component_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE component_id = ? "
            "ORDER BY scheduled_date ASC, id ASC",
            (int(component_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_component_done(self, component_id: int) -> bool:
        """存在 status='done' 且绑定该 component 的任务 → 完成。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE component_id = ? "
            "AND status = 'done'",
            (int(component_id),),
        ).fetchone()[0]) > 0

    # ---------- 批量 ----------

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM topic_learning_components ORDER BY topic_id, order_index"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_route(self, route_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.* FROM topic_learning_components c "
            "JOIN study_topics t ON t.id = c.topic_id "
            "JOIN study_phases ph ON ph.id = t.phase_id "
            "JOIN study_plans p ON p.id = ph.plan_id "
            "WHERE p.route_id = ? ORDER BY c.topic_id, c.order_index",
            (int(route_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]
