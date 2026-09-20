"""Capability backfill preview/apply 测试（Phase 3）。"""

from __future__ import annotations

import json

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.skill_repository import LearningOutcomeRepository
from app.services.capability import AWARE, EXPLAIN, IMPLEMENT
from app.services.capability_service import CapabilityService


def _kp(conn, plan_repo, name="LoRA"):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31")
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                   "2099-12-31")
    topic = plan_repo.create_topic(phase.id, name)
    a = AssessmentRepository(conn)
    kp = a.get_or_create_knowledge_point_for_topic(topic.id, name, "")
    return topic, kp


def _svc(conn):
    return CapabilityService(conn, CapabilityEvidenceRepository(conn))


class TestPreview:
    def test_preview_does_not_write(self, conn, plan_repo, repo):
        topic, kp = _kp(conn, plan_repo)
        t = repo.create("done task", scheduled_date="2026-01-05",
                        topic_id=topic.id, knowledge_point_id=kp["id"],
                        task_type="new", source="generated")
        repo.mark_done(t.id)
        svc = _svc(conn)
        preview = svc.preview()
        assert preview["aware_from_tasks"] == 1
        assert svc.repo.count_active() == 0  # 未写库

    def test_preview_counts_assessment_and_outcome(self, conn, plan_repo, repo):
        topic, kp = _kp(conn, plan_repo)
        a = AssessmentRepository(conn)
        att = a.create_attempt(kp["id"], json.dumps(
            [{"question": "q", "type": "coding", "expected_points": 2}]
        ))
        a.update_attempt(att["id"], judge_status="judged",
                         mastery_estimate=0.8, result_level="good",
                         ai_result_json=json.dumps({
                             "questions": [{"question_index": 0,
                                            "verdict": "correct",
                                            "reason": "ok"}],
                             "weak_points": [], "result_level": "good",
                             "mastery_estimate": 0.8,
                         }))
        svc = _svc(conn)
        preview = svc.preview()
        assert preview["implement_from_assessments"] == 1
        assert preview["skipped_assessment_insufficient_data"] == 0


class TestApply:
    def test_backfill_all_sources(self, conn, plan_repo, repo):
        # task → AWARE
        topic, kp = _kp(conn, plan_repo, name="LoRA")
        t = repo.create("done", scheduled_date="2026-01-05",
                        topic_id=topic.id, knowledge_point_id=kp["id"],
                        task_type="new", source="generated")
        repo.mark_done(t.id)
        # assessment → IMPLEMENT
        a = AssessmentRepository(conn)
        att = a.create_attempt(kp["id"], json.dumps(
            [{"question": "q", "type": "coding", "expected_points": 2}]
        ))
        a.update_attempt(att["id"], judge_status="judged",
                         mastery_estimate=0.8, result_level="good",
                         ai_result_json=json.dumps({
                             "questions": [{"question_index": 0,
                                            "verdict": "correct",
                                            "reason": "ok"}],
                             "weak_points": [], "result_level": "good",
                             "mastery_estimate": 0.8,
                         }))
        # experiment outcome → EXPERIMENT
        topic2, kp2, = _kp(conn, plan_repo, name="PPO")
        etask = repo.create("exp", scheduled_date="2026-01-05",
                            topic_id=topic2.id, knowledge_point_id=kp2["id"],
                            task_type="new", source="generated",
                            learning_activity_kind="experiment")
        repo.mark_done(etask.id)
        out_repo = LearningOutcomeRepository(conn)
        out_repo.create(kind="experiment", title="exp", content="结果",
                        metrics={"loss": 0.1}, linked_kp_id=kp2["id"],
                        task_id=etask.id)
        svc = _svc(conn)
        stats = svc.backfill()
        assert stats["aware_from_tasks"] >= 2
        assert stats["implement_from_assessments"] == 1
        assert stats["experiment_from_outcomes"] == 1
        assert svc.get_current_level(kp["id"]) == IMPLEMENT
        assert svc.get_current_level(kp2["id"]) == 4

    def test_backfill_idempotent(self, conn, plan_repo, repo):
        topic, kp = _kp(conn, plan_repo)
        t = repo.create("done", scheduled_date="2026-01-05",
                        topic_id=topic.id, knowledge_point_id=kp["id"],
                        task_type="new", source="generated")
        repo.mark_done(t.id)
        svc = _svc(conn)
        svc.backfill()
        n1 = svc.repo.count_active()
        svc.backfill()
        assert svc.repo.count_active() == n1

    def test_insufficient_assessment_skipped(self, conn, plan_repo):
        topic, kp = _kp(conn, plan_repo)
        a = AssessmentRepository(conn)
        att = a.create_attempt(kp["id"], json.dumps(
            [{"question": "q", "type": "concept", "expected_points": 2}]
        ))
        # judged 但无单题 judge 结果（旧数据）
        a.update_attempt(att["id"], judge_status="judged",
                         mastery_estimate=0.9, result_level="excellent",
                         ai_result_json="")
        svc = _svc(conn)
        stats = svc.backfill()
        assert stats["skipped_assessment_insufficient_data"] == 1
        assert svc.get_current_level(kp["id"]) == 0

    def test_mastery_never_used_for_backfill(self, conn, plan_repo):
        topic, kp = _kp(conn, plan_repo)
        AssessmentRepository(conn).update_knowledge_point(
            kp["id"], mastery_estimate=0.95,
            last_assessed_at="2026-01-01T00:00:00",
        )
        svc = _svc(conn)
        stats = svc.backfill()
        assert svc.get_current_level(kp["id"]) == 0
        assert stats["aware_from_tasks"] == 0


