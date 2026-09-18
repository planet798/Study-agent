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
    JdGapSkill,
    KnowledgeEvidence,
    MarketTrend,
    PlanningContext,
    PrerequisiteBlocked,
    SkillPriority,
    WeeklyFocus,
)
from ..database.repository import Task, TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_DONE, STATUS_NOT_DONE
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
        assessment_repo=None,
        skill_service=None,
        jd_service=None,
        scope_tasks_by_route: bool = False,
    ):
        self.repo = repo
        self.plan_repo = plan_repo or StudyPlanRepository(repo.conn)
        self.study_plan_service = study_plan_service or StudyPlanService(
            repo, self.plan_repo
        )
        # 保证共用的 StudyPlanService 具备 knowledge_point 关联能力
        if self.study_plan_service.assessment_repo is None and \
                assessment_repo is not None:
            self.study_plan_service.assessment_repo = assessment_repo
        self.planner = planner
        self.decision_repo = PlannerDecisionRepository(repo.conn)
        self.max_daily_minutes = max_daily_minutes
        # 可选：读取 knowledge_points / assessment_attempts 作为掌握证据（Phase 8）
        self.assessment_repo = assessment_repo
        # 可选：SkillService 与 JdService（Phase C）；不注入则字段为空、行为与旧版一致
        self.skill_service = skill_service
        self.jd_service = jd_service
        # Phase D：route-scoped 实例（Scheduler 创建）置 True，
        # 使 recent/任务摘要/掌握证据只读本路线，避免跨路线污染。
        self.scope_tasks_by_route = scope_tasks_by_route

    def _route_id(self) -> int | None:
        try:
            return self.study_plan_service._resolved_route_id()
        except Exception:  # noqa: BLE001
            return None

    def _route_name(self) -> str:
        route_id = self._route_id()
        repo = getattr(self.study_plan_service, "learning_route_repo", None)
        if route_id is None or repo is None:
            return ""
        try:
            route = repo.get(route_id)
            return route.name if route is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _route_goal(self) -> str:
        route_id = self._route_id()
        repo = getattr(self.study_plan_service, "learning_route_repo", None)
        if route_id is None or repo is None:
            return ""
        try:
            route = repo.get(route_id)
            return (route.goal or "") if route is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _allowed_skill_names(self) -> set[str] | None:
        """本路线允许影响 Planner 的技能名集合。

        - None：不做限制（默认路线且无显式 route_skills 绑定 → 保留旧行为）；
        - set()：完全无技能信号（非默认路线且未绑定任何 skill）；
        - 非空 set：只允许 route_skills 中显式绑定的技能。
        """
        binding = self.route_skill_ids()
        route_id = self._route_id()
        repo = getattr(self.study_plan_service, "learning_route_repo", None)
        if repo is None or route_id is None:
            # 无法读取路线/route_skills（旧调用）→ 不限制，保持旧行为
            return None
        is_default = False
        try:
            default = repo.get_default_learning_route()
            is_default = default is not None and default.id == route_id
        except Exception:  # noqa: BLE001
            is_default = False
        if not binding:
            return None if is_default else set()
        names: set[str] = set()
        if self.skill_service is not None:
            for skill_id in binding:
                try:
                    skill = self.skill_service.skill_repo.get(skill_id)
                except Exception:  # noqa: BLE001
                    skill = None
                if skill is not None:
                    names.add(skill["name"])
        return names

    def route_skill_ids(self) -> set[int]:
        """本路线绑定的 skill id 集合（无绑定/无 repo 时为空集）。"""
        route_id = self._route_id()
        repo = getattr(self.study_plan_service, "learning_route_repo", None)
        if route_id is None or repo is None:
            return set()
        try:
            return set(repo.list_skill_ids(route_id))
        except Exception:  # noqa: BLE001
            return set()

    # ================= 幂等保护 =================

    def latest_plan_for_date(
        self, date_str: str, route_id: int | None = None
    ) -> dict | None:
        """返回该日期已存在的规划决策（用于幂等）。Phase D 按路线查询。"""
        return self.decision_repo.latest_for_date(date_str, route_id=route_id)

    # ================= 上下文构造 =================

    def build_context(self, date_str: str) -> PlanningContext:
        """构造传给 AI 的上下文（目标日期为 date 的"下一天"）。"""
        plan_next_date = add_days(date_str, 1)
        phase = self.study_plan_service.get_current_phase(plan_next_date)
        ctx = PlanningContext(
            current_date=plan_next_date,
            route_id=self._route_id(),
            route_name=self._route_name(),
            route_goal=(self._route_goal() if hasattr(self, "_route_goal") else ""),
        )
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
        # Phase 8：把真实验收证据放进上下文（无证据的知识点不出现，不伪造 mastery）
        self._fill_knowledge_evidence(ctx, plan_next_date)
        # Phase C：JD / 技能 / 四周优先级上下文（无注入时保持空、兼容）
        self._fill_skill_context(ctx, plan_next_date)
        return ctx

    def _fill_skill_context(self, ctx: PlanningContext, plan_date: str) -> None:
        """用 SkillService/JdService 的结果填充技能上下文；不重复计算优先级。

        Step 6：使用近期市场信号（Daily Summary 近30天趋势优先，
        individual JD fallback），并区分 active_gap / blocked_gap。
        """
        skill_service = self.skill_service
        if skill_service is None:
            return
        # Phase D：route-scoped 技能过滤（无绑定的非默认路线→无技能信号）
        allowed_skills = self._allowed_skill_names()
        if allowed_skills is not None and len(allowed_skills) == 0:
            return

        # 刷新近期市场（用计划日作为窗口结束日，保证与当天一致）
        market = None
        try:
            market = skill_service.refresh_market(plan_date)
        except Exception:  # noqa: BLE001
            market = None
        if market:
            ctx.market_source = market.get("source", "none")
            ctx.market_sample_count_30d = int(market.get("sample_count_30d") or 0)
            trends = []
            for name, rec in (market.get("skills") or {}).items():
                if allowed_skills is not None and name not in allowed_skills:
                    continue
                trends.append(MarketTrend(
                    skill=name,
                    market_30d=float(rec.get("freq30") or 0.0),
                    mention_30d=int(rec.get("mention_30d") or 0),
                ))
            trends.sort(key=lambda t: (-t.market_30d, t.skill))
            ctx.market_trends = trends[:10]

        # 当前 phase（用于 stage alignment 排序偏好）
        current_phase = None
        try:
            current_phase = self.study_plan_service.get_current_phase(plan_date)
        except Exception:  # noqa: BLE001
            current_phase = None

        # 1) 近期技能优先级（SkillService 计算，仅 gate 放行的可学技能）
        try:
            candidates = skill_service.select_active_candidates(
                limit=8, current_phase=current_phase
            )
        except Exception:  # noqa: BLE001 - 技能数据异常不影响规划
            candidates = []
        if allowed_skills is not None:
            candidates = [
                d for d in candidates if d.get("name") in allowed_skills
            ]
        priorities: list[SkillPriority] = []
        for d in candidates:
            skill = skill_service.skill_repo.get_by_name(d["name"])
            explain = skill_service.explain_skill(
                skill, current_phase=current_phase
            ) if skill else d
            priorities.append(SkillPriority(
                skill=explain.get("name", d["name"]),
                tier=explain.get("tier", d.get("tier", "")),
                score=float(explain.get("score", d.get("score", 0.0))),
                reason=" · ".join(explain.get("reasons") or []) or _skill_reason(d),
                market_30d=explain.get("market_30d"),
                sample_count=int(explain.get("sample_count_30d") or 0),
                market_source=explain.get("market_source", "none"),
                stage_alignment=explain.get("stage_alignment", "unknown"),
                blocked=bool(explain.get("blocked")),
                reasons=tuple(explain.get("reasons") or ()),
            ))
        ctx.skill_priorities = priorities

        # 2) JD 缺口：近期市场高频 + 未掌握；区分 active_gap / blocked_gap
        all_skills = skill_service.skill_repo.list_all()
        active_gap: list[JdGapSkill] = []
        blocked_entries: list[PrerequisiteBlocked] = []
        for s in all_skills:
            name = s["name"]
            if allowed_skills is not None and name not in allowed_skills:
                continue
            rec = (market or {}).get("skills", {}).get(name) if market else None
            m30 = float(rec.get("freq30") or 0.0) if rec else 0.0
            sig = skill_service._market_signal_value(market, name)
            freq = s.get("jd_frequency") or {}
            has_jd = bool(freq.get("must") or freq.get("plus"))
            has_market = sig > 0 or m30 > 0
            blocked = skill_service.is_blocked(s)
            mastery = skill_service._mastery_for_skill(s)
            if s["status"] in ("not_started", "learning") and (
                has_market or has_jd or blocked
            ):
                gap = JdGapSkill(
                    skill=name,
                    jd_must_count=int(freq.get("must") or 0),
                    jd_plus_count=int(freq.get("plus") or 0),
                    mastery=mastery,
                    blocked=blocked,
                    market_30d=m30 if has_market else None,
                    sample_count=int((market or {}).get("sample_count_30d") or 0),
                )
                active_gap.append(gap)
            if blocked and has_market:
                blocked_entries.append(PrerequisiteBlocked(
                    skill=name,
                    missing=list(skill_service.missing_prerequisites(s)),
                ))
        # 高频优先
        active_gap.sort(key=lambda g: (
            g.blocked, -(g.market_30d or 0.0), g.skill
        ))
        ctx.jd_gap_skills = active_gap[:12]
        ctx.prerequisite_blocked = blocked_entries[:10]

        # 3) 未来 1~2 周学习形状（纯规则预览，不写 tasks）
        if self.jd_service is not None:
            try:
                weekly = self.jd_service.preview_weekly_priorities(
                    plan_date, days=7, top_each_day=3
                )
                ctx.weekly_focus = [
                    WeeklyFocus(date=d["date"], skills=d["skills"])
                    for d in weekly["daily_focus"]
                ]
            except Exception:  # noqa: BLE001 - 预览异常不影响规划
                ctx.weekly_focus = []

    def _fill_knowledge_evidence(
        self, ctx: PlanningContext, plan_date: str
    ) -> None:
        """把 knowledge_points + 最近验收结果整理为 KnowledgeEvidence 列表。"""
        if self.assessment_repo is None:
            return
        from .knowledge_evidence import (
            _attempt_weak_points,
            _latest_judged_attempt,
        )

        topic_names = {
            r["id"]: r["name"]
            for r in self.repo.conn.execute("SELECT id, name FROM study_topics")
        }
        for kp in self.assessment_repo.list_knowledge_points():
            if not kp.get("last_assessed_at"):
                continue  # 没有真实验收证据：不进入 evidence
            if self.scope_tasks_by_route and self._route_id() is not None:
                if kp.get("route_id") != self._route_id():
                    continue  # 只读本路线的掌握证据
            attempt = _latest_judged_attempt(self.assessment_repo, kp["id"])
            tid = kp.get("topic_id")
            topic_id = int(tid) if tid is not None else None
            ctx.knowledge_evidence.append(
                KnowledgeEvidence(
                    knowledge_point_id=kp["id"],
                    name=kp["name"],
                    topic_id=topic_id,
                    topic=topic_names.get(topic_id, "") if topic_id else "",
                    mastery_estimate=float(kp.get("mastery_estimate") or 0.0),
                    weak_points=tuple(_attempt_weak_points(attempt)),
                    last_assessed_at=kp.get("last_assessed_at"),
                    review_count=int(kp.get("review_count") or 0),
                    next_review_date=kp.get("next_review_date"),
                    recent_result_level=(
                        attempt.get("result_level") if attempt else None
                    ),
                )
            )

    def _scoped_tasks(self, tasks: list) -> list:
        """Phase D：route-scoped 实例只保留本路线任务。"""
        if not self.scope_tasks_by_route:
            return tasks
        route_id = self._route_id()
        if route_id is None:
            return tasks
        return [t for t in tasks if t.route_id == route_id]

    def _build_recent_days(self, anchor: str, week: int = WEEK_DAYS) -> list[DaySummary]:
        """anchor 之前 week 天（不含 anchor）的每日摘要，按日期升序。"""
        out: list[DaySummary] = []
        day = add_days(anchor, -week)
        for _ in range(week):
            stats = self.repo.stats_by_date(day)
            # cancelled 是“用户主动移除”，不进入有效任务 / 预计时间 / 完成率
            tasks = [
                t for t in self._scoped_tasks(self.repo.list_by_date(day))
                if t.status != STATUS_CANCELLED
            ]
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
        tasks = self._scoped_tasks(
            self.repo.list_between(start, add_days(anchor, -1))
        )

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

    def generate_next_day_plan(self, date_str: str, force: bool = False) -> dict:
        """为 date_str 的"下一天"生成计划（幂等）。

        :param date_str: 今天的日期
        :param force: True 时忽略“当天已有决策”的幂等保护，强制重新生成
            （供“重新规划今天”使用；仍受本地规则校验）。
        :return: {"date", "created", "fallback", "existing", ...}
        """
        plan_date = add_days(date_str, 1)

        # Phase C：暂停/归档路线不再自动生成新计划
        if not self.study_plan_service.is_planning_enabled():
            return {
                "date": plan_date,
                "existing": False,
                "fallback": False,
                "planning_paused": True,
                "created": [],
            }

        # 幂等：同一天已有计划且当天确实已有任务时，直接返回（不重复生成）。
        # 只存在决策但当天没有任何任务（例如上次因阶段全部完成而生成为空），
        # 则允许重新生成，避免“当天永远空任务”的卡死。
        route_id = self._route_id()
        existing = self.latest_plan_for_date(plan_date, route_id=route_id)
        if not force and existing is not None and \
                self.repo.has_generated_new_on_date(plan_date, route_id):
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

    # ---------- Phase D：route-specific Planner API ----------

    def generate_for_route(
        self,
        plan_date: str,
        max_tasks: int | None = 1,
        force: bool = False,
        max_minutes: int | None = None,
    ) -> dict:
        """只在本路线内选择 Topic 并生成任务（Scheduler 每个 slot 调用）。

        - 严格 route-scoped：valid_topic_ids 只来自本路线当前 phase；
        - max_tasks：本调用最多创建多少个任务；
        - max_minutes：全局剩余分钟；超过的候选不生成（minute_budget_exhausted）；
        - force：True 时不做“当天已有决策”幂等（Scheduler 依赖 budget +
          topic 去重避免重复，不会重复生成同 topic）；
        - 任何 AI/校验失败只 fallback 本路线，绝不抛给 Scheduler。
        """
        route_id = self._route_id()
        result = {
            "route_id": route_id,
            "date": plan_date,
            "existing": False,
            "fallback": False,
            "created": [],
            "created_ids": [],
        }
        if not self.study_plan_service.is_planning_enabled():
            result["planning_paused"] = True
            return result

        existing = self.latest_plan_for_date(plan_date, route_id=route_id)
        if not force and existing is not None and \
                self.repo.has_generated_new_on_date(plan_date, route_id):
            result["existing"] = True
            result["decision_id"] = existing["id"]
            return result

        context = self.build_context(add_days(plan_date, -1))
        if self.planner is None or not self.planner.is_configured():
            return self._fallback_plan_route(
                plan_date, "ai_not_configured", max_tasks, result, context,
                max_minutes,
            )
        try:
            plan = self.planner.plan_next_day(context)
        except AIServiceError:
            return self._fallback_plan_route(
                plan_date, "ai_error", max_tasks, result, context, max_minutes,
            )

        valid, accepted, problems = self._validate_and_create(
            plan, plan_date, max_tasks=max_tasks, max_minutes=max_minutes
        )
        if not valid:
            return self._fallback_plan_route(
                plan_date, "validation_failed", max_tasks, result, context,
                max_minutes,
            )
        self._save_decision(
            date=plan_date,
            phase_id=self._current_phase_id(plan_date),
            context=context,
            plan=plan,
            accepted=accepted,
            source="ai",
        )
        result.update(
            created=[self.repo.get(i) for i in accepted],
            created_ids=list(accepted),
            reasoning=plan.reasoning,
            adjustment=plan.adjustment,
        )
        if not accepted:
            result["skip_reason"] = self._no_candidate_reason(
                plan_date, max_minutes
            )
        return result

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

    def _fallback_plan_route(
        self,
        plan_date: str,
        reason: str,
        max_tasks: int | None,
        result: dict,
        context,
        max_minutes: int | None = None,
    ) -> dict:
        """route-specific fallback：严格使用本路线（不触碰其它路线）。"""
        gen = self.study_plan_service.generate_daily_tasks(
            plan_date, max_tasks=max_tasks, max_minutes=max_minutes
        )
        accepted_ids = [t.id for t in gen.get("generated", [])]
        self._save_decision(
            date=plan_date,
            phase_id=self._current_phase_id(plan_date),
            context=context,
            plan=None,
            accepted=accepted_ids,
            source="fallback_rule",
        )
        result.update(
            fallback=True,
            fallback_reason=reason,
            created_ids=accepted_ids,
            created=[self.repo.get(i) for i in accepted_ids],
        )
        if not accepted_ids:
            if gen.get("skipped_minute_budget"):
                result["skip_reason"] = "minute_budget_exhausted"
            else:
                result["skip_reason"] = self._no_candidate_reason(
                    plan_date, max_minutes
                )
        return result

    def _no_candidate_reason(
        self, plan_date: str, max_minutes: int | None
    ) -> str:
        """无任务生成时区分 minute_budget_exhausted / no_available_topic。"""
        if max_minutes is None:
            return "no_available_topic"
        current_phase = self.study_plan_service.get_current_phase(plan_date)
        if current_phase is None:
            return "no_available_topic"
        occupied = self._occupied_topic_ids(plan_date)
        candidates = [
            t for t in current_phase.topics if t.id not in occupied
        ]
        if candidates and all(
            t.estimated_minutes > max_minutes for t in candidates
        ):
            return "minute_budget_exhausted"
        return "no_available_topic"

    # ---------- 本地校验与创建 ----------

    def _validate_and_create(
        self, plan, plan_date: str, max_tasks: int | None = None,
        max_minutes: int | None = None,
    ):
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
        # 当天任何非 cancelled、带 topic 的任务（含 manual / done / 延期）
        # 都视为该 topic 今日已被占用，Agent 不得重复生成。
        occupied_topic_ids = self._occupied_topic_ids(plan_date)
        # 当天被用户主动移除（cancelled）的 topic：今天 replan 不再重新生成，
        # 但次日不受影响（次日 plan_date 不同，查不到该 cancelled 记录）。
        today_cancelled_topic_ids = self._cancelled_topic_ids(plan_date)
        # Phase 8：高掌握且最近良好 / 已有未完成复习任务 的主题不重复安排
        # （复习交给 ReviewService；此处视为去重，不当作规划失败）
        evidence_skip: set[int] = set()
        if self.assessment_repo is not None:
            from .knowledge_evidence import skip_topic_ids

            evidence_skip = skip_topic_ids(self.repo, self.assessment_repo)

        # Phase C：技能视图（前置 gate；已掌握不重复由 Phase 8 验收证据负责）
        blocked_ids, _ = self.study_plan_service.skill_topic_views(
            current_phase.topics if current_phase is not None else [],
            plan_date,
        )

        # 推荐任务校验（不实际创建，先全部校验）
        to_create: list = []
        seen_topic_ids: set[int] = set()
        topic_by_id: dict[int, object] = {
            t.id: t for t in (current_phase.topics if current_phase else [])
        }
        for rec in plan.recommended_tasks:
            if rec.topic_id not in valid_topic_ids:
                problems.append(f"topic_id {rec.topic_id} 不属于当前阶段")
                continue
            if max_minutes is not None:
                rec_topic = topic_by_id.get(rec.topic_id)
                if rec_topic is not None and \
                        rec_topic.estimated_minutes > max_minutes:
                    # Phase D.1：全局剩余分钟不够 → 跳过（不算规划失败）
                    continue
            if rec.topic_id in done_topic_ids:
                problems.append(f"topic_id {rec.topic_id} 已完成，不应重新生成")
                continue
            if rec.topic_id in blocked_ids:
                # 前置关键技能未满足：即使 JD 高分也不能越级安排
                problems.append(
                    f"topic_id {rec.topic_id} 对应技能前置未满足（gate blocked）"
                )
                continue
            if rec.topic_id in evidence_skip:
                # 已掌握/复习进行中：不生成正式新任务，也不判为规划失败
                continue
            if rec.topic_id in today_cancelled_topic_ids:
                # 今天已移除过该 topic：去重，不重新安排
                continue
            if rec.topic_id in scheduled_topic_ids or rec.topic_id in seen_topic_ids:
                # 已存在/已排过：去重，不算违规
                continue
            if rec.topic_id in occupied_topic_ids:
                # 今天已有该 topic 的任意任务（含 manual/done）：不重复
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

        # Phase D：严格限制每个 slot 最多创建 max_tasks 个任务（含 carry_over）
        if max_tasks is not None:
            to_carry = to_carry[:max(0, int(max_tasks))]
            to_create = to_create[:max(0, int(max_tasks) - len(to_carry))]

        # 任何违规 => 不采纳整份计划（不写库不建任务）
        if problems:
            return False, [], problems

        # 全部合法：先创建推荐任务
        for rec in to_create:
            task = self._create_task_from_recommendation(
                rec, plan_date, topic_by_id=topic_by_id
            )
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

    def _occupied_topic_ids(self, date_str: str) -> set[int]:
        """某天任意非 cancelled、带 topic 的任务（含 manual/done/延期）。"""
        tasks = self.repo.list_by_date(date_str)
        if self.scope_tasks_by_route and self._route_id() is not None:
            tasks = [t for t in tasks if t.route_id == self._route_id()]
        return {
            t.topic_id for t in tasks
            if t.topic_id is not None and t.status != STATUS_CANCELLED
        }

    def _cancelled_topic_ids(self, date_str: str) -> set[int]:
        """某天被用户主动移除（cancelled）主题的 topic_id 集合。

        仅用于阻止“取消 A → replan → 立即又生成 A”，只对当天生效。
        """
        rows = self.repo.conn.execute(
            "SELECT topic_id FROM tasks WHERE scheduled_date = ? AND status = ? "
            "AND topic_id IS NOT NULL",
            (date_str, STATUS_CANCELLED),
        ).fetchall()
        return {r["topic_id"] for r in rows}

    def _create_task_from_recommendation(
        self, rec, plan_date: str, topic_by_id: dict | None = None
    ) -> Task:
        """创建 AI 推荐的任务；description 若无执行性则升级为结构化学习内容。"""
        from .task_content import build_topic_task_content, has_actionable_content

        if not has_actionable_content(rec.description):
            topic = (topic_by_id or {}).get(rec.topic_id)
            if topic is not None:
                content = build_topic_task_content(topic.name, topic.description)
            else:
                content = build_topic_task_content(rec.title)
        else:
            content = rec.description
        task = self.repo.create(
            title=rec.title,
            scheduled_date=plan_date,
            description=content,
            category="学习",
            estimated_minutes=rec.estimated_minutes,
            priority=rec.priority,
            source="generated",
            topic_id=rec.topic_id,
        )
        # 与 fallback path 复用同一个 topic -> knowledge_point 关联实现
        topic = (topic_by_id or {}).get(rec.topic_id)
        return self.study_plan_service.link_task_knowledge_point(task, topic)

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
        route_id = None
        try:
            route_id = self.study_plan_service._resolved_route_id()
        except Exception:  # noqa: BLE001 - 解析失败保持 NULL，不阻塞规划
            route_id = None
        self.decision_repo.create(
            date=date,
            current_phase_id=phase_id,
            input_context=ctx_json,
            ai_response=plan_json,
            accepted_tasks=json.dumps(accepted, ensure_ascii=False),
            source=source,
            route_id=route_id,
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


def _skill_reason(d: dict) -> str:
    """把 SkillService 的评分明细渲染成可读的优先级理由。"""
    parts = []
    freq = d.get("jd_frequency") or {}
    if freq.get("must"):
        parts.append(f"JD must×{freq['must']}")
    if freq.get("plus"):
        parts.append(f"JD plus×{freq['plus']}")
    mastery = d.get("mastery_estimate")
    parts.append(
        f"mastery={mastery:.2f}" if mastery is not None else "未验收"
    )
    if d.get("gate") == "blocked":
        parts.append("前置未满足")
    suffix = "；".join(parts) if parts else "基础优先级"
    return f"{d.get('tier', '')}级；{suffix}"
