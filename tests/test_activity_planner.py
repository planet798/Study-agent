"""Component-aware Planner / Phase progression / Skill coverage 测试（Phase 2）。"""

from __future__ import annotations

import pytest

from app.ai.schemas import DailyPlan, RecommendedTask
from app.services.canonical_topic_components import profile_for_topic
from app.services.daily_planner_service import DailyPlannerService
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-01-05"
TOMORROW = "2026-01-06"


def _custom_route(env, name, phases_spec, route_key=None):
    """创建一条测试路线：phases_spec = [(phase_name, [topic_name,...]), ...]。"""
    route = env.route_repo.create(name, route_key=route_key)
    plan = env.plan_repo.create_plan(
        f"{name} plan", "2026-01-01", "2099-12-31", route_id=route.id
    )
    topics = {}
    for i, (pname, tnames) in enumerate(phases_spec):
        ph = env.plan_repo.create_phase(
            plan.id, pname, "2026-01-01", "2099-12-31", order_index=i + 1
        )
        for j, tname in enumerate(tnames):
            t = env.plan_repo.create_topic(ph.id, tname, order_index=j + 1)
            env.tl.ensure_profile_from_spec(
                t.id, profile_for_topic(tname, "R2_LLM_POST_TRAINING")
            )
            topics[tname] = t
    return route, plan, topics


def _sps(env, route_id):
    return StudyPlanService(
        env.repo, env.plan_repo, route_id=route_id,
        learning_route_repo=env.route_repo, topic_learning_service=env.tl,
        scope_tasks_by_route=True,
    )


def _complete_component(env, topic, kind, status="done"):
    comp = next(
        c for c in env.tl.get_components(topic.id)
        if c["activity_kind"] == kind
    )
    t = env.repo.create(
        f"{topic.name} {kind}", scheduled_date=TODAY, topic_id=topic.id,
        route_id=env.plan_repo.get_route_id_for_topic(topic.id),
        component_id=comp["id"], learning_activity_kind=kind,
    )
    if status != "active":
        env.conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, t.id))
        env.conn.commit()
    return comp, t


