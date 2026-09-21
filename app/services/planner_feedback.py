"""PlannerFeedbackService（Phase 6）。

职责边界（§78）：
- StudyPlan / existing route planner：决定哪些 Topic 当前 legal（Phase / gate / 去重）；
- TopicLearningProfileService：决定 next component；
- PracticeReadinessService：决定项目 capability gap / blocker；
- **本 Service 只对已经 legal 的 Topic 排序**，不重复实现 prerequisite。

排序采用可解释的离散 Tier（§25）：
    TIER 0 ACTIONABLE_PRACTICE_BLOCKER（最高）
    TIER 1 MARKET_SIGNAL_TOPIC
    TIER 2 NORMAL_CURRICULUM

硬 gate 永远优先：调用方只传 legal topics；本 Service 不改变合法性。
不写数据库（Tier 是当前状态的派生信号，不持久化）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from ..database.learning_route_repository import LearningRouteRepository
from ..database.study_plan_repository import StudyPlanRepository
from ..utils.date_utils import today as _today
from .practice_readiness import PracticeReadinessService

TIER_PRACTICE_BLOCKER = 0
TIER_MARKET_SIGNAL = 1
TIER_NORMAL = 2

TIER_LABELS: dict[int, str] = {
    TIER_PRACTICE_BLOCKER: "practice_blocker",
    TIER_MARKET_SIGNAL: "market_signal",
    TIER_NORMAL: "normal_curriculum",
}

_NO_DATE = "0000-00-00"


@dataclass
class PlannerTopicSignal:
    topic_id: int
    tier: int
    next_component_id: int | None = None
    next_activity_kind: str = ""
    next_activity_label: str = ""
    priority_reasons: list[str] = field(default_factory=list)
    active_project_blocker_count: int = 0
    max_capability_gap: int = 0
    market_factor: float = 0.0
    skill_priority: float = 0.0
    last_learning_at: str = _NO_DATE
    topic_order: int = 0
    stable_rank: tuple = ()
    # 紧凑的项目需求信息（仅用于 prompt / UI，不含 Output 内容）
    practice_requirements: list[dict] = field(default_factory=list)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, "normal_curriculum")

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "next_component_id": self.next_component_id,
            "next_activity_kind": self.next_activity_kind,
            "next_activity_label": self.next_activity_label,
            "priority_reasons": list(self.priority_reasons),
            "active_project_blocker_count": self.active_project_blocker_count,
            "max_capability_gap": self.max_capability_gap,
            "market_factor": round(float(self.market_factor), 6),
            "skill_priority": round(float(self.skill_priority), 6),
            "last_learning_at": self.last_learning_at,
            "topic_order": self.topic_order,
            "practice_requirements": list(self.practice_requirements),
        }


class PlannerFeedbackService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        readiness_service: PracticeReadinessService | None = None,
        plan_repo: StudyPlanRepository | None = None,
        route_repo: LearningRouteRepository | None = None,
        skill_service=None,
        task_repo=None,
        refresh_market_on_rank: bool = True,
    ):
        self.conn = conn
        self.readiness = readiness_service or PracticeReadinessService(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)
        self.route_repo = route_repo or LearningRouteRepository(conn)
        self.skill_service = skill_service
        self.task_repo = task_repo
        # 只读诊断场景置 False：不触发 skill_service.refresh_market（会写 coverage）
        self.refresh_market_on_rank = bool(refresh_market_on_rank)

    def _blocked_provider_for(self, study_plan_service):
        def provider(route_id, plan_date):
            try:
                phase = study_plan_service.get_current_phase(plan_date)
                if phase is None:
                    return set()
                blocked, _ = study_plan_service.skill_topic_views(
                    list(phase.topics), plan_date
                )
                return {int(x) for x in blocked}
            except Exception:  # noqa: BLE001
                return set()
        return provider

    def for_route(self, route_id: int, study_plan_service):
        """返回绑定到某 route 的 StudyPlanService 的 feedback 实例。

        readiness 需要 route-scoped 的 current phase 与 prerequisite gate，
        因此不能复用全局 plan_repo 的解析。其它配置（skill/plan/route）共享。
        """
        readiness = PracticeReadinessService(
            self.conn,
            requirement_repo=self.readiness.requirements,
            project_repo=self.readiness.projects,
            plan_repo=self.readiness.plan_repo,
            route_repo=self.readiness.route_repo,
            capability_repo=self.readiness.capability_repo,
            topic_learning_service=getattr(
                study_plan_service, "_tl", lambda: None
            )(),
            task_repo=self.readiness.task_repo,
            current_phase_provider=(
                lambda rid, d, _sps=study_plan_service: _sps.get_current_phase(d)
            ),
            blocked_topic_provider=self._blocked_provider_for(study_plan_service),
        )
        return PlannerFeedbackService(
            self.conn,
            readiness_service=readiness,
            plan_repo=self.plan_repo,
            route_repo=self.route_repo,
            skill_service=self.skill_service,
            task_repo=self.task_repo,
            refresh_market_on_rank=self.refresh_market_on_rank,
        )

    # ================= 学习时间 =================

    def last_meaningful_learning_at(self, topic_id: int) -> str:
        """最近一次“正式学习任务”完成的日期；只算 formal new task，不含 Review/todo。"""
        row = self.conn.execute(
            "SELECT MAX(scheduled_date) FROM tasks WHERE topic_id = ? "
            "AND task_type = 'new' AND source IN ('generated','manual') "
            "AND status = 'done'",
            (int(topic_id),),
        ).fetchone()
        return (row[0] if row and row[0] else _NO_DATE)

    # ================= market / skill =================

    def _route_skill_names(self, route_id: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if self.skill_service is None:
            return out
        try:
            ids = self.route_repo.list_skill_ids(route_id)
        except Exception:  # noqa: BLE001
            return out
        for sid in ids:
            try:
                skill = self.skill_service.skill_repo.get(sid)
            except Exception:  # noqa: BLE001
                skill = None
            if skill is not None:
                out[skill["name"]] = skill
        return out

    def _market_for_topic(self, topic_id: int, route_skills: dict) -> tuple[float, float]:
        """返回 (market_factor, skill_priority)。只取本 route 绑定的技能。"""
        if self.skill_service is None or not route_skills:
            return 0.0, 0.0
        try:
            names = self.skill_service.skills_for_topic(int(topic_id))
        except Exception:  # noqa: BLE001
            return 0.0, 0.0
        best_market = 0.0
        best_priority = 0.0
        for name in names:
            skill = route_skills.get(name)
            if skill is None:
                continue
            try:
                mf = float(self.skill_service.market_factor(skill) or 0.0)
            except Exception:  # noqa: BLE001
                mf = 0.0
            best_market = max(best_market, mf)
            try:
                best_priority = max(
                    best_priority,
                    float((skill.get("jd_frequency") or {}).get("must") or 0),
                )
            except Exception:  # noqa: BLE001
                pass
        return best_market, best_priority

    def _refresh_market(self, plan_date: str) -> None:
        if not self.refresh_market_on_rank:
            return
        if self.skill_service is None:
            return
        try:
            self.skill_service.refresh_market(plan_date)
        except Exception:  # noqa: BLE001
            pass

    # ================= 主排序 =================

    def rank_available_topics(
        self, route_id: int, plan_date: str, legal_topics: list
    ) -> list[PlannerTopicSignal]:
        """对 legal topics 排序（deterministic）。不改变合法性、不写库。"""
        self._refresh_market(plan_date)
        route_skills = self._route_skill_names(route_id)

        # 该 route 的 actionable blockers，按 topic 归组
        blockers_by_topic: dict[int, list] = {}
        try:
            blockers = self.readiness.list_actionable_blockers_by_route(
                route_id, plan_date
            )
        except Exception:  # noqa: BLE001
            blockers = []
        for st in blockers:
            blockers_by_topic.setdefault(int(st.topic_id), []).append(st)

        signals: list[PlannerTopicSignal] = []
        for topic in legal_topics:
            tid = int(getattr(topic, "id", topic))
            order = int(getattr(topic, "order_index", 0) or 0)
            topic_blockers = blockers_by_topic.get(tid, [])
            market, skill_priority = self._market_for_topic(tid, route_skills)
            last_learning = self.last_meaningful_learning_at(tid)

            component_id = None
            activity_kind = ""
            activity_label = ""
            reasons: list[str] = []
            requirements: list[dict] = []
            if topic_blockers:
                st = topic_blockers[0]
                component_id = st.next_component_id
                activity_kind = st.next_activity_kind
                activity_label = st.next_activity_label
                for s in topic_blockers:
                    reasons.append(
                        f"项目需求 · {s.project_name}："
                        f"{s.current_capability_label} → {s.target_capability_label}"
                    )
                    requirements.append({
                        "project_name": s.project_name,
                        "current_capability": s.current_capability_label,
                        "target_capability": s.target_capability_label,
                        "capability_gap": s.capability_gap,
                        "next_activity": s.next_activity_label,
                        "note": s.note,
                    })
            else:
                try:
                    comp = self.readiness.topic_learning_service.get_next_required_component(
                        tid
                    ) if self.readiness.topic_learning_service else None
                except Exception:  # noqa: BLE001
                    comp = None
                if comp is not None:
                    component_id = int(comp["id"])
                    activity_kind = comp["activity_kind"]
                    from .learning_activity import activity_label as _al

                    activity_label = _al(activity_kind)

            if topic_blockers:
                tier = TIER_PRACTICE_BLOCKER
            elif market > 0:
                tier = TIER_MARKET_SIGNAL
                reasons.append("近期目标岗位样本需求")
            else:
                tier = TIER_NORMAL

            blocker_count = len(topic_blockers)
            max_gap = max((s.capability_gap for s in topic_blockers), default=0)
            # §31 确定性排序键：tier 优先；Tier0 内 blocker_count / gap；
            # 之后 market factor、最近学习时间（越久远越先）、topic 顺序、id
            rank = (
                int(tier),
                -int(blocker_count),
                -int(max_gap),
                -round(float(market), 6),
                last_learning,
                order,
                tid,
            )
            signals.append(PlannerTopicSignal(
                topic_id=tid,
                tier=tier,
                next_component_id=component_id,
                next_activity_kind=activity_kind,
                next_activity_label=activity_label,
                priority_reasons=reasons,
                active_project_blocker_count=blocker_count,
                max_capability_gap=max_gap,
                market_factor=market,
                skill_priority=skill_priority,
                last_learning_at=last_learning,
                topic_order=order,
                stable_rank=rank,
                practice_requirements=requirements,
            ))

        signals.sort(key=lambda s: s.stable_rank)
        return signals

    @staticmethod
    def highest_tier_pool(
        signals: list[PlannerTopicSignal]
    ) -> list[PlannerTopicSignal]:
        """最高非空 Tier 的候选 Topic（AI 本轮只能从中选择）。"""
        if not signals:
            return []
        best = min(s.tier for s in signals)
        return [s for s in signals if s.tier == best]

    def candidate_pool(
        self, route_id: int, plan_date: str, legal_topics: list
    ) -> list[PlannerTopicSignal]:
        return self.highest_tier_pool(
            self.rank_available_topics(route_id, plan_date, legal_topics)
        )
