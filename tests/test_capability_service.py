"""CapabilityService 核心测试（Phase 3）。"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.services.capability import AWARE, EXPLAIN, IMPLEMENT, UNLEARNED
from app.services.capability_service import CapabilityService


def _kp(conn, plan_repo, route_id=None, name="LoRA"):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31",
                                 route_id=route_id)
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                   "2099-12-31")
    topic = plan_repo.create_topic(phase.id, name)
    repo = AssessmentRepository(conn)
    return repo.get_or_create_knowledge_point_for_topic(
        topic.id, name, "", route_id=route_id
    )


class TestCurrentLevel:
    def test_no_evidence_is_zero_and_not_stored(self, conn, capability_service):
        kp = _kp(conn, __import__("app.database.study_plan_repository",
                                  fromlist=["StudyPlanRepository"])
                 .StudyPlanRepository(conn))
        assert capability_service.get_current_level(kp["id"]) == UNLEARNED
        assert capability_service.repo.get_by_key("nope") is None
        assert capability_service.repo.count_active() == 0

    def test_max_of_active(self, conn, plan_repo, capability_service):
        kp = _kp(conn, plan_repo)
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=AWARE,
            evidence_type="learning_activity", evidence_key="task:1",
        )
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=EXPLAIN,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        assert capability_service.get_current_level(kp["id"]) == EXPLAIN
        cap = capability_service.get_current_capability(kp["id"])
        assert cap["label"] == "能够解释"
        assert cap["evidence_count"] == 2

    def test_strongest_only_no_downgrade(self, conn, plan_repo, capability_service):
        kp = _kp(conn, plan_repo)
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=IMPLEMENT,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        # 同一 key 再来一次低级别 → 不降级
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=AWARE,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        assert capability_service.get_current_level(kp["id"]) == IMPLEMENT
        assert len(capability_service.repo.list_by_kp(kp["id"])) == 1

    def test_higher_evidence_upgrades_same_key(self, conn, plan_repo,
                                               capability_service):
        kp = _kp(conn, plan_repo)
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=AWARE,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=IMPLEMENT,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        assert capability_service.get_current_level(kp["id"]) == IMPLEMENT
        assert len(capability_service.repo.list_by_kp(kp["id"])) == 1


class TestRevoke:
    def test_revoke_reduces_current_but_keeps_row(self, conn, plan_repo,
                                                  capability_service):
        kp = _kp(conn, plan_repo)
        e1 = capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=AWARE,
            evidence_type="learning_activity", evidence_key="task:1",
        )
        e3 = capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=IMPLEMENT,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        capability_service.revoke_evidence(e3["id"], "录错")
        assert capability_service.get_current_level(kp["id"]) == AWARE
        # 不物理删除
        assert capability_service.repo.get(e3["id"]) is not None
        assert capability_service.repo.get(e3["id"])["is_active"] is False
        assert capability_service.repo.get(e3["id"])["revocation_reason"] == "录错"

    def test_revoke_all_returns_zero(self, conn, plan_repo, capability_service):
        kp = _kp(conn, plan_repo)
        e = capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=AWARE,
            evidence_type="learning_activity", evidence_key="task:1",
        )
        capability_service.revoke_evidence(e["id"])
        assert capability_service.get_current_level(kp["id"]) == UNLEARNED


class TestNoMasteryInference:
    def test_high_mastery_no_evidence_is_zero(self, conn, plan_repo,
                                              capability_service):
        kp = _kp(conn, plan_repo)
        AssessmentRepository(conn).update_knowledge_point(
            kp["id"], mastery_estimate=0.95, last_assessed_at="2026-01-01T00:00:00"
        )
        assert capability_service.get_current_level(kp["id"]) == UNLEARNED


class TestRepository:
    def test_route_summary_and_level_counts(self, conn, plan_repo,
                                            capability_service):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )

        route = LearningRouteRepository(conn).create("R", route_key=None)
        kp = _kp(conn, plan_repo, route_id=route.id)
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=IMPLEMENT,
            evidence_type="assessment", evidence_key="assessment:1",
        )
        summary = capability_service.get_route_capability_summary(route.id)
        assert summary["total"] == 1
        assert summary["level_counts"][3] == 1
        # 没有 average capability 字段
        assert "average" not in summary

    def test_repo_route_via_kp(self, conn, plan_repo, capability_service):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )

        route = LearningRouteRepository(conn).create("R", route_key=None)
        kp = _kp(conn, plan_repo, route_id=route.id)
        capability_service.repo.create_or_update_by_key(
            knowledge_point_id=kp["id"], capability_level=EXPLAIN,
            evidence_type="assessment", evidence_key="assessment:9",
        )
        rows = capability_service.repo.list_by_route_via_kp(route.id)
        assert len(rows) == 1
        assert capability_service.repo.max_level_by_kp(kp["id"]) == EXPLAIN
