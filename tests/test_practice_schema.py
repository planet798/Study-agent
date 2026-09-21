"""Practice schema v18 测试（Phase 4）。"""

from __future__ import annotations

import sqlite3


class TestSchemaV18:
    def test_version_and_tables(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 18
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for t in ("practice_projects", "practice_project_routes",
                  "practice_project_skills", "practice_project_topics",
                  "practice_milestones", "practice_outputs"):
            assert t in tables

    def test_project_type_check(self, conn):
        with _integrity(conn):
            conn.execute(
                "INSERT INTO practice_projects (name, project_type, status,"
                " source, created_at, updated_at) "
                "VALUES ('x', 'bogus', 'planned', 'manual', '', '')"
            )

    def test_status_check(self, conn):
        with _integrity(conn):
            conn.execute(
                "INSERT INTO practice_projects (name, project_type, status,"
                " source, created_at, updated_at) "
                "VALUES ('x', 'other', 'bogus', 'manual', '', '')"
            )

    def test_milestone_status_check(self, conn, practice_env):
        p = practice_env.service.create_project("P", "other")
        with _integrity(conn):
            conn.execute(
                "INSERT INTO practice_milestones (project_id, title, status,"
                " order_index, created_at, updated_at) "
                "VALUES (?, 'm', 'bogus', 1, '', '')",
                (p["id"],),
            )

    def test_output_type_check(self, conn, practice_env):
        p = practice_env.service.create_project("P", "other")
        with _integrity(conn):
            conn.execute(
                "INSERT INTO practice_outputs (project_id, output_type, title,"
                " created_at, updated_at) VALUES (?, 'bogus', 't', '', '')",
                (p["id"],),
            )

    def test_route_relation_unique(self, conn, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        # 再次插入同 (project, route) 应冲突
        with _integrity(conn):
            conn.execute(
                "INSERT INTO practice_project_routes (project_id, route_id,"
                " created_at) VALUES (?, ?, '')", (p["id"], env.r1.id),
            )

    def test_project_name_not_unique(self, practice_env):
        env = practice_env
        a = env.service.create_project("推荐系统项目", "recommendation_search")
        b = env.service.create_project("推荐系统项目", "recommendation_search")
        assert a["id"] != b["id"]


def _integrity(conn):
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


class TestSequentialMigrationV18:
    def test_phase1_to_phase4_no_auto_projects(self, tmp_path):
        from app.database.connection import get_connection
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.skill_repository import SkillRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.canonical_route_service import CanonicalRouteService
        from app.services.study_plan_service import StudyPlanService

        path = tmp_path / "legacy.db"
        conn = get_connection(path)
        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        StudyPlanService(repo, plan_repo).ensure_default_plan()
        route_repo = LearningRouteRepository(conn)
        skill_repo = SkillRepository(conn)
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo).ensure_all()
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 18
        # v18 只建表，不自动创建任何用户 Project
        assert conn.execute(
            "SELECT COUNT(*) FROM practice_projects"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM practice_milestones"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM practice_outputs"
        ).fetchone()[0] == 0
        conn.close()

    def test_migration_idempotent(self, tmp_path):
        from app.database.connection import get_connection
        from app.database.schema import migrate

        path = tmp_path / "idem.db"
        conn = get_connection(path)
        assert migrate(conn) >= 18
        assert migrate(conn) >= 18
        conn.close()
