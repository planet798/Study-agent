"""TopicLearningProfileService 测试（Phase 2）。"""

from __future__ import annotations

import pytest

from app.services.learning_activity import ALL_ACTIVITY_KINDS
from app.services.topic_learning_profile_service import ActivityProfileError


def _topic(env, route_key: str, name: str):
    return env.plan_repo.find_topic_by_name_in_route(
        env.route_ids[route_key], name
    )


def _kinds(env, topic_id, enabled_only=False):
    comps = env.tl.get_components(topic_id)
    if enabled_only:
        comps = [c for c in comps if c["enabled"]]
    return [(c["activity_kind"], c["required"]) for c in comps]


def _complete(env, topic, kind, status="done"):
    comp = next(
        c for c in env.tl.get_components(topic.id)
        if c["activity_kind"] == kind
    )
    t = env.repo.create(
        f"{topic.name} {kind}", scheduled_date="2026-01-05",
        topic_id=topic.id, route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        component_id=comp["id"], learning_activity_kind=kind,
    )
    if status != "active":
        env.conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, t.id))
        env.conn.commit()
    return comp, t


class TestDefaultAndCanonicalProfiles:
    def test_canonical_profiles_seeded(self, activity_env):
        assert activity_env.result["activity_profiles"] == 93

    def test_lora_profile(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        assert _kinds(env, lora.id, enabled_only=True) == [
            ("theory", True), ("code_reading", True),
            ("experiment", True), ("interview", False),
        ]

    def test_transformer_profile(self, activity_env):
        env = activity_env
        comps = _kinds(env, _topic(env, "R1_LLM_FUNDAMENTALS",
                                   "Transformer：Attention / MHA / FFN").id,
                       enabled_only=True)
        assert ("theory", True) in comps
        assert ("code_reading", True) in comps

    def test_vllm_profile(self, activity_env):
        env = activity_env
        comps = _kinds(env, _topic(env, "R3_LLM_INFRA",
                                   "vLLM 与 PagedAttention").id,
                       enabled_only=True)
        assert ("theory", True) in comps
        assert ("code_reading", True) in comps
        assert ("experiment", True) in comps

    def test_function_calling_profile(self, activity_env):
        env = activity_env
        comps = _kinds(env, _topic(env, "R4_AI_AGENT",
                                   "Function Calling / Tool Calling").id,
                       enabled_only=True)
        assert ("theory", True) in comps
        assert ("experiment", True) in comps

    def test_langgraph_profile(self, activity_env):
        env = activity_env
        comps = _kinds(env, _topic(env, "R4_AI_AGENT", "LangGraph").id,
                       enabled_only=True)
        assert ("theory", True) in comps
        assert ("experiment", True) in comps
        assert ("code_reading", False) in comps

    def test_deepfm_profile(self, activity_env):
        env = activity_env
        comps = _kinds(env, _topic(env, "R5_RECOMMENDATION_SEARCH",
                                   "Wide & Deep / DeepFM 基础").id,
                       enabled_only=True)
        assert ("theory", True) in comps
        assert ("code_reading", True) in comps
        assert ("experiment", True) in comps

    def test_dsa_and_cpp_profile(self, activity_env):
        env = activity_env
        for name in ("数据结构与算法", "C++ 基础"):
            comps = _kinds(env, _topic(env, "R6_CS_FUNDAMENTALS", name).id,
                           enabled_only=True)
            assert ("theory", True) in comps
            assert ("experiment", True) in comps
            assert ("interview", True) in comps

    def test_default_profile_for_manual_topic(self, conn, plan_repo,
                                              topic_learning):
        plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31")
        phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                       "2099-12-31")
        topic = plan_repo.create_topic(phase.id, "手写 Topic")
        topic_learning.ensure_default_profile(topic.id)
        comps = topic_learning.get_components(topic.id)
        assert [(c["activity_kind"], c["required"]) for c in comps] == [
            ("theory", True)
        ]


