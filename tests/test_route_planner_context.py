"""Phase D：route-specific Planner / PlanningContext 隔离。

覆盖需求 46 的 24~32、42：
- RL context 不含搜广推 topic（RAG/Ranking）；
- 默认路线 context 不含 RL topic（MDP/PPO）；
- 非默认 route 无 route_skills 时不灌入 JD/market/skill；
- default route 保留 skill context；
- route_skills 严格 scoped；
- fallback route-aware；AI failure 只影响当前 route。
"""

from __future__ import annotations

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_scheduler import GlobalDailyScheduler
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-09-15"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    skill_service = SkillService(skill_repo, plan_repo=plan_repo,
                                 assessment_repo=arepo)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           skill_service=skill_service,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "skill_repo": skill_repo, "skill_service": skill_service,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "sps": sps,
        "default": route_repo.get_default_learning_route(),
    }


def _add_route(env, name, topics, priority=5, planning_enabled=True):
    rl = env["route_service"].create_learning_route(
        name, priority=priority, planning_enabled=planning_enabled
    )
    plan = env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    for i, t in enumerate(topics):
        env["route_plan"].add_topic(rl.id, phase.id, t, order_index=i)
    return rl, plan, phase


def _route_planner(env, route_id, planner=None):
    sps = StudyPlanService(
        env["repo"], env["plan_repo"], assessment_repo=env["arepo"],
        skill_service=env["skill_service"], route_id=route_id,
        learning_route_repo=env["route_repo"], scope_tasks_by_route=True,
    )
    return DailyPlannerService(
        env["repo"], env["plan_repo"], planner=planner, study_plan_service=sps,
        assessment_repo=env["arepo"], skill_service=env["skill_service"],
        scope_tasks_by_route=True,
    )


class _FakePlanner:
    def __init__(self, topic_id=None, error=None, configured=True):
        self.topic_id = topic_id
        self.error = error
        self.configured = configured
        self.inputs = []

    def is_configured(self):
        return self.configured

    def plan_next_day(self, context):
        self.inputs.append(context)
        if self.error is not None:
            raise self.error
        return DailyPlan(
            reasoning="r",
            recommended_tasks=(
                RecommendedTask(topic_id=self.topic_id, title="t",
                                estimated_minutes=30, priority=2),
            ),
            carry_over_tasks=(), daily_minutes=30, adjustment="a",
        )


# ================= 24~25：上下文隔离 =================

class TestContextIsolation:
    def test_rl_context_excludes_default_topics(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP", "Policy", "PPO"])
        planner = _route_planner(env, rl.id)
        ctx = planner.build_context(TODAY)
        titles = [t.title for t in ctx.available_topics]
        assert titles == ["MDP", "Policy", "PPO"]
        assert ctx.route_id == rl.id
        assert ctx.route_name == "强化学习"
        assert "RAG 全流程搭建" not in titles
        assert "Ranking 基础" not in titles

    def test_default_context_excludes_rl_topics(self, env):
        _add_route(env, "强化学习", ["MDP", "PPO"])
        planner = _route_planner(env, env["default"].id)
        ctx = planner.build_context(TODAY)
        titles = [t.title for t in ctx.available_topics]
        assert "MDP" not in titles and "PPO" not in titles
        assert ctx.route_name == env["default"].name

    def test_route_planner_generates_only_own_topics(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP", "Policy"])
        planner = _route_planner(env, rl.id)
        res = planner.generate_for_route(TODAY, max_tasks=2, force=True)
        assert res["created_ids"]
        for task in res["created"]:
            assert task.route_id == rl.id
            assert task.source == "generated" and task.task_type == "new"


# ================= 26~28：skill / market scope =================

class TestSkillScope:
    def test_non_default_without_route_skills_has_no_skill_context(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP"])
        # 建一个全局 skill（不绑定到 RL route）
        env["skill_repo"].create("Ranking", tier="S", status="learning")
        env["skill_service"].recompute_all_priority_scores()
        planner = _route_planner(env, rl.id)
        assert planner._allowed_skill_names() == set()
        ctx = planner.build_context(TODAY)
        assert ctx.skill_priorities == []
        assert ctx.market_trends == []
        assert ctx.jd_gap_skills == []

    def test_default_route_keeps_legacy_skill_context(self, env):
        # 默认路线且无显式 route_skills 绑定 → 不做限制（保留旧行为）
        env["skill_repo"].create("Ranking", tier="S", status="learning")
        env["skill_service"].recompute_all_priority_scores()
        planner = _route_planner(env, env["default"].id)
        assert planner._allowed_skill_names() is None

    def test_route_skills_scoped_filtering(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP"])
        ppo = env["skill_repo"].create("PPO", tier="S", status="learning")
        ranking = env["skill_repo"].create("Ranking", tier="S", status="learning")
        env["route_repo"].assign_skill(rl.id, ppo["id"])
        planner = _route_planner(env, rl.id)
        allowed = planner._allowed_skill_names()
        assert allowed == {"PPO"}
        assert "Ranking" not in allowed

    def test_no_route_skills_still_plannable(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP", "Policy"])
        planner = _route_planner(env, rl.id)  # planner=None -> fallback rule
        res = planner.generate_for_route(TODAY, max_tasks=1, force=True)
        assert len(res["created_ids"]) == 1


# ================= 29~32：fallback / AI failure =================

class TestFallbackAndFailure:
    def test_fallback_route_aware(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP"])
        planner = _route_planner(env, rl.id)  # AI 未配置
        res = planner.generate_for_route(TODAY, max_tasks=1, force=True)
        assert res["fallback"] is True
        task = env["repo"].get(res["created_ids"][0])
        assert task.route_id == rl.id
        assert task.title == "MDP"

    def test_ai_error_falls_back_single_route(self, env):
        rl, plan, phase = _add_route(env, "强化学习", ["MDP"])
        fake = _FakePlanner(error=AIServiceError("boom"))
        planner = _route_planner(env, rl.id, planner=fake)
        res = planner.generate_for_route(TODAY, max_tasks=1, force=True)
        assert res["fallback"] is True
        assert res["fallback_reason"] == "ai_error"
        assert env["repo"].get(res["created_ids"][0]).route_id == rl.id

    def test_ai_failure_does_not_abort_other_routes(self, env):
        rl, _, _ = _add_route(env, "强化学习", ["MDP"])
        ds, _, _ = _add_route(env, "数据结构与算法", ["Hash", "Tree"])
        fake = _FakePlanner(error=AIServiceError("boom"))
        sched = GlobalDailyScheduler(
            env["repo"], env["plan_repo"], env["route_repo"],
            assessment_repo=env["arepo"], skill_service=env["skill_service"],
            planner=fake, budget=3,
        )
        out = sched.generate(TODAY)
        routes = {env["repo"].get(i).route_id for i in out["created_ids"]}
        assert rl.id in routes  # RL 走 fallback
        assert ds.id in routes  # DS 也照常生成（不受 RL 影响）
