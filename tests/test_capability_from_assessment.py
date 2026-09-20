"""Assessment → EXPLAIN / IMPLEMENT capability 测试（Phase 3）。"""

from __future__ import annotations

import json

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.services.capability import EXPLAIN, IMPLEMENT
from app.services.capability_extractors import extract_assessment_capability
from app.services.capability_service import CapabilityService


def _kp(conn, plan_repo, route_id=None, name="LoRA"):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31",
                                 route_id=route_id)
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                   "2099-12-31")
    topic = plan_repo.create_topic(phase.id, name)
    a = AssessmentRepository(conn)
    return a.get_or_create_knowledge_point_for_topic(
        topic.id, name, "", route_id=route_id
    )


def _judged_attempt(conn, a_repo, kp_id, pairs, result_level="good",
                    mastery=0.8, judge_status="judged", with_ai=True):
    """pairs: [(question_type, verdict)]"""
    questions = [
        {"question": f"q{i}", "type": t, "expected_points": 2}
        for i, (t, _v) in enumerate(pairs)
    ]
    att = a_repo.create_attempt(kp_id, json.dumps(questions))
    ai = {
        "questions": [
            {"question_index": i, "verdict": v, "reason": "r"}
            for i, (_t, v) in enumerate(pairs)
        ],
        "weak_points": [],
        "result_level": result_level,
        "mastery_estimate": mastery,
    }
    a_repo.update_attempt(
        att["id"],
        ai_result_json=json.dumps(ai) if with_ai else "",
        judge_status=judge_status,
        mastery_estimate=mastery,
        result_level=result_level,
    )
    return a_repo.get_attempt(att["id"])


def _svc(conn):
    return CapabilityService(conn, CapabilityEvidenceRepository(conn))


class TestExtractor:
    def test_concept_pass_explain(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "correct")])
        out = extract_assessment_capability(att)
        assert out["level"] == EXPLAIN
        assert "concept" in out["details"]["supported_types"]

    def test_code_reading_pass_explain(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("code_reading", "correct")])
        assert extract_assessment_capability(att)["level"] == EXPLAIN

    def test_scenario_pass_explain(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("scenario", "correct")])
        assert extract_assessment_capability(att)["level"] == EXPLAIN

    def test_debug_pass_conservative_explain(self, conn, plan_repo,
                                             assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("debug", "correct")])
        # 无法证明“写出了代码” → 保守 EXPLAIN
        assert extract_assessment_capability(att)["level"] == EXPLAIN

    def test_coding_pass_implement(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("coding", "correct")])
        assert extract_assessment_capability(att)["level"] == IMPLEMENT

    def test_coding_fail_no_implement(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("coding", "incorrect")])
        assert extract_assessment_capability(att) is None

    def test_concept_pass_coding_fail_explain(self, conn, plan_repo,
                                              assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "correct"), ("coding", "incorrect")])
        assert extract_assessment_capability(att)["level"] == EXPLAIN

    def test_strongest_only_one_row(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "correct"),
                               ("code_reading", "correct"),
                               ("coding", "correct")])
        svc = _svc(conn)
        ev = svc.sync_from_assessment(att["id"])
        assert ev["capability_level"] == IMPLEMENT
        rows = svc.repo.list_by_kp(kp["id"])
        assert len(rows) == 1
        assert set(rows[0]["details"]["supported_types"]) >= {
            "concept", "code_reading", "coding"
        }

    def test_partial_not_pass(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "partial")])
        assert extract_assessment_capability(att) is None

    def test_pending_not_judged(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "correct")],
                              judge_status="pending")
        assert extract_assessment_capability(att) is None

    def test_missing_ai_result_skipped(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "correct")], with_ai=False)
        assert extract_assessment_capability(att) is None

    def test_mastery_high_without_pass_is_none(self, conn, plan_repo,
                                               assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("concept", "incorrect")], mastery=0.99)
        assert extract_assessment_capability(att) is None


class TestSync:
    def test_sync_and_idempotent(self, conn, plan_repo, assessment_repo):
        kp = _kp(conn, plan_repo)
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("coding", "correct")])
        svc = _svc(conn)
        e1 = svc.sync_from_assessment(att["id"])
        e2 = svc.sync_from_assessment(att["id"])
        assert e1["id"] == e2["id"]
        assert len(svc.repo.list_by_kp(kp["id"])) == 1

    def test_later_failure_no_downgrade(self, conn, plan_repo,
                                        assessment_repo):
        kp = _kp(conn, plan_repo)
        svc = _svc(conn)
        good = _judged_attempt(conn, assessment_repo, kp["id"],
                               [("coding", "correct")])
        svc.sync_from_assessment(good["id"])
        bad = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("coding", "incorrect")])
        svc.sync_from_assessment(bad["id"])
        assert svc.get_current_level(kp["id"]) == IMPLEMENT

    def test_mastery_unchanged_by_sync(self, conn, plan_repo,
                                       assessment_repo):
        kp = _kp(conn, plan_repo)
        assessment_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.61, last_assessed_at="2026-01-01T00:00:00"
        )
        att = _judged_attempt(conn, assessment_repo, kp["id"],
                              [("coding", "correct")])
        _svc(conn).sync_from_assessment(att["id"])
        assert float(
            assessment_repo.get_knowledge_point(kp["id"])["mastery_estimate"]
        ) == 0.61

    def test_route_isolation(self, conn, plan_repo, assessment_repo):
        rrepo = LearningRouteRepository(conn)
        r1 = rrepo.create("R1", route_key=None)
        r2 = rrepo.create("R2", route_key=None)
        kp1 = _kp(conn, plan_repo, route_id=r1.id, name="基础")
        kp2 = _kp(conn, plan_repo, route_id=r2.id, name="基础")
        svc = _svc(conn)
        att = _judged_attempt(conn, assessment_repo, kp1["id"],
                              [("coding", "correct")])
        svc.sync_from_assessment(att["id"])
        assert svc.get_current_level(kp1["id"]) == IMPLEMENT
        assert svc.get_current_level(kp2["id"]) == 0