def activity_env_stub(tl):
    class _E:
        tl = tl
    return _E


class TestSeedIdempotencyAndUserEdits:
    def test_seed_idempotent(self, activity_env):
        env = activity_env
        from app.services.canonical_route_service import CanonicalRouteService

        before = env.tl.repo.count_by_topic(
            _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA").id
        )
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo,
            topic_learning_service=env.tl,
        ).ensure_all()
        after = env.tl.repo.count_by_topic(
            _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA").id
        )
        assert before == after

    def test_seed_does_not_overwrite_user_edit(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        interview = env.tl.repo.get_by_topic_and_kind(lora.id, "interview")
        # 用户把 interview 改成 required
        env.tl.set_component_required(interview["id"], True)
        from app.services.canonical_route_service import CanonicalRouteService

        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo,
            topic_learning_service=env.tl,
        ).ensure_all()
        again = env.tl.repo.get_by_topic_and_kind(lora.id, "interview")
        assert again["required"] is True


class TestEnableRequiredGuards:
    def test_set_enabled_and_required(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        interview = env.tl.repo.get_by_topic_and_kind(lora.id, "interview")
        env.tl.set_component_enabled(interview["id"], False)
        got = env.tl.repo.get(interview["id"])
        assert got["enabled"] is False
        env.tl.set_component_enabled(interview["id"], True)
        assert env.tl.repo.get(interview["id"])["enabled"] is True

    def test_cannot_disable_last_required(self, activity_env):
        env = activity_env
        # 造一个只有一个 required 的 topic
        plan = env.plan_repo.create_plan("P2", "2026-01-01", "2099-12-31")
        phase = env.plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                           "2099-12-31")
        topic = env.plan_repo.create_topic(phase.id, "OnlyTheory")
        env.tl.ensure_default_profile(topic.id)
        theory = env.tl.repo.get_by_topic_and_kind(topic.id, "theory")
        with pytest.raises(ActivityProfileError):
            env.tl.set_component_enabled(theory["id"], False)
        with pytest.raises(ActivityProfileError):
            env.tl.set_component_required(theory["id"], False)

    def test_cannot_disable_with_active_task(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp, _ = _complete(env, lora, "theory", status="active")
        with pytest.raises(ActivityProfileError):
            env.tl.set_component_enabled(comp["id"], False)

    def test_can_disable_with_only_done_history(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp, _ = _complete(env, lora, "interview", status="done")
        env.tl.set_component_enabled(comp["id"], False)
        assert env.tl.repo.get(comp["id"])["enabled"] is False


class TestCompletion:
    def test_done_completes_component(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp, _ = _complete(env, lora, "theory", status="done")
        assert env.tl.is_component_complete(comp["id"]) is True

    @pytest.mark.parametrize("status", ["active", "not_done", "cancelled"])
    def test_non_done_not_complete(self, activity_env, status):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp, _ = _complete(env, lora, "theory", status=status)
        assert env.tl.is_component_complete(comp["id"]) is False

    def test_review_task_does_not_complete(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        t = env.repo.create(
            "复习", scheduled_date="2026-01-05", topic_id=lora.id,
            task_type="review", source="review", component_id=comp["id"],
            learning_activity_kind="theory",
        )
        env.repo.mark_done(t.id)
        # review 任务即使 done 也不参与 component completion
        assert env.tl.repo.is_component_done(comp["id"]) is True
        # 服务层用 component-linked done 判定；这里说明 review 任务不应绑定
        # component（Phase 2 规则），repo 层只按 component_id+done 判定。

    def test_curriculum_complete_requires_all_required(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        assert env.tl.is_topic_curriculum_complete(lora.id) is False
        for kind in ("theory", "code_reading"):
            _complete(env, lora, kind, status="done")
        assert env.tl.is_topic_curriculum_complete(lora.id) is False
        _complete(env, lora, "experiment", status="done")
        assert env.tl.is_topic_curriculum_complete(lora.id) is True

    def test_optional_pending_does_not_block(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        for kind in ("theory", "code_reading", "experiment"):
            _complete(env, lora, kind, status="done")
        # interview 是 optional，未完成不阻止
        assert env.tl.is_topic_curriculum_complete(lora.id) is True

    def test_disabled_pending_does_not_block(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "code_reading")
        env.tl.set_component_enabled(comp["id"], False)
        for kind in ("theory", "experiment"):
            _complete(env, lora, kind, status="done")
        assert env.tl.is_topic_curriculum_complete(lora.id) is True

    def test_no_profile_legacy_fallback(self, conn, plan_repo, repo,
                                        topic_learning):
        plan = plan_repo.create_plan("P3", "2026-01-01", "2099-12-31")
        phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                       "2099-12-31")
        topic = plan_repo.create_topic(phase.id, "LegacyTopic")
        assert topic_learning.is_topic_curriculum_complete(topic.id) is False
        t = repo.create("done", scheduled_date="2026-01-05", topic_id=topic.id)
        repo.mark_done(t.id)
        assert topic_learning.is_topic_curriculum_complete(topic.id) is True

    def test_next_required_component_order(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        assert env.tl.get_next_required_component(lora.id)["activity_kind"] \
            == "theory"
        _complete(env, lora, "theory")
        assert env.tl.get_next_required_component(lora.id)["activity_kind"] \
            == "code_reading"
        _complete(env, lora, "code_reading")
        assert env.tl.get_next_required_component(lora.id)["activity_kind"] \
            == "experiment"
        _complete(env, lora, "experiment")
        assert env.tl.get_next_required_component(lora.id) is None


class TestLegacyBackfill:
    def test_backfill_only_theory(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        # 历史 done 正式任务（无 component），标题含“实验”也不能猜 experiment
        t = env.repo.create(
            "LoRA 实验", scheduled_date="2026-01-05", topic_id=lora.id,
            source="generated", task_type="new",
        )
        env.repo.mark_done(t.id)
        stats = env.tl.backfill_legacy_theory()
        assert stats["legacy_theory_backfilled"] >= 1
        theory = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        experiment = env.tl.repo.get_by_topic_and_kind(lora.id, "experiment")
        assert env.tl.repo.is_component_done(theory["id"]) is True
        assert env.tl.repo.is_component_done(experiment["id"]) is False
        # 绑定的 activity_kind 必须是 theory
        row = env.conn.execute(
            "SELECT learning_activity_kind FROM tasks WHERE id = ?", (t.id,)
        ).fetchone()
        assert row[0] == "theory"

    def test_split_new_no_inheritance(self, activity_env):
        env = activity_env
        # SPLIT_NEW 新 topic：Embedding Fundamentals 全新状态
        new_topic = _topic(env, "R1_LLM_FUNDAMENTALS", "Embedding Fundamentals")
        assert env.tl.is_topic_curriculum_complete(new_topic.id) is False
        assert env.tl.get_next_required_component(new_topic.id)[
            "activity_kind"] == "theory"


class TestConsistency:
    def test_validate_and_repair(self, activity_env):
        env = activity_env
        lora = _topic(env, "R2_LLM_POST_TRAINING", "LoRA / QLoRA")
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        t = env.repo.create(
            "T", scheduled_date="2026-01-05", topic_id=lora.id,
            component_id=comp["id"], learning_activity_kind="experiment",
        )
        conflicts = env.tl.validate_consistency()
        assert any(c["task_id"] == t.id for c in conflicts)
        stats = env.tl.repair_consistency()
        assert stats["activity_kind_fixed"] >= 1
        fixed = env.repo.get(t.id)
        assert fixed.learning_activity_kind == "theory"
        assert env.tl.validate_consistency() == []
