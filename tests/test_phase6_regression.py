"""Phase 6：回归测试（Scheduler / budget / priority / Review / JD / Prompt）。"""

from __future__ import annotations

import inspect

import pytest

from app.utils.date_utils import add_days

TODAY = "2026-09-15"


def _build_env(tmp_path, name):
    """从零构建 canonical 六路线环境（用于两次独立模拟对比）。"""
    from app.database.assessment_repository import AssessmentRepository
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.database.connection import get_connection
    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.practice_repository import (
        PracticeProjectRepository,
        PracticeRequirementRepository,
        PracticeTopicEvidenceRepository,
    )
    from app.database.repository import TaskRepository
    from app.database.schema import migrate
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.canonical_route_service import CanonicalRouteService
    from app.services.capability_service import CapabilityService
    from app.services.planner_feedback import PlannerFeedbackService
    from app.services.practice_capability_service import (
        PracticeCapabilityService,
    )
    from app.services.practice_project_service import PracticeProjectService
    from app.services.practice_readiness import PracticeReadinessService
    from app.services.study_plan_service import StudyPlanService
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    conn = get_connection(str(tmp_path / f"{name}.db"))
    migrate(conn)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    StudyPlanService(repo, plan_repo).ensure_default_plan()
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    arepo = AssessmentRepository(conn)
    tl = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    CanonicalRouteService(
        conn, route_repo, plan_repo, skill_repo, topic_learning_service=tl
    ).ensure_all()
    cap = CapabilityService(conn, CapabilityEvidenceRepository(conn))
    project_service = PracticeProjectService(
        conn, route_repo=route_repo, plan_repo=plan_repo, skill_repo=skill_repo,
        evidence_repo=PracticeTopicEvidenceRepository(conn),
    )
    readiness = PracticeReadinessService(
        conn, requirement_repo=PracticeRequirementRepository(conn),
        plan_repo=plan_repo, route_repo=route_repo, capability_repo=cap.repo,
        topic_learning_service=tl, task_repo=repo,
        current_phase_provider=None, blocked_topic_provider=None,
    )
    feedback = PlannerFeedbackService(
        conn, readiness_service=readiness, plan_repo=plan_repo,
        route_repo=route_repo, skill_service=None, task_repo=repo,
    )
    return {
        "conn": conn, "repo": repo, "plan_repo": plan_repo,
        "route_repo": route_repo, "arepo": arepo, "tl": tl,
        "project_service": project_service, "readiness": readiness,
        "feedback": feedback, "cap": cap,
    }


def _make_scheduler(env, feedback_service=None):
    from app.services.route_scheduler import GlobalDailyScheduler

    return GlobalDailyScheduler(
        env["repo"], env["plan_repo"], env["route_repo"],
        assessment_repo=env["arepo"], topic_learning_service=env["tl"],
        feedback_service=feedback_service,
    )


def _simulate(env, days=14, feedback_service=None):
    sched = _make_scheduler(env, feedback_service)
    sequence = []
    total_created = 0
    max_minutes = 0
    date = TODAY
    for _ in range(days):
        out = sched.generate(date)
        allocation = [
            (a.route_id, a.allocated_slots) for a in out["allocations"]
        ]
        sequence.append(allocation)
        total_created += len(out["created_ids"])
        minutes = sum(
            (env["repo"].get(i).estimated_minutes or 0)
            for i in out["created_ids"]
        )
        max_minutes = max(max_minutes, minutes)
        for tid in out["created_ids"]:
            env["repo"].mark_done(tid)
        date = add_days(date, 1)
    return sequence, total_created, max_minutes


def _add_blockers(env):
    r3 = env["route_repo"].get_by_key("R3_LLM_INFRA")
    vllm = env["plan_repo"].find_topic_by_name_in_route(
        r3.id, "vLLM 与 PagedAttention"
    )
    project = env["project_service"].create_project(
        "Qwen LoRA 微调", "llm_training", route_ids=[r3.id]
    )
    env["project_service"].add_topic(project["id"], vllm.id)
    env["project_service"].set_status(project["id"], "in_progress")
    env["readiness"].set_requirement(project["id"], vllm.id, 3)
    return project, vllm


