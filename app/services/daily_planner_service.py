"""每日动态规划服务。

职责：
- 根据"学习计划 + 最近 7 天 + 延期/完成情况"构造 PlanningContext；
- 调用 AIPlanner 得到 DailyPlan 建议；
- 通过本地规则二次校验（只允许当前 Phase topic、不超预算、不重复、
  不触碰已完成任务）；
- 创建合法任务、保存 planner_decisions 决策记录；
- 幂等：同一天重复调用返回已有计划，不重复创建；
- AI 失败时自动 fallback 到规则型 generate_daily_tasks()。

约束（重要）：AI 只调整每日任务；不修改 StudyPlan/Phase/Topic、
不修改历史完成记录、不创建新知识领域、不触碰已完成任务。
"""

from __future__ import annotations

import json

from ..ai.interface import AIServiceError
from ..ai.planner import AIPlanner
from ..ai.planner_context import (
    ContextTask,
    ContextTopic,
    DaySummary,
    PlanningContext,
)
from ..database.repository import Task, TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from ..database.study_plan_repository import (
    PlannerDecisionRepository,
    StudyPlanRepository,
)
from ..utils.date_utils import add_days
from .study_plan_service import MAX_DAILY_STUDY_MINUTES, StudyPlanService

WEEK_DAYS = 7


