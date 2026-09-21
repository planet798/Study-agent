"""Phase 6：PlannerFeedbackService（Tier 排序）测试。"""

from __future__ import annotations

import pytest

from app.services.planner_feedback import (
    TIER_MARKET_SIGNAL,
    TIER_NORMAL,
    TIER_PRACTICE_BLOCKER,
    PlannerFeedbackService,
)
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    current_phase_topics,
    ensure_capability,
    make_requirement_project,
    mark_component_done,
    topic_in_current_phase,
)


class _StubSkillService:
    """最小 skill adapter：只为 market Tier 提供可预测输入。"""

    def __init__(self, skill_repo, topic_skills, market):
        self.skill_repo = skill_repo
        self._topic_skills = topic_skills
        self._market = market

    def skills_for_topic(self, topic_id):
        return list(self._topic_skills.get(int(topic_id), []))

    def market_factor(self, skill):
        return float(self._market.get(skill["name"], 0.0))

    def refresh_market(self, plan_date):
        return {}


def _rank(env, route_id=3, plan_date=DEFAULT_PLAN_DATE):
    route = {1: env.r1, 2: env.r2, 3: env.r3, 4: env.r4, 5: env.r5,
             6: env.r6}[route_id]
    topics = current_phase_topics(env, route.id, plan_date)
    return env.feedback.rank_available_topics(route.id, plan_date, topics)