class TestFallbackPlannerActivity:
    def test_next_component_task(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        sps = _sps(env, route.id)
        res = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert len(res["generated"]) == 1
        task = res["generated"][0]
        assert task.learning_activity_kind == "theory"
        assert task.component_id is not None

    def test_advances_theory_to_code_reading(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        sps = _sps(env, route.id)
        sps.generate_daily_tasks(TODAY, max_tasks=1)
        lora = topics["LoRA / QLoRA"]
        env.repo.mark_done(
            env.repo.list_by_topic_id(lora.id)[0].id
        )
        res2 = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert res2["generated"][0].learning_activity_kind == "code_reading"

    def test_active_component_not_duplicated(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        _complete_component(env, lora, "theory", status="active")
        sps = _sps(env, route.id)
        res = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert res["generated"] == []
        assert lora.id in res["skipped_duplicate"]

    def test_cancelled_not_regenerated_today_but_next_day(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        sps = _sps(env, route.id)
        res = sps.generate_daily_tasks(TODAY, max_tasks=1)
        task = res["generated"][0]
        env.conn.execute(
            "UPDATE tasks SET status='cancelled' WHERE id=?", (task.id,)
        )
        env.conn.commit()
        res_same = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert res_same["generated"] == []
        res_next = sps.generate_daily_tasks(TOMORROW, max_tasks=1)
        assert len(res_next["generated"]) == 1
        assert res_next["generated"][0].learning_activity_kind == "theory"

    def test_postponed_not_duplicated(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        sps = _sps(env, route.id)
        task = sps.generate_daily_tasks(TODAY, max_tasks=1)["generated"][0]
        env.repo.postpone(task.id, TOMORROW)
        env.repo.set_status(task.id, "active")
        # 同一天重规划不应重复生成
        res = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert res["generated"] == []

    def test_experiment_after_theory_and_code(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        _complete_component(env, lora, "theory")
        _complete_component(env, lora, "code_reading")
        sps = _sps(env, route.id)
        res = sps.generate_daily_tasks(TODAY, max_tasks=1)
        assert res["generated"][0].learning_activity_kind == "experiment"


class TestAiPlannerCannotExceedComponent:
    def _dps(self, env, route_id):
        return DailyPlannerService(
            env.repo, env.plan_repo, planner=None,
            study_plan_service=_sps(env, route_id),
            scope_tasks_by_route=True,
        )

    def test_ai_task_gets_forced_component(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        dps = self._dps(env, route.id)
        plan_obj = DailyPlan(
            reasoning="r",
            recommended_tasks=(RecommendedTask(
                topic_id=lora.id, title="随便写的标题",
                description="", estimated_minutes=30, priority=2,
            ),),
            carry_over_tasks=(), daily_minutes=30, adjustment="a",
        )
        valid, created, problems = dps._validate_and_create(
            plan_obj, TODAY, max_tasks=1
        )
        assert valid and created, problems
        task = env.repo.get(created[0])
        assert task.learning_activity_kind == "theory"
        assert task.component_id is not None

    def test_ai_cannot_skip_to_experiment(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-LoRA", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        _complete_component(env, lora, "theory")
        dps = self._dps(env, route.id)
        plan_obj = DailyPlan(
            reasoning="r",
            recommended_tasks=(RecommendedTask(
                topic_id=lora.id, title="直接做实验",
                description="", estimated_minutes=30, priority=2,
            ),),
            carry_over_tasks=(), daily_minutes=30, adjustment="a",
        )
        valid, created, problems = dps._validate_and_create(
            plan_obj, TODAY, max_tasks=1
        )
        assert valid and created
        # 即使标题写“实验”，program 仍强制 code_reading
        task = env.repo.get(created[0])
        assert task.learning_activity_kind == "code_reading"


class TestPhaseProgression:
    def test_phase_waits_for_required_components(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-2P", [("P1", ["LoRA / QLoRA"]),
                          ("P2", ["PPO"])]
        )
        lora = topics["LoRA / QLoRA"]
        sps = _sps(env, route.id)
        assert sps.get_current_phase(TODAY).name == "P1"
        _complete_component(env, lora, "theory")
        assert sps.get_current_phase(TODAY).name == "P1"  # 仍等待
        _complete_component(env, lora, "code_reading")
        _complete_component(env, lora, "experiment")
        assert sps.get_current_phase(TODAY).name == "P2"

    def test_route_isolation_components(self, activity_env):
        env = activity_env
        r1, _, t1 = _custom_route(env, "RA", [("P", ["LoRA / QLoRA"])])
        r2, _, t2 = _custom_route(env, "RB", [("P", ["LoRA / QLoRA"])])
        # 完成 RA 的 required
        for kind in ("theory", "code_reading", "experiment"):
            _complete_component(env, t1["LoRA / QLoRA"], kind)
        assert env.tl.is_topic_curriculum_complete(t1["LoRA / QLoRA"].id)
        # RB 不受影响
        assert not env.tl.is_topic_curriculum_complete(t2["LoRA / QLoRA"].id)


class TestSkillCoverageComponentAware:
    def test_coverage_waits_for_curriculum(self, activity_env):
        env = activity_env
        from app.services.skill_service import SkillService

        route, plan, topics = _custom_route(
            env, "R-Skill", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        env.skill_repo.create("LoRA / QLoRA", tier="S",
                              linked_topics=[lora.id])
        svc = SkillService(env.skill_repo, plan_repo=env.plan_repo,
                           topic_learning_service=env.tl)
        assert "LoRA / QLoRA" not in svc.refresh_coverage()
        for kind in ("theory", "code_reading", "experiment"):
            _complete_component(env, lora, kind)
        assert "LoRA / QLoRA" in svc.refresh_coverage()


class TestBudgetRegression:
    def test_scheduler_budget_unchanged(self, activity_env):
        from app.services.route_scheduler import GlobalDailyScheduler

        sched = GlobalDailyScheduler(
            activity_env.repo, activity_env.plan_repo,
            activity_env.route_repo,
            topic_learning_service=activity_env.tl,
        )
        res = sched.generate(TODAY)
        assert len(res["created_ids"]) <= 3
        total = sum(t.estimated_minutes for t in res["created"])
        assert total <= res["max_minutes"]


class TestAssessmentActivityContext:
    def test_activity_context_in_prompt(self):
        from app.ai.prompts import build_assessment_generate_vars

        ctx = build_assessment_generate_vars("LoRA", "", 4, "experiment")
        assert "实验" in ctx["activity_context"]
        ctx2 = build_assessment_generate_vars("LoRA", "", 4, None)
        assert ctx2["activity_context"] == ""

    def test_mastery_algorithm_unchanged(self):
        # Phase 2 不改 mastery 平滑策略
        from app.services import assessment_service as a

        assert a._PREVIOUS_WEIGHT == 0.7
        assert a._NEW_WEIGHT == 0.3


class TestReviewDoesNotAdvance:
    def test_review_task_does_not_complete_component(self, activity_env):
        env = activity_env
        route, plan, topics = _custom_route(
            env, "R-Rev", [("P1", ["LoRA / QLoRA"])]
        )
        lora = topics["LoRA / QLoRA"]
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        t = env.repo.create(
            "复习任务", scheduled_date=TODAY, topic_id=lora.id,
            task_type="review", source="review",
        )
        env.repo.mark_done(t.id)
        # Review 任务不带 component_id → 不推进 component
        assert env.tl.is_component_complete(comp["id"]) is False
        assert env.tl.get_next_required_component(lora.id)[
            "activity_kind"] == "theory"