class DailyPlannerService:
    def __init__(
        self,
        repo: TaskRepository,
        plan_repo: StudyPlanRepository | None = None,
        planner: AIPlanner | None = None,
        study_plan_service: StudyPlanService | None = None,
        max_daily_minutes: int = MAX_DAILY_STUDY_MINUTES,
    ):
        self.repo = repo
        self.plan_repo = plan_repo or StudyPlanRepository(repo.conn)
        self.study_plan_service = study_plan_service or StudyPlanService(
            repo, self.plan_repo
        )
        self.planner = planner
        self.decision_repo = PlannerDecisionRepository(repo.conn)
        self.max_daily_minutes = max_daily_minutes

    # ================= 幂等保护 =================

    def latest_plan_for_date(self, date_str: str) -> dict | None:
        """返回该日期已存在的规划决策（用于幂等）。"""
        return self.decision_repo.latest_for_date(date_str)

    # ================= 上下文构造 =================

    def build_context(self, date_str: str) -> PlanningContext:
        """构造传给 AI 的上下文（目标日期为 date 的"下一天"）。"""
        plan_next_date = add_days(date_str, 1)
        phase = self.study_plan_service.get_current_phase(plan_next_date)
        ctx = PlanningContext(current_date=plan_next_date)
        if phase is None:
            return ctx

        ctx.current_phase = phase.name
        ctx.phase_goal = phase.goals or ""
        ctx.available_topics = [
            ContextTopic(
                topic_id=t.id,
                title=t.name,
                description=t.description,
                estimated_minutes=t.estimated_minutes,
                priority=t.priority,
            )
            for t in phase.topics
        ]

        recent = self._build_recent_days(plan_next_date, week=WEEK_DAYS)
        ctx.recent_7_days = recent

        # 任务摘要
        unfinished, postponed, completed = self._classify_tasks(plan_next_date)
        ctx.unfinished_tasks = unfinished
        ctx.postponed_tasks = postponed
        ctx.completed_tasks = completed

        # 学习时间统计（过去 7 天）
        ctx.estimated_minutes = sum(d.estimated_minutes for d in recent)
        ctx.actual_completed_minutes = sum(d.completed_minutes for d in recent)
        ctx.current_daily_limit = self.max_daily_minutes
        return ctx

    def _build_recent_days(self, anchor: str, week: int = WEEK_DAYS) -> list[DaySummary]:
        """anchor 之前 week 天（不含 anchor）的每日摘要，按日期升序。"""
        out: list[DaySummary] = []
        day = add_days(anchor, -week)
        for _ in range(week):
            stats = self.repo.stats_by_date(day)
            tasks = self.repo.list_by_date(day)
            postponed = sum(
                1 for t in tasks if t.postpone_count > 0
            )
            completed_min = sum(
                t.estimated_minutes for t in tasks if t.status == STATUS_DONE
            )
            estimated = sum(t.estimated_minutes for t in tasks)
            rate = stats["rate"]
            out.append(
                DaySummary(
                    date=day,
                    total_tasks=stats["total"],
                    completed_tasks=stats["done"],
                    not_done_tasks=stats["not_done"],
                    postponed_tasks=postponed,
                    completion_rate=rate,
                    estimated_minutes=estimated,
                    completed_minutes=completed_min,
                )
            )
            day = add_days(day, 1)
        return out

    def _classify_tasks(self, anchor: str):
        """把最近任务分成未完成 / 延期 / 已完成三类摘要。"""
        start = add_days(anchor, -7)
        tasks = self.repo.list_between(start, add_days(anchor, -1))

        unfinished: list[ContextTask] = []
        postponed: list[ContextTask] = []
        completed: list[ContextTask] = []

        for t in tasks:
            if t.status == STATUS_NOT_DONE:
                unfinished.append(self._ctx_task(t))
            elif t.status == STATUS_DONE:
                completed.append(self._ctx_task(t))
            if t.postpone_count > 0:
                postponed.append(self._ctx_task(t))
        return unfinished, postponed, completed

    @staticmethod
    def _ctx_task(t: Task) -> ContextTask:
        return ContextTask(
            task_id=t.id,
            title=t.title,
            status=t.status,
            scheduled_date=t.scheduled_date,
            postpone_count=t.postpone_count,
            reason=t.reason,
        )

    # ================= 主流程 =================

    def generate_next_day_plan(self, date_str: str) -> dict:
        """为 date_str 的"下一天"生成计划（幂等）。

        :param date_str: 今天的日期
        :return: {"date", "created", "fallback", "existing", ...}
        """
        plan_date = add_days(date_str, 1)

        # 幂等：同一天已有计划且当天确实已有任务时，直接返回（不重复生成）。
        # 只存在决策但当天没有任何任务（例如上次因阶段全部完成而生成为空），
        # 则允许重新生成，避免“当天永远空任务”的卡死。
        existing = self.latest_plan_for_date(plan_date)
        if existing is not None and len(self.repo.list_by_date(plan_date)) > 0:
            return {
                "date": plan_date,
                "existing": True,
                "decision_id": existing["id"],
                "created": [],
                "fallback": False,
            }

        if self.planner is None or not self.planner.is_configured():
            return self._fallback_plan(date_str, plan_date, reason="ai_not_configured")

        context = self.build_context(date_str)
        try:
            plan = self.planner.plan_next_day(context)
        except AIServiceError:
            return self._fallback_plan(date_str, plan_date, reason="ai_error")

        # 本地二次校验 + 创建任务（严格：任何违规则整体回退规则型）
        valid, accepted, problems = self._validate_and_create(plan, plan_date)
        if not valid:
            return self._fallback_plan(date_str, plan_date, reason="validation_failed")

        self._save_decision(
            date=plan_date,
            phase_id=self._current_phase_id(plan_date),
            context=context,
            plan=plan,
            accepted=accepted,
            source="ai",
        )
        return {
            "date": plan_date,
            "existing": False,
            "fallback": False,
            "created": [t for t in accepted],
            "reasoning": plan.reasoning,
            "adjustment": plan.adjustment,
        }

    # ---------- fallback ----------

    def _fallback_plan(self, date_str: str, plan_date: str, reason: str) -> dict:
        """AI 不可用/失败时回退到规则型生成（不因 AI 失败而无法生成）。"""
        result = self.study_plan_service.generate_daily_tasks(plan_date)
        accepted_ids = [t.id for t in result.get("generated", [])]
        self._save_decision(
            date=plan_date,
            phase_id=self._current_phase_id(plan_date),
            context=None,
            plan=None,
            accepted=accepted_ids,
            source="fallback_rule",
        )
        return {
            "date": plan_date,
            "existing": False,
            "fallback": True,
            "fallback_reason": reason,
            "created": accepted_ids,
        }

    # ---------- 本地校验与创建 ----------

    def _validate_and_create(self, plan, plan_date: str):
        """本地规则二次校验 AI 建议，全部合法才创建任务。

        :return: (valid: bool, created_ids: list[int], problems: list[str])

        规则：
        - daily_minutes 不得超过每日上限
        - topic_id 必须属于当前阶段
        - topic 不允许已完成再次生成
        - 同一天已有同主题任务则不重复（去重）
        - task_id 必须真实存在、且未完成
        - 延期任务不重复生成（同主题已有置到今天则跳过）
        任何一条违规则整份计划不采纳（回退规则型），避免部分写入。
        """
        problems: list[str] = []
        created: list[int] = []

        # 当日预算
        if plan.daily_minutes > self.max_daily_minutes:
            problems.append(f"daily_minutes {plan.daily_minutes} 超出上限")

        # 当前阶段合法 topic 集合
        current_phase = self.study_plan_service.get_current_phase(plan_date)
        valid_topic_ids: set[int] = set()
        if current_phase is not None:
            valid_topic_ids = {t.id for t in current_phase.topics}
        else:
            problems.append(f"{plan_date} 不在任何阶段内")

        done_topic_ids = self._done_topic_ids()
        scheduled_topic_ids = self._scheduled_topic_ids(plan_date)

        # 推荐任务校验（不实际创建，先全部校验）
        to_create: list = []
        seen_topic_ids: set[int] = set()
        for rec in plan.recommended_tasks:
            if rec.topic_id not in valid_topic_ids:
                problems.append(f"topic_id {rec.topic_id} 不属于当前阶段")
                continue
            if rec.topic_id in done_topic_ids:
                problems.append(f"topic_id {rec.topic_id} 已完成，不应重新生成")
                continue
            if rec.topic_id in scheduled_topic_ids or rec.topic_id in seen_topic_ids:
                # 已存在/已排过：去重，不算违规
                continue
            seen_topic_ids.add(rec.topic_id)
            to_create.append(rec)

        # carry_over 校验
        to_carry: list = []
        for carry in plan.carry_over_tasks:
            task = self.repo.get(carry.task_id)
            if task is None:
                problems.append(f"task_id {carry.task_id} 不存在")
                continue
            if task.status == STATUS_DONE:
                problems.append(f"task_id {carry.task_id} 已完成，不能修改")
                continue
            if not _is_recent_unfinished(task, plan_date):
                problems.append(f"task_id {carry.task_id} 不是近期未完成任务")
                continue
            # 延期去重：同主题已有任务排在计划日则跳过
            if task.topic_id:
                same = self.repo.list_earliest_active_for_topic(task.topic_id)
                if same is not None and same.id != carry.task_id:
                    continue
            to_carry.append((carry, task))

        # 任何违规 => 不采纳整份计划（不写库不建任务）
        if problems:
            return False, [], problems

        # 全部合法：先创建推荐任务
        for rec in to_create:
            task = self._create_task_from_recommendation(rec, plan_date)
            created.append(task.id)
        # 再安排 carry_over（改期到今天，保留延期次数）
        for carry, task in to_carry:
            self.repo.postpone(carry.task_id, plan_date)
            self.repo.set_status(carry.task_id, STATUS_ACTIVE)
            created.append(carry.task_id)

        return True, created, []

    def _scheduled_topic_ids(self, date_str: str) -> set[int]:
        """某天已安排（active）主题的 topic_id 集合（用于去重）。"""
        rows = self.repo.conn.execute(
            "SELECT topic_id FROM tasks WHERE scheduled_date = ? AND status = ? "
            "AND topic_id IS NOT NULL",
            (date_str, STATUS_ACTIVE),
        ).fetchall()
        return {r["topic_id"] for r in rows}

    def _create_task_from_recommendation(self, rec, plan_date: str) -> Task:
        return self.repo.create(
            title=rec.title,
            scheduled_date=plan_date,
            description=rec.description,
            category="学习",
            estimated_minutes=rec.estimated_minutes,
            priority=rec.priority,
            source="generated",
            topic_id=rec.topic_id,
        )

    def _done_topic_ids(self) -> set[int]:
        rows = self.repo.conn.execute(
            "SELECT DISTINCT topic_id FROM tasks WHERE status = ? AND topic_id IS NOT NULL",
            (STATUS_DONE,),
        ).fetchall()
        return {r["topic_id"] for r in rows}

    def _current_phase_id(self, plan_date: str) -> int | None:
        phase = self.study_plan_service.get_current_phase(plan_date)
        return phase.id if phase is not None else None

    def _save_decision(
        self,
        date: str,
        phase_id: int | None,
        context: PlanningContext | None,
        plan,
        accepted: list[int],
        source: str,
    ) -> None:
        ctx_json = context.to_json() if context is not None else "{}"
        if plan is not None:
            plan_json = json.dumps(plan, ensure_ascii=False, default=_plan_to_dict)
        else:
            plan_json = "{}"
        self.decision_repo.create(
            date=date,
            current_phase_id=phase_id,
            input_context=ctx_json,
            ai_response=plan_json,
            accepted_tasks=json.dumps(accepted, ensure_ascii=False),
            source=source,
        )


def _plan_to_dict(obj):
    """把 DailyPlan 序列化为 dict（供 JSON 落库）。"""
    if obj is None:
        return {}
    return {
        "reasoning": obj.reasoning,
        "recommended_tasks": [
            {
                "topic_id": r.topic_id,
                "title": r.title,
                "description": r.description,
                "estimated_minutes": r.estimated_minutes,
                "priority": r.priority,
            }
            for r in obj.recommended_tasks
        ],
        "carry_over_tasks": [
            {"task_id": c.task_id, "reason": c.reason} for c in obj.carry_over_tasks
        ],
        "daily_minutes": obj.daily_minutes,
        "adjustment": obj.adjustment,
    }


def _is_recent_unfinished(task: Task, plan_date: str) -> bool:
    """判断是否为"近期未完成"任务，允许 carry_over。

    - 状态为 active 或 not_done（非 done）；
    - 计划日期不晚于目标日（即今天之前遗留，或延期到计划日的）。
    """
    if task.status == STATUS_DONE:
        return False
    if task.status == STATUS_NOT_DONE:
        return True
    # active：只允许日期早于目标日，或已经排在目标日（延期进来）
    return task.scheduled_date <= plan_date