class TestTiers:
    def test_tier0_practice_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        signals = _rank(env)
        vllm = next(s for s in signals if s.topic_id == topics[0].id)
        assert vllm.tier == TIER_PRACTICE_BLOCKER
        assert vllm.active_project_blocker_count == 1
        assert vllm.priority_reasons
        assert vllm.next_activity_kind
        # 其它 topic 仍为 normal
        others = [s for s in signals if s.topic_id != topics[0].id]
        assert others and all(s.tier == TIER_NORMAL for s in others)

    def test_tier1_market_signal(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        route_skill_ids = env.route_repo.list_skill_ids(env.r3.id)
        if not route_skill_ids:
            pytest.skip("no route skills")
        skill = env.skill_repo.get(route_skill_ids[0])
        stub = _StubSkillService(
            env.skill_repo, {vllm.id: [skill["name"]]},
            {skill["name"]: 0.8},
        )
        fb = PlannerFeedbackService(
            env.conn, readiness_service=env.readiness,
            plan_repo=env.plan_repo, route_repo=env.route_repo,
            skill_service=stub, task_repo=env.repo,
        )
        topics = current_phase_topics(env, env.r3.id)
        signals = fb.rank_available_topics(env.r3.id, DEFAULT_PLAN_DATE, topics)
        v = next(s for s in signals if s.topic_id == vllm.id)
        assert v.tier == TIER_MARKET_SIGNAL
        assert v.market_factor == 0.8
        assert any("岗位样本" in r for r in v.priority_reasons)

    def test_tier2_normal(self, practice_readiness_env):
        env = practice_readiness_env
        signals = _rank(env)
        assert signals
        assert all(s.tier == TIER_NORMAL for s in signals)

    def test_tier0_outranks_tier1_and_tier2(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        route_skill_ids = env.route_repo.list_skill_ids(env.r3.id)
        if not route_skill_ids:
            pytest.skip("no route skills")
        skill = env.skill_repo.get(route_skill_ids[0])
        stub = _StubSkillService(
            env.skill_repo, {vllm.id: [skill["name"]]},
            {skill["name"]: 0.9},
        )
        make_requirement_project(env, env.r3.id, ["Continuous Batching"])
        env.feedback.skill_service = stub
        signals = _rank(env)
        assert signals[0].tier == TIER_PRACTICE_BLOCKER
        cb = next(s for s in signals
                  if s.topic_id != signals[0].topic_id)
        assert cb.tier in (TIER_MARKET_SIGNAL, TIER_NORMAL)


class TestHardGate:
    def test_non_legal_topic_not_ranked(self, practice_readiness_env):
        env = practice_readiness_env
        # 后续阶段 Topic 不在 legal_topics → 不参与排序
        quant = env.plan_repo.find_topic_by_name_in_route(env.r3.id, "Quantization")
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        p2 = env.service.create_project("P2", "other", route_ids=[env.r3.id])
        env.service.add_topic(p2["id"], quant.id)
        env.service.set_status(p2["id"], "in_progress")
        env.readiness.set_requirement(p2["id"], quant.id, 3)
        signals = _rank(env)
        ids = {s.topic_id for s in signals}
        assert quant.id not in ids  # hard gate：不因 Tier0 越级

    def test_market_topic_also_needs_current_phase(self, practice_readiness_env):
        env = practice_readiness_env
        quant = env.plan_repo.find_topic_by_name_in_route(env.r3.id, "Quantization")
        signals = _rank(env)
        assert quant.id not in {s.topic_id for s in signals}


class TestCandidatePool:
    def test_only_highest_tier_to_ai(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        featured = _rank(env)
        pool = env.feedback.highest_tier_pool(featured)
        assert [s.topic_id for s in pool] == [topics[0].id]

    def test_empty_pool(self, practice_readiness_env):
        assert practice_readiness_env.feedback.highest_tier_pool([]) == []


class TestTieBreaks:
    def test_blocker_count(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        cb = topic_in_current_phase(env, env.r3.id, "Continuous Batching")
        # 项目 A 需要两者；项目 B 只需要 vLLM → vLLM blocker_count=2
        p_a, _ = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention", "Continuous Batching"],
            name="A",
        )
        p_b, _ = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], name="B",
        )
        signals = _rank(env)
        assert signals[0].topic_id == vllm.id
        assert signals[0].active_project_blocker_count == 2
        second = signals[1]
        assert second.topic_id == cb.id

    def test_capability_gap(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        cb = topic_in_current_phase(env, env.r3.id, "Continuous Batching")
        make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": 4}, name="A",
        )
        make_requirement_project(
            env, env.r3.id, ["Continuous Batching"],
            targets={"Continuous Batching": 1}, name="B",
        )
        signals = _rank(env)
        assert signals[0].topic_id == vllm.id
        assert signals[0].max_capability_gap == 4

    def test_last_learning_tiebreak(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        cb = topic_in_current_phase(env, env.r3.id, "Continuous Batching")
        make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], name="A",
        )
        make_requirement_project(
            env, env.r3.id, ["Continuous Batching"], name="B",
        )
        # vLLM 最近刚学过 → CB（更久未学）应先
        components = env.tl.get_components(vllm.id)
        req = [c for c in components if c["enabled"] and c["required"]][0]
        task = env.repo.create(
            title="x", scheduled_date="2026-09-14", topic_id=vllm.id,
            component_id=req["id"], source="generated", task_type="new",
        )
        env.repo.set_status(task.id, "done")
        signals = _rank(env)
        assert signals[0].topic_id == cb.id

    def test_deterministic(self, practice_readiness_env):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        a = [s.topic_id for s in _rank(env)]
        b = [s.topic_id for s in _rank(env)]
        assert a == b
        assert len(a) == len(set(a))


class TestRouteIsolation:
    def test_route_a_blocker_does_not_pollute_route_b(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        r2_signals = _rank(env, route_id=2)
        assert all(s.tier == TIER_NORMAL for s in r2_signals)
        r3_signals = _rank(env, route_id=3)
        assert r3_signals[0].tier == TIER_PRACTICE_BLOCKER

    def test_same_name_topic_route_isolation(self, practice_readiness_env):
        env = practice_readiness_env
        # R2 与 R6 各自有同名 "基础"-ish Topic；route_id 必须精确
        make_requirement_project(env, env.r2.id, ["SFT 指令微调"])
        r6_signals = _rank(env, route_id=6)
        for s in r6_signals:
            topic = env.plan_repo.get_topic(s.topic_id)
            assert env.plan_repo.get_route_id_for_topic(topic.id) == env.r6.id