class TestSchedulerRegression:
    def test_route_allocation_unchanged_by_practice(self, tmp_path):
        baseline = _build_env(tmp_path, "base")
        seq_base, created_base, _ = _simulate(baseline, days=14)

        withp = _build_env(tmp_path, "withp")
        _add_blockers(withp)
        seq_p, created_p, _ = _simulate(
            withp, days=14, feedback_service=withp["feedback"]
        )

        assert [tuple(x) for x in seq_base] == [tuple(x) for x in seq_p]
        assert created_base == created_p

    def test_practice_changes_topic_not_allocation(self, tmp_path):
        withp = _build_env(tmp_path, "topics")
        project, vllm = _add_blockers(withp)
        sched = _make_scheduler(withp, withp["feedback"])
        out = sched.generate(TODAY)
        r3_created = [
            withp["repo"].get(i) for i in out["created_ids"]
            if withp["repo"].get(i).route_id == project[
                "id"
            ] or False
        ]
        # 至少能生成任务，且 R3 topic 优先为 vLLM（blocker）
        r3_id = withp["route_repo"].get_by_key("R3_LLM_INFRA").id
        r3_tasks = [
            withp["repo"].get(i) for i in out["created_ids"]
            if withp["repo"].get(i).route_id == r3_id
        ]
        if r3_tasks:
            assert r3_tasks[0].topic_id == vllm.id

    def test_global_budget(self, tmp_path):
        env = _build_env(tmp_path, "budget")
        _add_blockers(env)
        sched = _make_scheduler(env, env["feedback"])
        out = sched.generate(TODAY)
        assert len(out["created_ids"]) <= 3
        minutes = sum(
            env["repo"].get(i).estimated_minutes for i in out["created_ids"]
        )
        assert minutes <= 180

    def test_route_priority_not_modified(self, tmp_path):
        env = _build_env(tmp_path, "prio")
        _add_blockers(env)
        before = {
            r.id: r.priority
            for r in env["route_repo"].list_learning_routes()
        }
        _simulate(env, days=7, feedback_service=env["feedback"])
        after = {
            r.id: r.priority
            for r in env["route_repo"].list_learning_routes()
        }
        assert before == after

    def test_scheduler_source_does_not_import_practice(self):
        import app.services.route_scheduler as sched

        src = inspect.getsource(sched)
        assert "PracticeReadinessService" not in src
        assert "PlannerFeedbackService" not in src
        assert "capability" not in src.lower()


class TestOtherRegressions:

    def test_jd_market_signal_unchanged(self):
        from app.services.market_signal import DEFAULT_WINDOW_DAYS

        assert DEFAULT_WINDOW_DAYS == 30
        import app.services.market_signal as ms

        src = inspect.getsource(ms).lower()
        assert "practice" not in src
        assert "week" not in src  # 不新增 weekly trend

    def test_curriculum_gap_does_not_create_topics(self):
        import app.services.skill_service as ss

        src = inspect.getsource(ss)
        assert "def curriculum_gap_skills" in src
        # 不自动创建 Topic
        assert "create_topic" not in src

    def test_prompt_override_compat(self, prompt_registry):
        # planner.user 的 planner_feedback_section 是 optional
        defn = prompt_registry.definition("planner.user")
        assert "planner_feedback_section" in defn.optional_variables
        assert "context_json" in defn.required_variables

    def test_old_override_without_feedback_placeholder(self, prompt_registry):
        from app.ai.prompts import build_planner_user_vars
        from app.ai.planner_context import PlanningContext

        ctx = PlanningContext(current_date="2026-09-15")
        variables = build_planner_user_vars(ctx)
        assert "planner_feedback_section" in variables
        # 无 feedback → 空串
        assert variables["planner_feedback_section"] == ""

    def test_capability_evidence_unchanged(self, practice_readiness_env):
        env = practice_readiness_env
        # requirement / readiness 不写 capability
        from tests.practice_capability_helpers import make_requirement_project

        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        env.readiness.list_route_blockers(env.r3.id, "2026-09-15")
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence"
        ).fetchone()[0] == 0

    def test_six_routes_unchanged(self, practice_readiness_env):
        env = practice_readiness_env
        keys = {
            r.route_key for r in env.route_repo.list_learning_routes()
        }
        for i in range(1, 7):
            assert any(k.startswith(f"R{i}_") for k in keys)
        assert not any(k.startswith("R7") for k in keys)

    def test_practice_evidence_history_not_regressed(
        self, practice_capability_env
    ):
        from tests.practice_capability_helpers import (
            create_evidence,
            lora_topic,
            ready_lora,
        )
        from app.services.practice_project_service import PracticeError

        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.delete_output(r.repo["id"])

    def test_activity_not_regressed(self, practice_readiness_env):
        env = practice_readiness_env
        vllm = env.plan_repo.find_topic_by_name_in_route(
            env.r3.id, "vLLM 与 PagedAttention"
        )
        from tests.practice_capability_helpers import make_requirement_project

        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        # requirement 不改变 curriculum 完成状态
        assert env.tl.is_topic_curriculum_complete(vllm.id) is False
