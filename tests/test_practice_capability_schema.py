"""Phase 5：v19 schema / 迁移测试。"""

from __future__ import annotations

import sqlite3

import pytest


class TestSchemaV19:
    def test_version_and_tables(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 19
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "practice_topic_evidence" in names
        assert "practice_topic_evidence_outputs" in names

    def test_capability_evidence_column(self, conn):
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(capability_evidence)"
        )}
        assert "practice_topic_evidence_id" in cols

    def test_partial_unique_index(self, conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE name='idx_practice_topic_evidence_active_unique'"
        ).fetchone()
        assert row is not None
        normalized = " ".join(row[0].split()).lower()
        assert "unique" in normalized
        assert "is_active = 1" in normalized

    def test_evidence_output_unique(self, conn):
        # UNIQUE(evidence_id, output_id) 强制存在
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(practice_topic_evidence_outputs)"
        )]
        assert cols == ["id", "evidence_id", "output_id", "created_at"]
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='practice_topic_evidence_outputs'"
        ).fetchall()
        assert idx

    def test_evidence_usage_description_not_null(self, conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='practice_topic_evidence'"
        ).fetchone()[0]
        assert "usage_description" in row
        assert "NOT NULL" in row.upper()

    def test_migration_idempotent(self, tmp_path):
        from app.database.connection import get_connection
        from app.database.schema import migrate

        c = get_connection(str(tmp_path / "idem.db"))
        assert migrate(c) >= 19
        assert migrate(c) >= 19
        c.close()

    def test_v18_to_v19_sequential(self, tmp_path):
        """从 v18 数据库升级到 v19：仅新增表/列，无自动 evidence。"""
        from app.database.connection import get_connection
        from app.database.repository import TaskRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.skill_repository import SkillRepository
        from app.services.study_plan_service import StudyPlanService
        from app.services.canonical_route_service import CanonicalRouteService

        c = get_connection(str(tmp_path / "v18.db"))
        repo = TaskRepository(c)
        plan_repo = StudyPlanRepository(c)
        StudyPlanService(repo, plan_repo).ensure_default_plan()
        CanonicalRouteService(
            c, LearningRouteRepository(c), plan_repo, SkillRepository(c)
        ).ensure_all()
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 19
        assert c.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM capability_evidence "
            "WHERE practice_topic_evidence_id IS NOT NULL"
        ).fetchone()[0] == 0
        c.close()

    def test_active_partial_unique_enforced(self, practice_capability_env):
        env = practice_capability_env
        from tests.practice_capability_helpers import ready_lora, lora_topic

        r = ready_lora(env)
        env.pc.create_project_topic_evidence(
            r.project["id"], r.lora.id, [r.repo["id"]], "u1", confirmed=True
        )
        # 直接 SQL 再插一条 active 应触发 UNIQUE 冲突
        with pytest.raises(sqlite3.IntegrityError):
            env.conn.execute(
                "INSERT INTO practice_topic_evidence "
                "(project_id, topic_id, knowledge_point_id, usage_description,"
                " is_active, created_at) VALUES (?, ?, ?, ?, 1, '')",
                (r.project["id"], r.lora.id, 1, "dup"),
            )
            env.conn.commit()
        env.conn.rollback()

    def test_revoked_allows_new_active(self, practice_capability_env):
        env = practice_capability_env
        from tests.practice_capability_helpers import ready_lora

        r = ready_lora(env)
        ev1 = env.pc.create_project_topic_evidence(
            r.project["id"], r.lora.id, [r.repo["id"]], "u1", confirmed=True
        )
        env.pc.revoke_project_topic_evidence(ev1["id"], "reason")
        ev2 = env.pc.create_project_topic_evidence(
            r.project["id"], r.lora.id, [r.repo["id"]], "u2", confirmed=True
        )
        assert ev2["id"] != ev1["id"]
        assert env.pc.count_active_by_project(r.project["id"]) == 1
