"""数据访问层（Repository）。

封装所有针对 tasks 表的 SQL 操作，向上层（services）
提供面向对象的 Task 数据对象和业务操作方法。
不在此层做业务校验（如延期次数警告），那是 services 的职责。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Iterable

import sqlite3

from ..utils.date_utils import now_iso, today
from .schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE

# 允许通过 dict 批量更新的字段白名单（不允许直接改 id / created_at / scheduled_date 等）
_UPDATABLE_FIELDS = (
    "title",
    "description",
    "category",
    "estimated_minutes",
    "priority",
    "status",
    "reason",
    "postpone_count",
    "completed_at",
    "not_done_at",
    "updated_at",
)


@dataclass
class Task:
    """内存中的任务数据对象，与 tasks 表字段一一对应。"""

    id: int
    title: str
    description: str = ""
    category: str = "学习"
    estimated_minutes: int = 0
    priority: int = 1
    status: str = STATUS_ACTIVE
    reason: str | None = None
    scheduled_date: str = ""
    postpone_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    not_done_at: str | None = None
    source: str = "manual"
    topic_id: int | None = None

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    @property
    def is_not_done(self) -> bool:
        return self.status == STATUS_NOT_DONE

    @property
    def over_postpone_limit(self) -> bool:
        """是否已达连续延期警告阈值（3 次）。"""
        return self.postpone_count >= 3


class TaskRepository:
    """tasks 表的增删改查。"""

    _COLUMNS = tuple(
        f.name
        for f in fields(Task)
        if f.name != "id"
    )

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 查询 ----------

    @staticmethod
    def _row_to_task(row: sqlite3.Row | None) -> Task | None:
        if row is None:
            return None
        return Task(**dict(row))

    @staticmethod
    def _rows_to_tasks(rows: Iterable[sqlite3.Row]) -> list[Task]:
        return [Task(**dict(r)) for r in rows]

    def get(self, task_id: int) -> Task | None:
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        return self._row_to_task(cur.fetchone())

    def list_by_date(self, date_str: str) -> list[Task]:
        """某一天的全部任务（任意状态）。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date = ? "
            "ORDER BY priority DESC, id ASC",
            (date_str,),
        )
        return self._rows_to_tasks(cur.fetchall())

    def list_active_by_date(self, date_str: str) -> list[Task]:
        """某一天待办（active）的任务，用于"今日任务"列表。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date = ? AND status = ? "
            "ORDER BY priority DESC, id ASC",
            (date_str, STATUS_ACTIVE),
        )
        return self._rows_to_tasks(cur.fetchall())

    def list_active_before(self, date_str: str) -> list[Task]:
        """所有日期早于 date_str 且仍为待办（active）的任务。

        用于日期切换时识别"过期未处理"任务（跨天遗留）。
        """
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date < ? AND status = ? "
            "ORDER BY scheduled_date ASC, id ASC",
            (date_str, STATUS_ACTIVE),
        )
        return self._rows_to_tasks(cur.fetchall())

    def list_between(self, start: str, end: str) -> list[Task]:
        """[start, end] 区间（含两端）内的任务，用于周/月统计。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date BETWEEN ? AND ? "
            "ORDER BY scheduled_date ASC, id ASC",
            (start, end),
        )
        return self._rows_to_tasks(cur.fetchall())

    # ---------- 写入 ----------

    def create(
        self,
        title: str,
        scheduled_date: str | None = None,
        description: str = "",
        category: str = "学习",
        estimated_minutes: int = 0,
        priority: int = 1,
        source: str = "manual",
        topic_id: int | None = None,
    ) -> Task:
        """新增一条任务，返回带 id 的 Task。

        :param source: 任务来源（manual=手动 / generated=学习计划自动生成）
        :param topic_id: 关联的 study_topics 主题 id（自动生成任务使用）
        """
        if not title.strip():
            raise ValueError("任务标题不能为空")
        date_str = scheduled_date or today()
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO tasks "
            "(title, description, category, estimated_minutes, priority, "
            " status, scheduled_date, postpone_count, created_at, updated_at, "
            " source, topic_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (title.strip(), description, category, estimated_minutes,
             priority, STATUS_ACTIVE, date_str, ts, ts, source, topic_id),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def list_by_topic_id(self, topic_id: int) -> list[Task]:
        """按主题 id 查找所有关联任务（可判断是否已完成）。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE topic_id = ? ORDER BY scheduled_date ASC, id ASC",
            (topic_id,),
        )
        return self._rows_to_tasks(cur.fetchall())

    def list_earliest_active_for_topic(self, topic_id: int) -> Task | None:
        """某主题最早的未完成任务（用于检查是否有等待中的延期/待办）。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE topic_id = ? AND status = ? "
            "ORDER BY scheduled_date ASC, id ASC LIMIT 1",
            (topic_id, STATUS_ACTIVE),
        )
        return self._row_to_task(cur.fetchone())

    def update(self, task_id: int, **fields: Any) -> Task | None:
        """按白名单更新字段。返回更新后的 Task，任务不存在返回 None。"""
        allowed = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not allowed:
            return self.get(task_id)
        allowed["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in allowed)
        values = list(allowed.values()) + [task_id]
        self.conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get(task_id)

    def mark_done(self, task_id: int) -> bool:
        """标记任务完成。返回是否更新成功。"""
        ts = now_iso()
        cur = self.conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ?, "
            "not_done_at = NULL, updated_at = ? WHERE id = ?",
            (STATUS_DONE, ts, ts, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_not_done(self, task_id: int, reason: str) -> bool:
        """标记未完成，并记录原因（业务层负责校验 reason 非空）。"""
        if not reason.strip():
            raise ValueError("未完成原因不能为空")
        ts = now_iso()
        cur = self.conn.execute(
            "UPDATE tasks SET status = ?, reason = ?, not_done_at = ?, "
            "completed_at = NULL, updated_at = ? WHERE id = ?",
            (STATUS_NOT_DONE, reason.strip(), ts, ts, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def postpone(self, task_id: int, new_date: str) -> bool:
        """把任务改期到 new_date，并累计延期次数。"""
        ts = now_iso()
        cur = self.conn.execute(
            "UPDATE tasks SET scheduled_date = ?, postpone_count = postpone_count + 1, "
            "updated_at = ? WHERE id = ?",
            (new_date, ts, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_status(self, task_id: int, status: str) -> bool:
        """直接设置状态（供日期切换 / 测试等使用）。"""
        ts = now_iso()
        cur = self.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, task_id: int) -> bool:
        """删除任务（保留，供后续 UI 删除功能使用）。"""
        cur = self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- 元数据（app_meta 键值对） ----------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        """读取应用元数据，不存在时返回 default。"""
        cur = self.conn.execute(
            "SELECT value FROM app_meta WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        """写入（或更新）应用元数据。"""
        self.conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- 统计 ----------

    def stats_by_date(self, date_str: str) -> dict[str, int]:
        """某一天的任务统计：总数 / 完成数 / 未完成数 / 待办数 / 完成率。"""
        cur = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks "
            "WHERE scheduled_date = ? GROUP BY status",
            (date_str,),
        )
        counts: dict[str, int] = {STATUS_ACTIVE: 0, STATUS_DONE: 0, STATUS_NOT_DONE: 0}
        for row in cur:
            counts[row["status"]] = row["n"]

        total = sum(counts.values())
        done = counts[STATUS_DONE]
        rate = round(done / total * 100, 1) if total else 0.0
        return {
            "total": total,
            "done": done,
            "not_done": counts[STATUS_NOT_DONE] + counts[STATUS_ACTIVE],
            "active": counts[STATUS_ACTIVE],
            "rate": rate,
        }
