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
from .schema import (
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_NOT_DONE,
)

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
    # Phase 11：项目化学习字段
    "project_name",
    "project_repo",
    "deliverable",
    "acceptance_criteria",
    "expected_artifact",
    # Phase B：路线归属
    "route_id",
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
    # v4 起：区分任务性质（new / review / extra），并关联知识点
    task_type: str = "new"
    knowledge_point_id: int | None = None
    # v5 起：额外任务难度（basic / practice / challenge）
    difficulty: str = "practice"
    # v11 起（Phase 11）：项目化学习字段；task_type 可为 project / experiment
    project_name: str = ""
    project_repo: str = ""
    deliverable: str = ""
    acceptance_criteria: str = ""
    expected_artifact: str = ""
    # v12 起（Phase B）：所属学习路线（可为 NULL = 未分类）
    route_id: int | None = None

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
    def is_cancelled(self) -> bool:
        """是否被用户主动从当天计划移除（不是未完成，也不是完成）。"""
        return self.status == STATUS_CANCELLED

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

    def list_done_learning_tasks_before(self, date_str: str) -> list[Task]:
        """date_str 之前已完成、且真正“学过”的正式学习任务。

        条件：status=done / task_type='new' / source='generated' /
        已关联知识点 / scheduled_date < date_str。
        供每日巩固复习选候选（不含 manual / extra / review / 未完成任务）。
        """
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date < ? AND status = ? "
            "AND task_type = 'new' AND source = 'generated' "
            "AND knowledge_point_id IS NOT NULL "
            "ORDER BY scheduled_date DESC, id DESC",
            (date_str, STATUS_DONE),
        )
        return self._rows_to_tasks(cur.fetchall())

    def list_review_tasks_by_source(
        self, source: str, start: str, end: str
    ) -> list[Task]:
        """[start, end] 内指定 source 的复习任务（用于每日巩固冷却判断）。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date BETWEEN ? AND ? "
            "AND task_type = 'review' AND source = ? "
            "ORDER BY scheduled_date ASC, id ASC",
            (start, end, source),
        )
        return self._rows_to_tasks(cur.fetchall())

    # ---------- Phase D：Agent Scheduler 统计 ----------

    def count_generated_new_by_date(
        self, date_str: str, route_id: int | None = None
    ) -> int:
        """某天 Agent 已发布的 new task 数（cancelled 不计）。

        只统计 source='generated' AND task_type='new'；manual/review/extra
        均不消耗 Agent Daily Budget。
        """
        sql = (
            "SELECT COUNT(*) AS n FROM tasks WHERE scheduled_date = ? "
            "AND source = 'generated' AND task_type = 'new' "
            "AND status != ?"
        )
        args: list = [date_str, STATUS_CANCELLED]
        if route_id is not None:
            sql += " AND route_id = ?"
            args.append(int(route_id))
        return int(self.conn.execute(sql, tuple(args)).fetchone()["n"] or 0)

    def count_recent_generated_by_route(
        self, route_id: int, start_date: str, end_date: str
    ) -> int:
        """[start_date, end_date]（含两端）内某路线的 Agent new task 数。

        cancelled 不计为“实际服务量”。
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE route_id = ? "
            "AND source = 'generated' AND task_type = 'new' "
            "AND status != ? AND scheduled_date BETWEEN ? AND ?",
            (int(route_id), STATUS_CANCELLED, start_date, end_date),
        ).fetchone()
        return int(row["n"] or 0)

    def has_generated_new_on_date(
        self, date_str: str, route_id: int | None = None
    ) -> bool:
        """某天某路线是否已有 Agent new task（cancelled 不算）。"""
        sql = (
            "SELECT 1 FROM tasks WHERE scheduled_date = ? "
            "AND source = 'generated' AND task_type = 'new' AND status != ?"
        )
        args: list = [date_str, STATUS_CANCELLED]
        if route_id is not None:
            sql += " AND route_id = ?"
            args.append(int(route_id))
        sql += " LIMIT 1"
        return self.conn.execute(sql, tuple(args)).fetchone() is not None

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
        task_type: str = "new",
        knowledge_point_id: int | None = None,
        difficulty: str = "practice",
        project_name: str = "",
        project_repo: str = "",
        deliverable: str = "",
        acceptance_criteria: str = "",
        expected_artifact: str = "",
        route_id: int | None = None,
    ) -> Task:
        """新增一条任务，返回带 id 的 Task。

        :param source: 任务来源（manual=手动 / generated=学习计划自动生成 /
            review=复习任务 / extra=额外学习）
        :param topic_id: 关联的 study_topics 主题 id（自动生成任务使用）
        :param task_type: 任务性质（new / review / extra；Phase 11 起也允许
            project / experiment，无数据库 CHECK 约束）
        :param knowledge_point_id: 关联的知识点 id（复习任务使用）
        :param difficulty: 难度（basic / practice / challenge，额外任务用）
        :param project_name/project_repo/deliverable/acceptance_criteria/
            expected_artifact: Phase 11 项目化学习字段（默认空串）
        """
        if not title.strip():
            raise ValueError("任务标题不能为空")
        date_str = scheduled_date or today()
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO tasks "
            "(title, description, category, estimated_minutes, priority, "
            " status, scheduled_date, postpone_count, created_at, updated_at, "
            " source, topic_id, task_type, knowledge_point_id, difficulty, "
            " project_name, project_repo, deliverable, acceptance_criteria, "
            " expected_artifact, route_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title.strip(), description, category, estimated_minutes,
             priority, STATUS_ACTIVE, date_str, ts, ts, source, topic_id,
             task_type, knowledge_point_id, difficulty,
             project_name or "", project_repo or "", deliverable or "",
             acceptance_criteria or "", expected_artifact or "", route_id),
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

    def list_by_knowledge_point(self, knowledge_point_id: int) -> list[Task]:
        """按知识点 id 查找所有关联任务（复习任务用）。"""
        cur = self.conn.execute(
            "SELECT * FROM tasks WHERE knowledge_point_id = ? "
            "ORDER BY scheduled_date ASC, id ASC",
            (knowledge_point_id,),
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

    def cancel(self, task_id: int) -> bool:
        """把任务标记为 cancelled（用户主动移除今日任务）。

        只改 status / updated_at，绝不 DELETE、不写 completed_at / not_done_at，
        以保留 Planner 历史与用户取消记录。
        """
        ts = now_iso()
        cur = self.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_CANCELLED, ts, task_id),
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
        # 统计只覆盖“用户最终选择保留执行的任务”的状态；cancelled 不计入
        # 总数与完成率分母（用户主动移除，不算未完成）。
        counts = {STATUS_ACTIVE: 0, STATUS_DONE: 0, STATUS_NOT_DONE: 0}
        for row in cur:
            if row["status"] in counts:
                counts[row["status"]] = row["n"]

        total = counts[STATUS_ACTIVE] + counts[STATUS_DONE] + counts[STATUS_NOT_DONE]
        done = counts[STATUS_DONE]
        rate = round(done / total * 100, 1) if total else 0.0
        return {
            "total": total,
            "done": done,
            "not_done": counts[STATUS_NOT_DONE] + counts[STATUS_ACTIVE],
            "active": counts[STATUS_ACTIVE],
            "rate": rate,
        }
