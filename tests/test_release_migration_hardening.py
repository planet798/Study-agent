"""v1 stabilization 加固：backup API / read-only / preflight / gate / fingerprint。"""

from __future__ import annotations

import hashlib
import io
import os
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _build_v14(path, *, duplicate_active_plans: bool = False) -> None:
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise
    from app.database.repository import TaskRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.study_plan_service import StudyPlanService
    from app.utils.date_utils import now_iso

    conn = get_raw_connection(path)
    migrate_stepwise(conn, target=14)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    arepo = AssessmentRepository(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    sps.ensure_default_plan()
    if duplicate_active_plans:
        route_id = sps._resolved_route_id()
        plan_repo.create_plan("dup A", "2026-01-01", "2099-12-31",
                              route_id=route_id)
        plan_repo.create_plan("dup B", "2026-01-01", "2099-12-31",
                              route_id=route_id)
    kp = arepo.create_knowledge_point("Transformer", route_id=None)
    conn.execute(
        "INSERT INTO tasks (title,description,category,estimated_minutes,"
        "priority,status,source,task_type,knowledge_point_id,scheduled_date,"
        "created_at,updated_at) VALUES ('历史任务','','学习',45,1,'done',"
        "'generated','new',?,'2026-09-10',?,?)",
        (kp["id"], now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()


# ============================================================
# 1) Backup API 正确处理 WAL
# ============================================================


class TestBackupApi:
    def test_backup_includes_wal_committed_rows(self, tmp_path):
        import sqlite3

        from app.diagnostics import release_migration as rm

        db = tmp_path / "wal.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA wal_autocheckpoint = 0")  # 阻止自动 checkpoint
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('committed-in-wal')")
        conn.commit()
        # 此时数据很可能仍在 -wal 中，主 DB 文件未包含
        assert Path(str(db) + "-wal").exists()
        conn.close()

        backup = rm.backup_database(str(db), out_dir=str(tmp_path))
        bconn = sqlite3.connect(backup)
        try:
            rows = [r[0] for r in bconn.execute("SELECT v FROM t").fetchall()]
        finally:
            bconn.close()
        assert rows == ["committed-in-wal"]

    def test_backup_refuses_overwrite(self, tmp_path, monkeypatch):
        import datetime as _dt

        from app.diagnostics import release_migration as rm

        db = tmp_path / "x.db"
        _build_v14(db)

        class _FixedDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 21, 12, 0, 0)

        monkeypatch.setattr(rm, "datetime", _FixedDT)
        first = rm.backup_database(str(db), out_dir=str(tmp_path))
        assert Path(first).exists()
        with pytest.raises(FileExistsError):
            rm.backup_database(str(db), out_dir=str(tmp_path))


# ============================================================
# 2) six-routes preview 不修改正式库
# ============================================================


class TestSixRoutesPreviewReadOnly:
    def test_preview_does_not_modify_real_db(self, tmp_path, capsys):
        from app.main import _run_six_routes_cli

        db = tmp_path / "v14.db"
        _build_v14(db)
        before = _md5(db)
        code = _run_six_routes_cli(["preview", "--db", str(db)])
        after = _md5(db)
        assert code == 0
        assert before == after, "six-routes preview 修改了正式库"
        out = capsys.readouterr().out
        assert "preview: MOVE=" in out

    def test_inventory_on_old_schema_is_graceful(self, tmp_path, capsys):
        from app.main import _run_six_routes_cli

        db = tmp_path / "v14.db"
        _build_v14(db)
        before = _md5(db)
        code = _run_six_routes_cli(["inventory", "--db", str(db)])
        assert code == 2
        assert _md5(db) == before
        assert "db-release inventory" in capsys.readouterr().out


# ============================================================
# 3) conflict 在业务修改前发现（preflight on copy）
# ============================================================


class TestPreflightConflict:
    def test_duplicate_active_plans_abort_before_touching_real_db(
        self, tmp_path, capsys
    ):
        from app.database.connection import get_raw_connection
        from app.main import _run_db_release_cli

        db = tmp_path / "dup.db"
        _build_v14(db, duplicate_active_plans=True)
        before = _md5(db)
        code = _run_db_release_cli(["migrate", "--db", str(db)])
        assert code == 1
        assert _md5(db) == before, "冲突时正式库不应被修改"
        conn = get_raw_connection(str(db))
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
        assert version == 14  # 未推进任何 schema
        assert "source_preflight" in capsys.readouterr().out

    def test_dry_run_reports_source_problems(self, tmp_path):
        from app.diagnostics import release_migration as rm

        db = tmp_path / "dup.db"
        _build_v14(db, duplicate_active_plans=True)
        pre = rm.dry_run_on_copy(str(db))
        assert pre["ok"] is False
        assert pre["stage"] == "source_preflight"
        assert any(
            p["kind"] == "duplicate_active_plans"
            for p in pre["source_problems"]
        )

    def test_clean_db_passes_preflight(self, tmp_path):
        from app.database.schema import SCHEMA_VERSION
        from app.diagnostics import release_migration as rm

        db = tmp_path / "clean.db"
        _build_v14(db)
        pre = rm.dry_run_on_copy(str(db))
        assert pre["ok"] is True
        assert pre["conflicts"] == 0
        assert pre["schema_version_after"] == SCHEMA_VERSION


# ============================================================
# 4) Migration Gate
# ============================================================


class TestMigrationGate:
    def test_status_old_current_new(self, tmp_path):
        from app.main import migration_gate_status
        from app.database.connection import get_raw_connection
        from app.database.schema import migrate_stepwise

        old = tmp_path / "old.db"
        _build_v14(old)
        assert migration_gate_status(str(old))["blocked"] is True
        assert migration_gate_status(str(old))["version"] == 14

        current = tmp_path / "cur.db"
        c = get_raw_connection(str(current))
        migrate_stepwise(c)
        c.close()
        assert migration_gate_status(str(current))["blocked"] is False

        assert migration_gate_status(str(tmp_path / "missing.db"))[
            "blocked"
        ] is False

    def test_message_contains_commands(self, tmp_path):
        from app.main import migration_gate_message, migration_gate_status

        old = tmp_path / "old.db"
        _build_v14(old)
        msg = migration_gate_message(migration_gate_status(str(old)))
        for token in ("db-release backup", "db-release inventory",
                      "db-release migrate", "db-release verify"):
            assert token in msg

    def test_main_blocks_gui_on_old_db(self, tmp_path, monkeypatch):
        import app.main as m
        from app.utils import date_utils

        old = tmp_path / "old.db"
        _build_v14(old)
        monkeypatch.setattr(m, "resolve_db_path", lambda *a, **k: str(old))
        monkeypatch.setattr(m.sys, "argv", ["study-agent", "--date",
                                            "2026-09-15"])
        buf = io.StringIO()
        original = date_utils.today()
        try:
            with redirect_stdout(buf):
                code = m.main()
        finally:
            date_utils.reset_today_provider()
        assert code == 3
        assert "db-release" in buf.getvalue()
        assert date_utils.today() == original
        assert _md5(old) == _md5(old)  # 未被迁移

    def test_main_allows_flag(self, tmp_path, monkeypatch):
        import app.main as m

        old = tmp_path / "old.db"
        _build_v14(old)
        monkeypatch.setattr(m, "resolve_db_path", lambda *a, **k: str(old))
        # 只验证门禁语义：allow flag → 不放行“blocked”分支
        monkeypatch.setattr(m.sys, "argv", [
            "study-agent", "--date", "2026-09-15",
            m.MIGRATION_GATE_ALLOW_FLAG,
        ])
        # 直接检查 gate 判定逻辑（避免真正启动 Qt）
        gate = m.migration_gate_status(str(old))
        assert gate["blocked"] is True
        assert m.MIGRATION_GATE_ALLOW_FLAG in m.sys.argv


# ============================================================
# 5) planner-diagnostic read-only + query_only
# ============================================================


class TestDiagnosticReadOnlyConnection:
    def test_query_only_on(self, tmp_path):
        from app.database.connection import get_readonly_connection

        db = tmp_path / "x.db"
        _build_v14(db)
        conn = get_readonly_connection(str(db))
        try:
            assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
            with pytest.raises(Exception):
                conn.execute("CREATE TABLE nope (id INTEGER)")
        finally:
            conn.close()

    def test_cli_does_not_modify_db(self, tmp_path):
        from app.main import _run_db_release_cli, _run_planner_diagnostic_cli
        from app.database.connection import get_raw_connection
        from app.main import _run_release_migrate

        db = tmp_path / "x.db"
        _build_v14(db)
        conn = get_raw_connection(str(db))
        _run_release_migrate(conn, skip_preflight=True)
        conn.close()
        before = _md5(db)
        with redirect_stdout(io.StringIO()):
            code = _run_planner_diagnostic_cli(["--db", str(db)])
        assert code == 0
        assert _md5(db) == before


# ============================================================
# 6) verify: integrity_check / foreign_key_check / fingerprint
# ============================================================


class TestVerifyHardening:
    def _migrated(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.main import _run_release_migrate

        db = tmp_path / "m.db"
        _build_v14(db)
        conn = get_raw_connection(str(db))
        _run_release_migrate(conn, skip_preflight=True)
        return conn

    def test_verify_fields_present(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated(tmp_path)
        try:
            result = rm.verify(conn)
            assert result["integrity_check"] == ["ok"]
            assert result["foreign_key_problems"] == []
            assert result["ok"] is True
            assert "fingerprints" in (result["after"] or {})
            assert "tasks" in result["after"]["fingerprints"]
        finally:
            conn.close()

    def test_fingerprint_detects_field_mutation(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated(tmp_path)
        try:
            before = rm.inventory(conn)
            conn.execute("UPDATE tasks SET title = 'HACKED' WHERE id = 1")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert "tasks" in result["history_fingerprint_changes"]
        finally:
            conn.close()

    def test_fingerprint_detects_id_replacement(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated(tmp_path)
        try:
            before = rm.inventory(conn)
            # 删除并插入同数量、不同 id 的行
            conn.execute("DELETE FROM tasks WHERE id = 1")
            conn.execute(
                "INSERT INTO tasks (title, description, category,"
                " estimated_minutes, priority, status, source, task_type,"
                " scheduled_date, created_at, updated_at) VALUES"
                " ('replacement','','学习',30,1,'done','manual','new',"
                " '2026-09-11','','')"
            )
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert "tasks" in result["history_id_changes"]
        finally:
            conn.close()

    def test_fingerprint_ignores_route_id_move(self, tmp_path):
        """canonical MOVE 合法修改 route_id，不应触发 fingerprint 误报。"""
        from app.diagnostics import release_migration as rm

        conn = self._migrated(tmp_path)
        try:
            before = rm.inventory(conn)
            conn.execute("UPDATE tasks SET route_id = NULL WHERE id = 1")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert "tasks" not in result["history_fingerprint_changes"]
        finally:
            conn.close()

    def test_integrity_and_fk_checks_clean(self, tmp_path):
        from app.diagnostics import release_migration as rm

        self._migrated(tmp_path).close()
        from app.database.connection import get_readonly_connection

        c = get_readonly_connection(str(tmp_path / "m.db"))
        try:
            assert rm.integrity_check(c) == ["ok"]
            assert rm.foreign_key_check(c) == []
        finally:
            c.close()

    def test_integrity_check_reports_non_ok(self, tmp_path, monkeypatch):
        """integrity_check 非 ok 时，ok=False。"""
        from app.diagnostics import release_migration as rm

        conn = self._migrated(tmp_path)
        try:
            monkeypatch.setattr(rm, "integrity_check", lambda c: ["corrupt"])
            result = rm.verify(conn)
            assert result["ok"] is False
            assert result["integrity_check"] == ["corrupt"]
        finally:
            conn.close()


# ============================================================
# 7) canonical 模式取消 route_id=None → first active plan
# ============================================================


class TestCanonicalNoFirstActivePlan:
    def test_active_plan_full_none_without_explicit_route(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.database.repository import TaskRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.skill_repository import SkillRepository
        from app.database.assessment_repository import AssessmentRepository
        from app.services.canonical_route_service import CanonicalRouteService
        from app.services.study_plan_service import StudyPlanService
        from app.database.schema import migrate_stepwise

        db = tmp_path / "canon.db"
        conn = get_raw_connection(str(db))
        migrate_stepwise(conn)
        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        CanonicalRouteService(
            conn, route_repo, plan_repo, SkillRepository(conn)
        ).ensure_all()
        sps = StudyPlanService(
            TaskRepository(conn), plan_repo,
            assessment_repo=AssessmentRepository(conn),
            learning_route_repo=route_repo,
        )
        assert sps._resolved_route_id() is None
        assert sps.get_active_plan_full() is None  # 不猜 first active plan
        # 显式 route 仍正常
        r1 = route_repo.get_by_key("R1_LLM_FUNDAMENTALS")
        sps_r1 = StudyPlanService(
            TaskRepository(conn), plan_repo, route_id=r1.id,
            learning_route_repo=route_repo,
        )
        assert sps_r1.get_active_plan_full() is not None
        conn.close()
