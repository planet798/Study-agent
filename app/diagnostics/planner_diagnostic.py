"""Planner Diagnostic（v1 stabilization，只读）。

回答 dogfooding 的核心问题：“今天为什么安排这个？”

输出每条 active 学习路线的**确定性** planner 状态：
  Route / priority / planning_enabled
  → Current Phase
  → Legal Topics（next activity / Tier / reasons / market factor / practice blockers）
  → Final candidate_topic_ids

严格只读、且不做任何副作用：
- 不创建 Task；
- 不写 planner_decisions；
- 不调用 AI；
- 不输出任何 secret / private URI / Output details。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..database.assessment_repository import AssessmentRepository
from ..database.capability_repository import CapabilityEvidenceRepository
from ..database.jd_summary_repository import (
    JdDailySummaryRepository,
    JdSkillCandidateRepository,
)
from ..database.learning_route_repository import LearningRouteRepository
from ..database.repository import TaskRepository
from ..database.skill_repository import SkillRepository
from ..database.study_plan_repository import StudyPlanRepository
from ..database.topic_learning_repository import TopicLearningComponentRepository
from ..services.capability_service import CapabilityService
from ..services.jd_summary_service import JdSummaryService
from ..services.market_signal import MarketSignal
from ..services.planner_feedback import (
    TIER_LABELS,
)
from ..services.skill_service import SkillService
from ..services.study_plan_service import StudyPlanService
from ..services.topic_learning_profile_service import (
    TopicLearningProfileService,
)
from ..utils.date_utils import today as _today


def build_planner_diagnostic(
    conn: sqlite3.Connection, plan_date: Optional[str] = None
) -> dict:
    """构建只读诊断所需的 service 集合（与生产 wiring 对齐，但不含 AI）。"""
    plan_date = plan_date or _today()
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    assessment_repo = AssessmentRepository(conn)
    tl = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    skill_service = SkillService(
        skill_repo, plan_repo=plan_repo, assessment_repo=assessment_repo
    )
    skill_service.topic_learning_service = tl
    skill_service.route_repo = route_repo
    # 近 30 天市场信号（只读；无样本时 market_factor 回退 individual JD）
    try:
        jd_summary = JdSummaryService(
            JdDailySummaryRepository(conn), skill_repo,
            candidate_repo=JdSkillCandidateRepository(conn),
            route_repo=route_repo,
        )
        skill_service.market_signal = MarketSignal(jd_summary)
        # 只读预计算市场信号（不调用 refresh_coverage，避免任何写入）
        market = skill_service.market_signal.compute(plan_date)
        skill_service._market = market
        skill_service._prereq_boost = skill_service._compute_prereq_boost(market)
    except Exception:  # noqa: BLE001 - 市场数据异常不影响诊断
        pass

    cap = CapabilityService(conn, CapabilityEvidenceRepository(conn))
    return {
        "conn": conn, "repo": repo, "plan_repo": plan_repo,
        "route_repo": route_repo, "skill_repo": skill_repo,
        "assessment_repo": assessment_repo, "tl": tl,
        "skill_service": skill_service, "cap": cap,
    }


def _route_scoped_sps(ctx: dict, route_id: int) -> StudyPlanService:
    """只读 route-scoped StudyPlanService。

    有意不注入 skill_service，使 skill_topic_views 不会触发 refresh_market /
    refresh_coverage 写入；prerequisite gate 由本模块用只读方式计算。
    """
    return StudyPlanService(
        ctx["repo"], ctx["plan_repo"], route_id=route_id,
        learning_route_repo=ctx["route_repo"], scope_tasks_by_route=True,
        assessment_repo=ctx["assessment_repo"],
        topic_learning_service=ctx["tl"],
    )


def _readonly_blocked_provider(ctx: dict, sps: StudyPlanService):
    """只读 prerequisite gate：不调用任何写方法。"""
    skill_service = ctx["skill_service"]

    def provider(route_id, plan_date):
        try:
            phase = sps.get_current_phase(plan_date)
            if phase is None:
                return set()
            blocked: set[int] = set()
            for t in phase.topics:
                for name in skill_service.skills_for_topic(t.id):
                    skill = ctx["skill_repo"].get_by_name(name)
                    if skill is not None and skill_service.is_blocked(skill):
                        blocked.add(int(t.id))
                        break
            return blocked
        except Exception:  # noqa: BLE001
            return set()

    return provider


def diagnose_route(ctx: dict, route, plan_date: str) -> dict:
    """单条路线的只读诊断结果。"""
    from ..database.practice_repository import PracticeRequirementRepository
    from ..services.planner_feedback import PlannerFeedbackService
    from ..services.practice_readiness import PracticeReadinessService

    sps = _route_scoped_sps(ctx, route.id)
    phase = sps.get_current_phase(plan_date)
    legal_topics = list(getattr(phase, "topics", None) or []) if phase else []
    readiness = PracticeReadinessService(
        ctx["conn"],
        requirement_repo=PracticeRequirementRepository(ctx["conn"]),
        plan_repo=ctx["plan_repo"], route_repo=ctx["route_repo"],
        capability_repo=ctx["cap"].repo,
        topic_learning_service=ctx["tl"], task_repo=ctx["repo"],
        current_phase_provider=lambda rid, d: sps.get_current_phase(d),
        blocked_topic_provider=_readonly_blocked_provider(ctx, sps),
    )
    feedback = PlannerFeedbackService(
        ctx["conn"], readiness_service=readiness,
        plan_repo=ctx["plan_repo"], route_repo=ctx["route_repo"],
        skill_service=ctx["skill_service"], task_repo=ctx["repo"],
        refresh_market_on_rank=False,
    )
    signals = feedback.rank_available_topics(
        route.id, plan_date, legal_topics
    ) if legal_topics else []
    pool = feedback.highest_tier_pool(signals)

    topics_out = []
    for s in signals:
        topic = ctx["plan_repo"].get_topic(s.topic_id)
        topics_out.append({
            "topic_id": s.topic_id,
            "name": getattr(topic, "name", ""),
            "next_activity": s.next_activity_kind,
            "next_activity_label": s.next_activity_label,
            "tier": s.tier,
            "tier_label": TIER_LABELS.get(s.tier, ""),
            "reasons": list(s.priority_reasons),
            "market_factor": round(float(s.market_factor), 4),
            "blocker_count": s.active_project_blocker_count,
            "capability_gap": s.max_capability_gap,
            "practice_requirements": list(s.practice_requirements),
        })

    return {
        "route_id": route.id,
        "route_name": route.name,
        "route_key": getattr(route, "route_key", None),
        "priority": int(route.priority or 0),
        "planning_enabled": bool(route.planning_enabled),
        "archived": bool(route.is_archived),
        "current_phase": phase.name if phase else None,
        "legal_topic_count": len(legal_topics),
        "topics": topics_out,
        "candidate_topic_ids": [s.topic_id for s in pool],
    }


def run_planner_diagnostic(
    conn: sqlite3.Connection,
    plan_date: Optional[str] = None,
    route_key: Optional[str] = None,
) -> dict:
    """对每条 active learning route 生成只读诊断（绝不写库 / 调 AI）。"""
    plan_date = plan_date or _today()
    ctx = build_planner_diagnostic(conn, plan_date)
    routes = [
        r for r in ctx["route_repo"].list_learning_routes()
        if not r.is_archived
    ]
    if route_key:
        routes = [
            r for r in ctx["route_repo"].list_learning_routes()
            if (r.route_key or "") == route_key
        ]
    return {
        "plan_date": plan_date,
        "routes": [diagnose_route(ctx, r, plan_date) for r in routes],
    }


def format_planner_diagnostic(data: dict) -> str:
    """人类可读输出（不包含 secret / URI / Output details）。"""
    lines = [f"Planner Diagnostic · {data.get('plan_date')}"]
    for r in data.get("routes", []):
        lines.append("")
        lines.append(f"■ {r['route_name']} ({r.get('route_key') or '—'})")
        lines.append(
            f"  Priority: {r['priority']}"
            f"　planning_enabled: {r['planning_enabled']}"
        )
        lines.append(f"  Current Phase: {r['current_phase'] or '（无）'}")
        if not r["topics"]:
            lines.append("  Legal Topics: （无）")
        else:
            lines.append("  Legal Topics:")
            for t in r["topics"]:
                lines.append(
                    f"    - {t['name']}"
                    f"　next={t['next_activity'] or '—'}"
                    f"　tier={t['tier']} {t['tier_label']}"
                )
                for reason in t["reasons"]:
                    lines.append(f"        reason: {reason}")
                if t["market_factor"]:
                    lines.append(
                        f"        market_factor: {t['market_factor']}"
                    )
        cand = r["candidate_topic_ids"] or []
        names = [
            t["name"] for t in r["topics"] if t["topic_id"] in set(cand)
        ]
        lines.append(f"  Candidates: {names or '（无）'}")
    lines.append("")
    lines.append("（只读诊断：未创建任务 / 未写 planner_decisions / 未调用 AI）")
    return "\n".join(lines)
