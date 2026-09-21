"""Phase 6：PracticeReadinessService 测试。"""

from __future__ import annotations

import pytest

from app.services.capability import (
    AWARE,
    EXPERIMENT,
    EXPLAIN,
    IMPLEMENT,
    PROJECT,
)
from app.services.practice_readiness import (
    REASON_ACTIONABLE,
    REASON_CANCELLED_TODAY,
    REASON_HAS_ACTIVE_TASK,
    REASON_NEEDS_ASSESSMENT,
    REASON_NEEDS_EXPERIMENT_EVIDENCE,
    REASON_PREREQUISITE_BLOCKED,
    REASON_PROJECT_NOT_IN_PROGRESS,
    REASON_ROUTE_ARCHIVED,
    REASON_ROUTE_PAUSED,
    REASON_SATISFIED,
    REASON_TOPIC_NOT_CURRENTLY_AVAILABLE,
)
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    add_same_day_task,
    complete_required_components,
    current_phase_topics,
    ensure_capability,
    has_activity,
    make_requirement_project,
    topic_in_current_phase,
)

R3 = "r3"
R2 = "r2"


def _status_for(env, project, topic_id):
    statuses = env.readiness.list_project_requirements(
        project["id"], plan_date=DEFAULT_PLAN_DATE
    )
    return next(s for s in statuses if s.topic_id == int(topic_id))


class TestProjectStatusGate:
    def test_planned_shows_but_not_driving(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], status="planned"
        )
        st = _status_for(env, project, topics[0].id)
        assert st.satisfied is False
        assert st.planner_actionable is False
        assert st.reason_code == REASON_PROJECT_NOT_IN_PROGRESS
        readiness = env.readiness.get_project_readiness(project["id"])
        assert readiness["total_count"] == 1
        assert readiness["drives_planner"] is False

    def test_in_progress_drives(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is True
        assert st.reason_code == REASON_ACTIONABLE
        assert env.readiness.list_actionable_blockers_by_route(
            env.r3.id, DEFAULT_PLAN_DATE
        )

    def test_completed_not_driving(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], status="completed"
        )
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_PROJECT_NOT_IN_PROGRESS

    def test_archived_not_driving(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], status="archived"
        )
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False


