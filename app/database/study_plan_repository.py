"""学习计划数据访问层（Repository）。

数据模型：
- StudyPlan  : 长期计划（这几个月学什么）
- StudyPhase : 阶段（这一阶段学什么），属于一个计划
- StudyTopic : 主题（具体要掌握什么），属于一个阶段

一个 Plan 包含多个 Phase，一个 Phase 包含多个 Topic。
只负责 CRUD，业务规则（当前阶段判断、每日任务生成）在 service 层。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class StudyPhase:
    id: int
    plan_id: int
    name: str
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    priority: int = 1
    goals: str = ""
    topics: list["StudyTopic"] = field(default_factory=list)


@dataclass
class StudyTopic:
    id: int
    phase_id: int
    name: str
    description: str = ""
    estimated_minutes: int = 30
    priority: int = 1
    order_index: int = 0


@dataclass
class StudyPlan:
    id: int
    name: str
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = "active"
    phases: list[StudyPhase] = field(default_factory=list)


class StudyPlanRepository:
    """study_plans / study_phases / study_topics 三张表的读写。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 计划 ----------

    def create_plan(
        self,
        name: str,
        start_date: str,
        end_date: str,
        description: str = "",
        status: str = "active",
    ) -> StudyPlan:
        cur = self.conn.execute(
            "INSERT INTO study_plans (name, description, start_date, end_date, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, description, start_date, end_date, status),
        )
        self.conn.commit()
        return StudyPlan(
            id=cur.lastrowid, name=name, description=description,
            start_date=start_date, end_date=end_date, status=status,
        )

    def get_plan(self, plan_id: int) -> StudyPlan | None:
        row = self.conn.execute(
            "SELECT * FROM study_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return self._plan_from_row(row) if row else None

    def list_plans(self, status: str | None = None) -> list[StudyPlan]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM study_plans WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM study_plans ORDER BY id"
            ).fetchall()
        return [self._plan_from_row(r) for r in rows]

    def get_active_plan(self) -> StudyPlan | None:
        """返回当前唯一的 active 计划（如有多个取第一个）。"""
        plans = self.list_plans(status="active")
        return plans[0] if plans else None

    # ---------- 阶段 ----------

    def create_phase(
        self,
        plan_id: int,
        name: str,
        start_date: str,
        end_date: str,
        description: str = "",
        priority: int = 1,
        goals: str = "",
    ) -> StudyPhase:
        cur = self.conn.execute(
            "INSERT INTO study_phases "
            "(plan_id, name, description, start_date, end_date, priority, goals) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, name, description, start_date, end_date, priority, goals),
        )
        self.conn.commit()
        return StudyPhase(
            id=cur.lastrowid, plan_id=plan_id, name=name, description=description,
            start_date=start_date, end_date=end_date, priority=priority, goals=goals,
        )

    def list_phases(self, plan_id: int) -> list[StudyPhase]:
        rows = self.conn.execute(
            "SELECT * FROM study_phases WHERE plan_id = ? "
            "ORDER BY start_date ASC, priority DESC, id ASC",
            (plan_id,),
        ).fetchall()
        return [self._phase_from_row(r) for r in rows]

    def get_phase(self, phase_id: int) -> StudyPhase | None:
        row = self.conn.execute(
            "SELECT * FROM study_phases WHERE id = ?", (phase_id,)
        ).fetchone()
        return self._phase_from_row(row) if row else None

    # ---------- 主题 ----------

    def create_topic(
        self,
        phase_id: int,
        name: str,
        description: str = "",
        estimated_minutes: int = 30,
        priority: int = 1,
        order_index: int = 0,
    ) -> StudyTopic:
        cur = self.conn.execute(
            "INSERT INTO study_topics "
            "(phase_id, name, description, estimated_minutes, priority, order_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (phase_id, name, description, estimated_minutes, priority, order_index),
        )
        self.conn.commit()
        return StudyTopic(
            id=cur.lastrowid, phase_id=phase_id, name=name, description=description,
            estimated_minutes=estimated_minutes, priority=priority,
            order_index=order_index,
        )

    def list_topics(self, phase_id: int) -> list[StudyTopic]:
        rows = self.conn.execute(
            "SELECT * FROM study_topics WHERE phase_id = ? "
            "ORDER BY priority DESC, order_index ASC, id ASC",
            (phase_id,),
        ).fetchall()
        return [self._topic_from_row(r) for r in rows]

    # ---------- 组合读取 ----------

    def get_phases_with_topics(self, plan_id: int) -> list[StudyPhase]:
        """一次取出某计划的所有阶段，并附带各自的主题列表。"""
        phases = self.list_phases(plan_id)
        for ph in phases:
            ph.topics = self.list_topics(ph.id)
        return phases

    def get_plan_with_phases(self, plan_id: int) -> StudyPlan | None:
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        plan.phases = self.get_phases_with_topics(plan_id)
        return plan

    def get_full_plan(self, plan_id: int) -> StudyPlan | None:
        """加载计划、所有阶段及其主题（供任务生成使用）。"""
        return self.get_plan_with_phases(plan_id)

    # ---------- 转换 ----------

    @staticmethod
    def _plan_from_row(row) -> StudyPlan:
        return StudyPlan(**dict(row))

    @staticmethod
    def _phase_from_row(row) -> StudyPhase:
        return StudyPhase(**dict(row))

    @staticmethod
    def _topic_from_row(row) -> StudyTopic:
        return StudyTopic(**dict(row))


