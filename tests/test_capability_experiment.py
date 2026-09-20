"""Experiment Outcome → EXPERIMENT capability 测试（Phase 3）。"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.skill_repository import LearningOutcomeRepository
from app.services.capability import AWARE, EXPERIMENT, PROJECT
from app.services.capability_service import CapabilityService
from app.services.learning_outcome_service import LearningOutcomeService


def _setup(conn, plan_repo, repo, route_id=None, name="LoRA",
           task_status="done", activity="experiment"):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31",
                                 route_id=route_id)
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                   "2099-12-31")
    topic = plan_repo.create_topic(phase.id, name)
    a = AssessmentRepository(conn)
    kp = a.get_or_create_knowledge_point_for_topic(
        topic.id, name, "", route_id=route_id
    )
    task = repo.create("LoRA 实验", scheduled_date="2026-01-05",
                       topic_id=topic.id, knowledge_point_id=kp["id"],
                       route_id=route_id, task_type="new", source="generated",
                       learning_activity_kind=activity)
    if task_status == "done":
        repo.mark_done(task.id)
    return topic, kp, task


def _svc(conn):
    cap = CapabilityService(conn, CapabilityEvidenceRepository(conn))
    out = LearningOutcomeService(LearningOutcomeRepository(conn),
                                 capability_service=cap)
    return cap, out


class TestExperimentEvidence:
    def test_valid_outcome_gives_experiment(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, out = _svc(conn)
        o = out.create_manual_outcome(
            kind="experiment", title="Qwen LoRA 微调实验",
            content="训练 loss 从 2.1 降到 0.8",
            metrics={"loss": 0.8}, linked_kp_id=kp["id"], task_id=task.id,
        )
        assert cap.get_current_level(kp["id"]) == EXPERIMENT
        ev = cap.repo.get_by_key(f"experiment_outcome:{o['id']}")
        assert ev["capability_level"] == EXPERIMENT
        assert ev["details"]["has_metrics"] is True

    def test_task_alone_max_aware(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, _out = _svc(conn)
        # 只有 done experiment task → 最多 AWARE
        cap.sync_from_task(task.id)
        assert cap.get_current_level(kp["id"]) == AWARE
        assert cap.get_current_level(kp["id"]) < EXPERIMENT

    def test_outcome_without_artifact_skipped(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, out = _svc(conn)
        o = out.create_manual_outcome(
            kind="experiment", title="实验",
            content="我做完了",  # 无 metrics/git/url/dataset
            linked_kp_id=kp["id"], task_id=task.id,
        )
        assert cap.get_current_level(kp["id"]) == 0
        assert cap.repo.get_by_key(f"experiment_outcome:{o['id']}") is None

    def test_outcome_empty_content_skipped(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, _out = _svc(conn)
        assert cap.sync_from_experiment_outcome(9999) is None
        # 构造无结果说明
        repo_out = LearningOutcomeRepository(conn)
        o = repo_out.create(kind="experiment", title="t", content="",
                            metrics={"loss": 0.1}, linked_kp_id=kp["id"],
                            task_id=task.id)
        assert cap.sync_from_experiment_outcome(o["id"]) is None

    def test_task_not_done_skipped(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo, task_status="active")
        cap, out = _svc(conn)
        o = out.create_manual_outcome(
            kind="experiment", title="t", content="结果",
            metrics={"loss": 0.1}, linked_kp_id=kp["id"], task_id=task.id,
        )
        assert cap.get_current_level(kp["id"]) == 0

    def test_wrong_activity_skipped(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo, activity="theory")
        cap, out = _svc(conn)
        o = out.create_manual_outcome(
            kind="experiment", title="t", content="结果",
            metrics={"loss": 0.1}, linked_kp_id=kp["id"], task_id=task.id,
        )
        assert cap.get_current_level(kp["id"]) == 0

    def test_kp_conflict_rejected(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        # 另一个 kp
        topic2, kp2, _ = _setup(conn, plan_repo, repo, name="PPO")
        cap, _out = _svc(conn)
        repo_out = LearningOutcomeRepository(conn)
        o = repo_out.create(kind="experiment", title="t", content="结果",
                            metrics={"loss": 0.1}, linked_kp_id=kp2["id"],
                            task_id=task.id)
        # linked_kp=kp2 但 task.kp=kp → conflict
        assert cap.sync_from_experiment_outcome(o["id"]) is None

    def test_kind_not_experiment_skipped(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, _out = _svc(conn)
        repo_out = LearningOutcomeRepository(conn)
        o = repo_out.create(kind="topic", title="t", content="结果",
                            metrics={"loss": 0.1}, linked_kp_id=kp["id"],
                            task_id=task.id)
        assert cap.sync_from_experiment_outcome(o["id"]) is None

    def test_outcome_idempotent(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, out = _svc(conn)
        o = out.create_manual_outcome(
            kind="experiment", title="t", content="结果",
            metrics={"loss": 0.1}, linked_kp_id=kp["id"], task_id=task.id,
        )
        cap.sync_from_experiment_outcome(o["id"])
        assert len(cap.repo.list_by_kp(kp["id"])) == 1


class TestProjectNeverGenerated:
    def test_task_cannot_project(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, _ = _svc(conn)
        cap.sync_from_task(task.id)
        assert cap.get_current_level(kp["id"]) <= AWARE
        assert cap.get_current_level(kp["id"]) < PROJECT

    def test_assessment_cannot_project(self, conn, plan_repo, repo):
        import json

        from app.database.assessment_repository import AssessmentRepository

        topic, kp, task = _setup(conn, plan_repo, repo)
        a = AssessmentRepository(conn)
        att = a.create_attempt(kp["id"], json.dumps(
            [{"question": "q", "type": "coding", "expected_points": 2}]
        ))
        a.update_attempt(
            att["id"], judge_status="judged", mastery_estimate=0.9,
            result_level="excellent",
            ai_result_json=json.dumps({
                "questions": [{"question_index": 0, "verdict": "correct",
                               "reason": "ok"}],
                "weak_points": [], "result_level": "excellent",
                "mastery_estimate": 0.9,
            }),
        )
        cap, _ = _svc(conn)
        cap.sync_from_assessment(att["id"])
        assert cap.get_current_level(kp["id"]) < PROJECT

    def test_experiment_cannot_project(self, conn, plan_repo, repo):
        topic, kp, task = _setup(conn, plan_repo, repo)
        cap, out = _svc(conn)
        out.create_manual_outcome(
            kind="experiment", title="t", content="结果",
            metrics={"loss": 0.1}, linked_kp_id=kp["id"], task_id=task.id,
        )
        assert cap.get_current_level(kp["id"]) == EXPERIMENT
        assert cap.get_current_level(kp["id"]) < PROJECT
        assert cap.can_generate_project() is False
