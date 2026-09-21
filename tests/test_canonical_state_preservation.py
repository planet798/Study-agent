"""Stabilization 1.2：Canonical 用户状态保留（priority / pause / archive）。"""

from __future__ import annotations

import pytest

from app.services.canonical_route_service import CanonicalRouteService
from app.services.study_plan_service import StudyPlanService


@pytest.fixture()
def canonical_env(conn):
    from types import SimpleNamespace

    from app.database.learning_route_repository import (
        LearningRouteRepository,
    )
    from app.database.repository import TaskRepository
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository

    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    service = CanonicalRouteService(conn, route_repo, plan_repo, skill_repo)
    service.ensure_all()
    return SimpleNamespace(
        conn=conn, repo=repo, plan_repo=plan_repo, route_repo=route_repo,
        skill_repo=skill_repo, service=service,
    )


def _r3(env):
    return env.route_repo.get_by_key("R3_LLM_INFRA")


class TestRuntimeStatePreservation:
    def test_priority_not_reset_by_reseed(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        assert r3.priority == 3
        env.route_repo.set_priority(r3.id, 5)
        env.service.ensure_all()
        assert _r3(env).priority == 5

    def test_pause_not_reset_by_reseed(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.set_planning_enabled(r3.id, False)
        env.service.ensure_all()
        after = _r3(env)
        assert after.planning_enabled is False
        assert after.status == "active"

    def test_archive_not_restored_by_reseed(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.archive(r3.id)
        env.service.ensure_all()
        after = _r3(env)
        assert after.status == "archived"
        assert after.planning_enabled is False

    def test_archived_at_preserved(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.archive(r3.id)
        archived_at = _r3(env).archived_at
        assert archived_at
        env.service.ensure_all()
        env.service.ensure_all()
        assert _r3(env).archived_at == archived_at

    def test_restore_is_explicit_only(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.archive(r3.id)
        env.service.ensure_all()
        assert _r3(env).status == "archived"
        # 只有显式 restore 才改变
        env.route_repo.restore(r3.id)
        env.service.ensure_all()
        assert _r3(env).status == "active"

    def test_metadata_still_synced(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.set_priority(r3.id, 5)
        env.route_repo.update(
            r3.id, name="TEMP NAME", description="TEMP DESC"
        )
        env.service.ensure_all()
        after = _r3(env)
        # system-owned metadata 被同步回 canonical…
        assert after.name == "R3 LLM Infra"
        assert after.description != "TEMP DESC"
        # …但用户状态保留
        assert after.priority == 5

    def test_repeated_ensure_all_idempotent(self, canonical_env):
        env = canonical_env
        r3 = _r3(env)
        env.route_repo.set_priority(r3.id, 5)
        env.route_repo.set_planning_enabled(r3.id, False)
        snapshot = {}
        for _ in range(3):
            env.service.ensure_all()
            snapshot = {
                r.route_key: (
                    r.name, r.status, r.priority,
                    bool(r.planning_enabled), r.archived_at,
                )
                for r in env.route_repo.list_learning_routes()
            }
        after = {
            r.route_key: (
                r.name, r.status, r.priority,
                bool(r.planning_enabled), r.archived_at,
            )
            for r in env.route_repo.list_learning_routes()
        }
        assert snapshot == after
        assert after["R3_LLM_INFRA"][2] == 5
        assert after["R3_LLM_INFRA"][3] is False

    def test_missing_route_self_heals_with_defaults(self, canonical_env):
        env = canonical_env
        r6 = env.route_repo.get_by_key("R6_CS_FUNDAMENTALS")
        env.route_repo.delete(r6.id)
        assert env.route_repo.get_by_key("R6_CS_FUNDAMENTALS") is None
        env.service.ensure_all()
        healed = env.route_repo.get_by_key("R6_CS_FUNDAMENTALS")
        assert healed is not None
        assert healed.priority == 3
        assert healed.planning_enabled is True
        assert healed.status == "active"

    def test_adoption_preserves_existing_state(self, canonical_env):
        """同名旧 route 被 adoption 时，只补 identity，不重置用户状态。"""
        env = canonical_env
        r5 = env.route_repo.get_by_key("R5_RECOMMENDATION_SEARCH")
        # 模拟“无 route_key 的同名旧 route”
        env.conn.execute(
            "UPDATE learning_routes SET route_key = NULL WHERE id = ?",
            (r5.id,),
        )
        env.route_repo.set_priority(r5.id, 5)
        env.route_repo.set_planning_enabled(r5.id, False)
        env.conn.commit()
        env.service.ensure_all()
        adopted = env.route_repo.get_by_key("R5_RECOMMENDATION_SEARCH")
        assert adopted is not None
        assert adopted.priority == 5
        assert adopted.planning_enabled is False

    def test_group_priority_not_overwritten(self, canonical_env):
        env = canonical_env
        group = env.route_repo.get_by_key("JOB_PREP")
        env.route_repo.set_priority(group.id, 1)
        env.service.ensure_all()
        assert env.route_repo.get_by_key("JOB_PREP").priority == 1


class TestSchedulerRespectsUserState:
    def _scheduler(self, env):
        from app.services.route_scheduler import GlobalDailyScheduler

        return GlobalDailyScheduler(
            env.repo, env.plan_repo, env.route_repo,
            assessment_repo=None, topic_learning_service=None, budget=0,
        )

    def test_scheduler_reads_user_priority(self, practice_readiness_env):
        env = practice_readiness_env
        env.route_repo.set_priority(env.r3.id, 5)
        env.conn.commit()
        # 用 canonical seed 再跑一次，确认 priority 不会被重置
        from app.services.canonical_route_service import CanonicalRouteService
        from app.database.study_plan_repository import StudyPlanRepository
        CanonicalRouteService(
            env.conn, env.route_repo,
            StudyPlanRepository(env.conn), env.skill_repo,
        ).ensure_all()
        assert env.route_repo.get(env.r3.id).priority == 5
        sched = self._scheduler(env)
        out = sched.generate("2026-09-15")
        alloc = {a.route_id: a for a in out["allocations"]}
        assert alloc[env.r3.id].priority == 5

    def test_paused_route_semantics_persist(self, practice_readiness_env):
        env = practice_readiness_env
        env.route_repo.set_planning_enabled(env.r3.id, False)
        from app.services.canonical_route_service import CanonicalRouteService
        from app.database.study_plan_repository import StudyPlanRepository
        CanonicalRouteService(
            env.conn, env.route_repo,
            StudyPlanRepository(env.conn), env.skill_repo,
        ).ensure_all()
        r3 = env.route_repo.get(env.r3.id)
        assert r3.planning_enabled is False
        sched = self._scheduler(env)
        assert sched.route_skip_reason(r3, "2026-09-15") == "planning_paused"
        assert env.r3.id not in [
            r.id for r in sched.plannable_routes("2026-09-15")
        ]

    def test_archived_route_not_plannable(self, practice_readiness_env):
        env = practice_readiness_env
        env.route_repo.archive(env.r3.id)
        from app.services.canonical_route_service import CanonicalRouteService
        from app.database.study_plan_repository import StudyPlanRepository
        CanonicalRouteService(
            env.conn, env.route_repo,
            StudyPlanRepository(env.conn), env.skill_repo,
        ).ensure_all()
        r3 = env.route_repo.get(env.r3.id)
        assert r3.status == "archived"
        sched = self._scheduler(env)
        assert sched.route_skip_reason(r3, "2026-09-15") == "archived"
        assert env.r3.id not in [
            r.id for r in sched.plannable_routes("2026-09-15")
        ]


class TestEnsureDefaultPlanSemantics:
    def test_canonical_global_returns_none(self, canonical_env):
        env = canonical_env
        sps = StudyPlanService(
            env.repo, env.plan_repo, learning_route_repo=env.route_repo
        )
        assert sps.ensure_default_plan() is None

    def test_explicit_route_still_works(self, canonical_env):
        env = canonical_env
        r1 = env.route_repo.get_by_key("R1_LLM_FUNDAMENTALS")
        sps = StudyPlanService(
            env.repo, env.plan_repo, route_id=r1.id,
            learning_route_repo=env.route_repo,
        )
        plan = sps.ensure_default_plan()
        assert plan is not None
        assert plan.route_id == r1.id

    def test_legacy_db_still_compatible(self, conn):
        from app.database.assessment_repository import AssessmentRepository
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.study_plan_repository import StudyPlanRepository

        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        sps = StudyPlanService(
            TaskRepository(conn), plan_repo,
            assessment_repo=AssessmentRepository(conn),
            learning_route_repo=route_repo,
        )
        plan = sps.ensure_default_plan()
        assert plan is not None
        # legacy seed 仍创建 6 个阶段
        assert len(plan_repo.list_phases(plan.id)) == 6


class TestMigrationGateFailClosed:
    def test_unreadable_existing_db_blocked(self, tmp_path):
        import app.main as m

        bad = tmp_path / "bad.db"
        bad.write_bytes(b"not a sqlite database" * 32)
        status = m.migration_gate_status(str(bad))
        assert status["blocked"] is True
        assert status["reason"] == "unreadable_db"
        msg = m.migration_gate_message(status)
        assert "无法安全读取" in msg
        assert "fail closed" in msg

    def test_missing_db_allowed(self, tmp_path):
        import app.main as m

        status = m.migration_gate_status(str(tmp_path / "nope.db"))
        assert status["blocked"] is False
        assert status["reason"] == "new_db"

    def test_empty_db_allowed(self, tmp_path):
        import app.main as m

        empty = tmp_path / "empty.db"
        empty.write_bytes(b"")
        status = m.migration_gate_status(str(empty))
        assert status["blocked"] is False

    def test_main_blocks_unreadable(self, tmp_path, monkeypatch):
        import io
        from contextlib import redirect_stdout

        import app.main as m
        from app.utils import date_utils

        bad = tmp_path / "bad.db"
        bad.write_bytes(b"garbage" * 64)
        monkeypatch.setattr(m, "resolve_db_path", lambda *a, **k: str(bad))
        monkeypatch.setattr(
            m.sys, "argv", ["study-agent", "--date", "2026-09-15"]
        )
        original = date_utils.today()
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = m.main()
        finally:
            date_utils.reset_today_provider()
        assert code == 3
        assert "无法安全读取" in buf.getvalue()
        assert date_utils.today() == original
        # 文件未被触碰
        assert bad.read_bytes() == b"garbage" * 64


class TestHardeningStillIntact:
    """aa14cda 迁移加固不回归（关键不变量）。"""

    def test_readonly_connection_query_only(self, canonical_env, tmp_path):
        from app.database.connection import get_readonly_connection

        db = tmp_path / "x.db"
        src = canonical_env.conn
        dst = __import__("sqlite3").connect(str(db))
        with dst:
            src.backup(dst)
        dst.close()
        conn = get_readonly_connection(str(db))
        try:
            assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        finally:
            conn.close()

    def test_backup_api_and_preflight_exist(self):
        from app.diagnostics import release_migration as rm

        for name in ("backup_database", "_sqlite_backup", "dry_run_on_copy",
                     "source_preflight_problems", "fingerprint",
                     "integrity_check", "foreign_key_check",
                     "readonly_copy", "working_copy"):
            assert hasattr(rm, name), name