class TestSequentialMigration:
    def test_phase1_phase2_phase3_sequential(self, tmp_path):
        """v14 legacy → Phase1 routes → Phase2 activity → Phase3 capability。"""
        from app.database.connection import get_connection
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.skill_repository import SkillRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.database.topic_learning_repository import (
            TopicLearningComponentRepository,
        )
        from app.services.canonical_route_service import CanonicalRouteService
        from app.services.study_plan_service import StudyPlanService
        from app.services.topic_learning_profile_service import (
            TopicLearningProfileService,
        )

        path = tmp_path / "legacy.db"
        conn = get_connection(path)
        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        StudyPlanService(repo, plan_repo).ensure_default_plan()
        route_repo = LearningRouteRepository(conn)
        legacy = route_repo.get_default_learning_route()
        topic = plan_repo.find_topic_by_name_in_route(
            legacy.id, "Function Calling / Tool Calling"
        )
        task = repo.create(
            "FC 学习", scheduled_date="2026-09-05", topic_id=topic.id,
            route_id=legacy.id, source="generated", task_type="new",
        )
        repo.mark_done(task.id)
        a_repo = AssessmentRepository(conn)
        kp = a_repo.get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, topic.description, route_id=legacy.id
        )
        a_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.77,
            last_assessed_at="2026-09-05T10:00:00",
        )
        conn.execute(
            "UPDATE tasks SET knowledge_point_id = ? WHERE id = ?",
            (kp["id"], task.id),
        )
        conn.commit()

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        skill_repo = SkillRepository(conn)
        tl = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo,
                              topic_learning_service=tl).ensure_all()
        cap = _svc(conn)
        stats = cap.backfill()
        # MOVE 无损：kp route 变成 R4，evidence 基于同一 kp
        r4 = route_repo.get_by_key("R4_AI_AGENT")
        assert a_repo.get_knowledge_point(kp["id"])["route_id"] == r4.id
        assert cap.get_current_level(kp["id"]) >= AWARE
        assert stats["aware_from_tasks"] >= 1
        # mastery 不被 capability 修改
        assert float(
            a_repo.get_knowledge_point(kp["id"])["mastery_estimate"]
        ) == 0.77
        conn.close()