class PlannerDecisionRepository:
    """planner_decisions 表读写（AI 决策审计）。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        date: str,
        input_context: str,
        ai_response: str,
        accepted_tasks: str,
        current_phase_id: int | None = None,
        source: str = "ai",
    ) -> int:
        import datetime

        created_at = datetime.datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO planner_decisions "
            "(date, current_phase_id, input_context, ai_response, accepted_tasks,"
            " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, current_phase_id, input_context, ai_response, accepted_tasks,
             source, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def latest_for_date(self, date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM planner_decisions WHERE date = ? "
            "ORDER BY id DESC LIMIT 1",
            (date,),
        ).fetchone()
        return dict(row) if row else None

    def list_for_date(self, date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM planner_decisions WHERE date = ? ORDER BY id ASC",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]


class SummaryCacheRepository:
    """weekly_summaries / monthly_summaries 缓存表读写。

    缓存键为 (period_start, period_end)。stats_json 记录生成时的统计快照，
    用于判断"统计数据是否变化"。
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(
        self, table: str, period_start: str, period_end: str
    ) -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE period_start = ? AND period_end = ? "
            "ORDER BY id DESC LIMIT 1",
            (period_start, period_end),
        ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        table: str,
        period_start: str,
        period_end: str,
        stats_json: str,
        ai_summary_json: str = "",
        source: str = "local",
    ) -> int:
        import datetime

        created_at = datetime.datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            f"INSERT INTO {table} "
            "(period_start, period_end, stats_json, ai_summary_json, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (period_start, period_end, stats_json, ai_summary_json, source, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert(
        self,
        table: str,
        period_start: str,
        period_end: str,
        stats_json: str,
        ai_summary_json: str = "",
        source: str = "local",
    ) -> int:
        """同期间若已存在则覆盖，否则新增。"""
        existing = self.get(table, period_start, period_end)
        if existing is not None:
            import datetime

            self.conn.execute(
                f"UPDATE {table} SET stats_json = ?, ai_summary_json = ?, "
                "source = ?, created_at = ? WHERE id = ?",
                (
                    stats_json,
                    ai_summary_json,
                    source,
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    existing["id"],
                ),
            )
            self.conn.commit()
            return existing["id"]
        return self.save(table, period_start, period_end, stats_json,
                         ai_summary_json, source)
