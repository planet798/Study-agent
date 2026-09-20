"""Task → AWARE capability 测试（Phase 3）。"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.services.capability import AWARE
from app.services.capability_service import CapabilityService
from app.services.past_task_service import PastTaskConfirmationService
from app.services.task_service import TaskService


def _setup(conn, plan_repo, route_id=None, name="LoRA"):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31",
                                 route_id=route_id)
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                   "2099-12-31")
    topic = plan_repo.create_topic(phase.id, name)
    a = AssessmentRepository(conn)
    kp = a.get_or_create_knowledge_point_for_topic(
        topic.id, name, "", route_id=route_id
    )
    return topic, kp


def _svc(conn, repo):
    cap = CapabilityService(conn, CapabilityEvidenceRepository(conn))
    return cap, TaskService(repo, capability_service=cap)


class TestTaskToAware:
    def test_done_formal_task_gives_aware(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("LoRA 理论", scheduled_date="2026-01-05",
                        topic_id=topic.id, knowledge_point_id=kp["id"],
                        task_type="new", source="generated",
                        learning_activity_kind="theory")
        ts.complete_task(t.id)
        assert cap.get_current_level(kp["id"]) == AWARE

    def test_active_not_done_cancelled_do_not_produce(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("T", scheduled_date="2026-01-05", topic_id=topic.id,
                        knowledge_point_id=kp["id"], task_type="new",
                        source="generated")
        assert cap.sync_from_task(t.id) is None  # active
        ts.mark_not_done(t.id, "没时间")
        assert cap.sync_from_task(t.id) is None  # not_done
        t2 = repo.create("T2", scheduled_date="2026-01-05", topic_id=topic.id,
                         knowledge_point_id=kp["id"], task_type="new",
                         source="generated")
        ts.cancel_task(t2.id)
        assert cap.sync_from_task(t2.id) is None  # cancelled

    def test_review_done_does_not_produce(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("复习", scheduled_date="2026-01-05", topic_id=topic.id,
                        knowledge_point_id=kp["id"], task_type="review",
                        source="review")
        ts.complete_task(t.id)
        assert cap.get_current_level(kp["id"]) == 0

    def test_manual_todo_does_not_produce(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("刷邮件", scheduled_date="2026-01-05",
                        task_type="manual", source="manual")
        ts.complete_task(t.id)
        assert cap.repo.count_active() == 0

    def test_formal_manual_knowledge_produces(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("手动学习", scheduled_date="2026-01-05",
                        topic_id=topic.id, knowledge_point_id=kp["id"],
                        task_type="new", source="manual")
        ts.complete_task(t.id)
        assert cap.get_current_level(kp["id"]) == AWARE

    def test_no_kp_does_not_produce(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("无 kp", scheduled_date="2026-01-05",
                        topic_id=topic.id, task_type="new", source="generated")
        ts.complete_task(t.id)
        assert cap.repo.count_active() == 0

    def test_task_evidence_idempotent(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("T", scheduled_date="2026-01-05", topic_id=topic.id,
                        knowledge_point_id=kp["id"], task_type="new",
                        source="generated")
        ts.complete_task(t.id)
        ts2 = cap.sync_from_task(t.id)
        assert ts2["evidence_key"] == f"task:{t.id}"
        assert len(cap.repo.list_by_kp(kp["id"])) == 1

    def test_all_activity_kinds_max_aware(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        for i, kind in enumerate(
            ["theory", "code_reading", "experiment", "interview", "practice"]
        ):
            t = repo.create(f"T{i}", scheduled_date="2026-01-05",
                            topic_id=topic.id, knowledge_point_id=kp["id"],
                            task_type="new", source="generated",
                            learning_activity_kind=kind)
            ts.complete_task(t.id)
        assert cap.get_current_level(kp["id"]) == AWARE


class TestAllCompletionEntryPoints:
    def test_past_confirmation_done_produces_aware(self, conn, plan_repo, repo):
        topic, kp = _setup(conn, plan_repo)
        cap, ts = _svc(conn, repo)
        t = repo.create("昨天", scheduled_date="2026-01-04", topic_id=topic.id,
                        knowledge_point_id=kp["id"], task_type="new",
                        source="generated")
        past = PastTaskConfirmationService(repo, ts)
        past.apply_decisions({t.id: "done"})
        assert cap.get_current_level(kp["id"]) == AWARE


class TestRouteIsolation:
    def test_same_name_kp_different_routes(self, conn, plan_repo, repo):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )

        rrepo = LearningRouteRepository(conn)
        r1 = rrepo.create("R1", route_key=None)
        r2 = rrepo.create("R2", route_key=None)
        topic1, kp1 = _setup(conn, plan_repo, route_id=r1.id, name="基础")
        topic2, kp2 = _setup(conn, plan_repo, route_id=r2.id, name="基础")
        assert kp1["id"] != kp2["id"]
        cap, ts = _svc(conn, repo)
        t = repo.create("T", scheduled_date="2026-01-05",
                        topic_id=topic1.id, knowledge_point_id=kp1["id"],
                        route_id=r1.id, task_type="new", source="generated")
        ts.complete_task(t.id)
        assert cap.get_current_level(kp1["id"]) == AWARE
        assert cap.get_current_level(kp2["id"]) == 0