class TestSatisfied:
    def test_current_ge_target_satisfied(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"], targets={"LoRA / QLoRA": IMPLEMENT}
        )
        ensure_capability(env, topics[0].id, IMPLEMENT)
        st = _status_for(env, project, topics[0].id)
        assert st.satisfied is True
        assert st.capability_gap == 0
        assert st.reason_code == REASON_SATISFIED

    def test_project_level_satisfies_lower_targets(self, practice_readiness_env):
        env = practice_readiness_env
        for target in (AWARE, EXPLAIN, IMPLEMENT, EXPERIMENT):
            env2 = None
            project, topics = make_requirement_project(
                env, env.r2.id, ["LoRA / QLoRA"],
                targets={"LoRA / QLoRA": target},
                name=f"p{target}",
            )
            ensure_capability(env, topics[0].id, PROJECT)
            st = _status_for(env, project, topics[0].id)
            assert st.satisfied is True, target

    def test_mastery_does_not_satisfy(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        kp_id = ensure_capability(env, topics[0].id, AWARE)
        # mastery 很高，但 capability 仍只有 AWARE
        env.conn.execute(
            "UPDATE knowledge_points SET mastery_estimate = 0.95 WHERE id = ?",
            (kp_id,),
        )
        env.conn.commit()
        st = _status_for(env, project, topics[0].id)
        assert st.satisfied is False
        assert st.current_capability_level == AWARE
        assert st.capability_gap == IMPLEMENT - AWARE

    def test_gap_values(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": EXPERIMENT},
        )
        ensure_capability(env, topics[0].id, AWARE)
        st = _status_for(env, project, topics[0].id)
        assert st.capability_gap == EXPERIMENT - AWARE

    def test_satisfied_removes_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        assert len(env.readiness.list_route_blockers(env.r3.id,
                                                     DEFAULT_PLAN_DATE)) == 1
        ensure_capability(env, topics[0].id, IMPLEMENT)
        assert env.readiness.list_route_blockers(
            env.r3.id, DEFAULT_PLAN_DATE
        ) == []


class TestRouteGates:
    def test_route_paused(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.route_repo.update(env.r3.id, planning_enabled=False)
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_ROUTE_PAUSED

    def test_route_archived(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.route_repo.archive(env.r3.id)
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_ROUTE_ARCHIVED

    def test_future_phase_not_available(self, practice_readiness_env):
        env = practice_readiness_env
        # Quantization 属于 R3 后续阶段
        project, topics = make_requirement_project(
            env, env.r3.id, ["Quantization"]
        )
        assert topics[0] not in current_phase_topics(env, env.r3.id)
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_TOPIC_NOT_CURRENTLY_AVAILABLE

    def test_prerequisite_blocked(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        topic_id = topics[0].id
        original = env.readiness._blocked_provider

        def blocked(route_id, plan_date):
            return {int(topic_id)}

        env.readiness._blocked_provider = blocked
        try:
            st = _status_for(env, project, topic_id)
            assert st.planner_actionable is False
            assert st.reason_code == REASON_PREREQUISITE_BLOCKED
        finally:
            env.readiness._blocked_provider = original


class TestSameDayExclusion:
    def test_active_task_blocks(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_same_day_task(env, topics[0].id, DEFAULT_PLAN_DATE, "active")
        st = _status_for(env, project, topics[0].id)
        assert st.reason_code == REASON_HAS_ACTIVE_TASK
        assert st.planner_actionable is False

    def test_not_done_blocks(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_same_day_task(env, topics[0].id, DEFAULT_PLAN_DATE, "not_done")
        st = _status_for(env, project, topics[0].id)
        assert st.reason_code == REASON_HAS_ACTIVE_TASK

    def test_postponed_blocks(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        task = add_same_day_task(env, topics[0].id, "2026-09-10", "active")
        env.repo.postpone(task.id, DEFAULT_PLAN_DATE)
        st = _status_for(env, project, topics[0].id)
        assert st.reason_code == REASON_HAS_ACTIVE_TASK

    def test_cancelled_today_blocks(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_same_day_task(env, topics[0].id, DEFAULT_PLAN_DATE, "cancelled")
        st = _status_for(env, project, topics[0].id)
        assert st.reason_code == REASON_CANCELLED_TODAY

    def test_cancelled_tomorrow_does_not_block(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_same_day_task(env, topics[0].id, "2026-09-16", "cancelled")
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is True


class TestComponentAware:
    def test_incomplete_component_actionable(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is True
        assert st.next_component_id is not None
        assert st.next_activity_kind  # theory 等

    def test_all_done_implement_gap_needs_assessment(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        ensure_capability(env, topics[0].id, EXPLAIN)
        complete_required_components(env, topics[0].id)
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_NEEDS_ASSESSMENT

    def test_experiment_done_no_level4_needs_evidence(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        if not has_activity(env, topic_in_current_phase(
            env, env.r3.id, "vLLM 与 PagedAttention"
        ).id, "experiment"):
            pytest.skip("no experiment component")
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": EXPERIMENT},
        )
        ensure_capability(env, topics[0].id, IMPLEMENT)
        complete_required_components(env, topics[0].id)
        st = _status_for(env, project, topics[0].id)
        assert st.reason_code == REASON_NEEDS_EXPERIMENT_EVIDENCE
        assert st.planner_actionable is False

    def test_pending_experiment_actionable(self, practice_readiness_env):
        env = practice_readiness_env
        topic = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        if not has_activity(env, topic.id, "experiment"):
            pytest.skip("no experiment component")
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": EXPERIMENT},
        )
        ensure_capability(env, topics[0].id, IMPLEMENT)
        complete_required_components(env, topics[0].id,
                                     except_kinds=("experiment",))
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is True
        assert st.next_activity_kind == "experiment"


class TestRealtimeFeedback:
    def test_evidence_revoke_restores_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        kp_id = ensure_capability(env, topics[0].id, IMPLEMENT)
        assert _status_for(env, project, topics[0].id).satisfied is True
        # 撤销所有 active evidence → 回落
        for e in env.cap.repo.list_active_by_kp(kp_id):
            env.cap.repo.revoke(e["id"], "test")
        st = _status_for(env, project, topics[0].id)
        assert st.satisfied is False
        assert st.planner_actionable is True

    def test_project_complete_removes_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.service.set_status(project["id"], "completed")
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is False

    def test_reopen_restores_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.service.set_status(project["id"], "completed")
        env.service.set_status(project["id"], "in_progress")
        st = _status_for(env, project, topics[0].id)
        assert st.planner_actionable is True

    def test_completing_component_advances_next(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        st1 = _status_for(env, project, topics[0].id)
        first_kind = st1.next_activity_kind
        first_id = st1.next_component_id
        from tests.practice_capability_helpers import mark_component_done

        mark_component_done(env, topics[0].id, first_id)
        st2 = _status_for(env, project, topics[0].id)
        assert st2.next_activity_kind != first_kind or st2.next_activity_kind == ""


class TestNoBlockerCases:
    def test_project_skill_alone_no_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r3.id])
        skills = env.skill_repo.list_all()
        if skills:
            env.service.add_skill(project["id"], skills[0]["id"])
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        env.service.add_topic(project["id"], vllm.id)
        env.service.set_status(project["id"], "in_progress")
        # 只有 Skill 标签，没有 Topic requirement → 不形成 blocker
        assert env.readiness.list_route_blockers(
            env.r3.id, DEFAULT_PLAN_DATE
        ) == []

    def test_topic_without_requirement_no_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r3.id])
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        env.service.add_topic(project["id"], vllm.id)
        env.service.set_status(project["id"], "in_progress")
        assert env.readiness.list_route_blockers(
            env.r3.id, DEFAULT_PLAN_DATE
        ) == []


class TestThreeDimensionsIndependent:
    def test_requirement_does_not_change_activity_mastery_review(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        assert env.conn.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0] == 0
        assert env.conn.execute(
            "SELECT COALESCE(SUM(mastery_estimate),0) FROM knowledge_points"
        ).fetchone()[0] == 0
        assert env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points "
            "WHERE next_review_date IS NOT NULL"
        ).fetchone()[0] == 0
        # readiness 查询本身也是只读的
        env.readiness.list_project_requirements(project["id"], DEFAULT_PLAN_DATE)
        assert env.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    def test_project_evidence_does_not_complete_curriculum(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], status="planned"
        )
        ensure_capability(env, topics[0].id, PROJECT)
        # curriculum 仍按 activity 状态（未完成）
        assert env.tl.is_topic_curriculum_complete(topics[0].id) is False
