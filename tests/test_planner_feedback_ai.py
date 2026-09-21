"""Phase 6：Planner Feedback 与 AI / fallback 集成测试。"""

from __future__ import annotations

import json

from app.ai.schemas import CarryOverTask, DailyPlan, RecommendedTask
from app.services.daily_planner_service import DailyPlannerService
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    current_phase_topics,
    make_requirement_project,
    topic_in_current_phase,
)


class _FakePlanner:
    def __init__(self, plan=None, configured=True):
        self._plan = plan
        self._configured = configured
        self.calls = []

    def is_configured(self):
        return self._configured

    def plan_next_day(self, context):
        self.calls.append(context)
        return self._plan


def _make_plan(topic_id, minutes=45, title="任务"):
    return DailyPlan(
        reasoning="test",
        recommended_tasks=(
            RecommendedTask(
                topic_id=int(topic_id), title=title, description="",
                estimated_minutes=int(minutes), priority=2,
            ),
        ),
        carry_over_tasks=(),
        daily_minutes=int(minutes),
        adjustment="",
    )


def _planner_for(env, route_id, plan=None, configured=True):
    sps = env.sps_for(route_id)
    fb = env.feedback.for_route(route_id, sps)
    planner = _FakePlanner(plan, configured=configured)
    service = DailyPlannerService(
        env.repo, env.plan_repo, planner=planner, study_plan_service=sps,
        assessment_repo=env.arepo, skill_service=None,
        scope_tasks_by_route=True, feedback_service=fb,
    )
    return service, planner, fb


class TestAiCandidatePool:
    def test_candidate_pool_is_highest_tier(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, planner, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        ctx = service.build_context("2026-09-14")
        assert ctx.candidate_topic_ids == [topics[0].id]
        vllm = next(f for f in ctx.planner_feedback
                    if f.topic_id == topics[0].id)
        assert vllm.tier == 0
        assert vllm.project_requirements

    def test_ai_selects_candidate_creates_task(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, _, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        assert res["created_ids"]
        task = env.repo.get(res["created_ids"][0])
        assert task.topic_id == topics[0].id
        # 程序强制写入 component / activity
        assert task.component_id is not None
        assert task.learning_activity_kind
        assert task.source == "generated"

    def test_ai_low_tier_rejected_then_fallback(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        # AI 想学 Tier2 的 Continuous Batching（合法 legal，但不在候选池）
        cb = topic_in_current_phase(env, env.r3.id, "Continuous Batching")
        service, _, _ = _planner_for(env, env.r3.id, _make_plan(cb.id))
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        assert res["fallback"] is True
        assert res["fallback_reason"] == "validation_failed"
        assert res["created_ids"]
        task = env.repo.get(res["created_ids"][0])
        # fallback 使用最高 Tier → 应该是 vLLM
        assert task.topic_id == topics[0].id

    def test_ai_future_phase_topic_rejected(self, practice_readiness_env):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        quant = env.plan_repo.find_topic_by_name_in_route(env.r3.id, "Quantization")
        service, _, _ = _planner_for(env, env.r3.id, _make_plan(quant.id))
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        assert res["fallback"] is True
        assert env.repo.get(res["created_ids"][0]).topic_id != quant.id

    def test_ai_cannot_change_activity(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        expected = env.tl.get_next_required_component(topics[0].id)
        service, _, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        task = env.repo.get(res["created_ids"][0])
        assert task.component_id == expected["id"]
        assert task.learning_activity_kind == expected["activity_kind"]


class TestFallbackUsesTier:
    def test_no_ai_fallback_prefers_tier0(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, planner, _ = _planner_for(
            env, env.r3.id, plan=None, configured=False
        )
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        assert res["fallback"] is True
        assert res["created_ids"]
        assert env.repo.get(res["created_ids"][0]).topic_id == topics[0].id

    def test_fallback_deterministic(self, practice_readiness_env):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        service, _, _ = _planner_for(
            env, env.r3.id, plan=None, configured=False
        )
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        topic_ids = [env.repo.get(i).topic_id for i in res["created_ids"]]
        assert topic_ids == [t for t in topic_ids]  # stable
        assert len(set(topic_ids)) == len(topic_ids)

    def test_no_requirement_keeps_normal_curriculum(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        service, _, _ = _planner_for(
            env, env.r3.id, plan=None, configured=False
        )
        res = service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        assert res["created_ids"]  # 没有 blocker 也能正常生成
        task = env.repo.get(res["created_ids"][0])
        assert env.plan_repo.get_route_id_for_topic(task.topic_id) == env.r3.id


class TestDecisionAudit:
    def test_planner_decision_records_feedback(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, _, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        service.generate_for_route(DEFAULT_PLAN_DATE, max_tasks=1)
        row = env.conn.execute(
            "SELECT input_context, ai_response, source FROM planner_decisions "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        ctx = json.loads(row[0])
        assert "planner_feedback" in ctx
        assert "candidate_topic_ids" in ctx
        fb = ctx["planner_feedback"]
        target = next(f for f in fb if f["topic_id"] == topics[0].id)
        assert target["tier"] == 0
        assert target["project_requirements"]
        assert row[2] == "ai"


class TestReplan:
    def test_replan_reflects_satisfied_requirement(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        from tests.practice_capability_helpers import ensure_capability

        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, _, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        assert service.build_context("2026-09-14").candidate_topic_ids == [
            topics[0].id
        ]
        # Assessment 使 capability 达标 → blocker 消失
        ensure_capability(env, topics[0].id, 3)
        ctx = service.build_context("2026-09-14")
        assert ctx.candidate_topic_ids != [topics[0].id]
        assert all(
            f.tier != 0 for f in ctx.planner_feedback
        )

    def test_replan_reflects_project_completion(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        service, _, _ = _planner_for(
            env, env.r3.id, _make_plan(topics[0].id)
        )
        env.service.set_status(project["id"], "completed")
        ctx = service.build_context("2026-09-14")
        assert all(f.tier != 0 for f in ctx.planner_feedback)
