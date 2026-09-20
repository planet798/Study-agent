"""Capability schema v17 测试（Phase 3）。"""

from __future__ import annotations

import sqlite3

from app.services.capability import (
    AWARE,
    CAPABILITY_LABELS,
    EXPLAIN,
    IMPLEMENT,
    PROJECT,
    UNLEARNED,
    capability_label,
    capability_name,
    is_valid_evidence_level,
)


class TestSchemaV17:
    def test_version_and_table(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 17
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "capability_evidence" in tables
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(capability_evidence)"
        )]
        for c in ("knowledge_point_id", "capability_level", "evidence_type",
                  "evidence_key", "source_task_id", "assessment_attempt_id",
                  "learning_outcome_id", "details_json", "is_active",
                  "created_at", "revoked_at", "revocation_reason"):
            assert c in cols
        assert "route_id" not in cols
        assert "practice_project_id" not in cols

    def test_level_check_constraint(self, conn, plan_repo):
        plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31")
        phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                       "2099-12-31")
        topic = plan_repo.create_topic(phase.id, "T")
        with pytest_raises_integrity(conn):
            conn.execute(
                "INSERT INTO capability_evidence "
                "(knowledge_point_id, capability_level, evidence_type,"
                " evidence_key, is_active) VALUES (?, 0, 'x', 'k0', 1)",
                (topic.id,),
            )
        with pytest_raises_integrity(conn):
            conn.execute(
                "INSERT INTO capability_evidence "
                "(knowledge_point_id, capability_level, evidence_type,"
                " evidence_key, is_active) VALUES (?, 6, 'x', 'k6', 1)",
                (topic.id,),
            )

    def test_evidence_key_unique(self, conn, plan_repo):
        plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31")
        phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01",
                                       "2099-12-31")
        topic = plan_repo.create_topic(phase.id, "T")
        conn.execute(
            "INSERT INTO capability_evidence "
            "(knowledge_point_id, capability_level, evidence_type,"
            " evidence_key, is_active, created_at) "
            "VALUES (?, 1, 'learning_activity', 'dup', 1, '')",
            (topic.id,),
        )
        with pytest_raises_integrity(conn):
            conn.execute(
                "INSERT INTO capability_evidence "
                "(knowledge_point_id, capability_level, evidence_type,"
                " evidence_key, is_active, created_at) "
                "VALUES (?, 1, 'learning_activity', 'dup', 1, '')",
                (topic.id,),
            )


def pytest_raises_integrity(conn):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            yield
        except sqlite3.IntegrityError:
            conn.rollback()
            return
        raise AssertionError("expected IntegrityError")
    return _cm()


class TestLevelDefinitions:
    def test_levels_and_labels(self):
        assert UNLEARNED == 0
        assert AWARE == 1 and EXPLAIN == 2 and IMPLEMENT == 3
        assert PROJECT == 5
        assert capability_label(4) == "完成独立实验"
        assert capability_name(3) == "IMPLEMENT"
        assert capability_label(0) == "未学习"

    def test_valid_evidence_level(self):
        assert is_valid_evidence_level(1)
        assert is_valid_evidence_level(5)
        assert not is_valid_evidence_level(0)
        assert not is_valid_evidence_level(6)
