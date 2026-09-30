"""Migration verifier regression：migration-owned 字段不误报 + 结构校验。

背景：v15 MOVE 合法改 `tasks.route_id`；v16 `backfill_legacy_theory` 合法把历史
done 任务从 NULL 绑定到 theory component（`component_id` /
`learning_activity_kind`）。这些字段不应进入 immutable fingerprint。
"""

from __future__ import annotations

import json

import pytest

# Populated historical DB upgrades through real migration/release paths.
pytestmark = [pytest.mark.migration, pytest.mark.integration, pytest.mark.slow]


def _build_v14_with_movable_task(path) -> tuple[int, str]:
    """v14 + 一个 topic-linked done 正式任务（会被 MOVE + theory backfill）。"""
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise
    from app.database.repository import TaskRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.study_plan_service import StudyPlanService
    from app.utils.date_utils import now_iso

    conn = get_raw_connection(str(path))
    migrate_stepwise(conn, target=14)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    arepo = AssessmentRepository(conn)
    StudyPlanService(repo, plan_repo, assessment_repo=arepo).ensure_default_plan()
    row = conn.execute(
        "SELECT t.id, t.name FROM study_topics t "
        "WHERE t.name LIKE 'Transformer%' LIMIT 1"
    ).fetchone()
    topic_id, topic_name = int(row[0]), row[1]
    kp = arepo.create_knowledge_point(topic_name, topic_id=topic_id)
    conn.execute(
        "INSERT INTO tasks (title,description,category,estimated_minutes,"
        "priority,status,source,task_type,knowledge_point_id,topic_id,"
        "scheduled_date,created_at,updated_at) VALUES "
        "('历史学习任务','','学习',45,1,'done','generated','new',?,?,"
        "'2026-09-10',?,?)",
        (kp["id"], topic_id, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()
    return topic_id, topic_name


def _migrate(path):
    from app.database.connection import get_raw_connection
    from app.main import _run_release_migrate

    conn = get_raw_connection(str(path))
    _run_release_migrate(conn, skip_preflight=True)
    return conn


def _build_populated_v3(path):
    """Create a real v3 database before tasks.task_type exists."""
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise

    conn = get_raw_connection(str(path))
    migrate_stepwise(conn, target=3)
    assert "task_type" not in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    conn.execute(
        "INSERT INTO tasks (title, description, category, estimated_minutes, "
        "priority, status, scheduled_date, postpone_count, created_at, "
        "updated_at, source, topic_id) VALUES "
        "('historical v3 task', '', '学习', 45, 1, 'active', "
        "'2026-09-10', 0, '2026-09-10T10:00:00', "
        "'2026-09-10T10:00:00', 'generated', NULL)"
    )
    conn.commit()
    return conn


def test_populated_v3_to_v25_preserves_immutable_history(tmp_path):
    from app.database.schema import migrate_stepwise
    from app.diagnostics import release_migration as rm

    conn = _build_populated_v3(tmp_path / "old.db")
    try:
        before = rm.inventory(conn)
        assert before["schema_version"] == 3
        migrate_stepwise(conn)
        assert conn.execute("SELECT task_type FROM tasks WHERE id=1").fetchone()[0] == "new"
        result = rm.verify(conn, before=before)
        assert result["ok"], result["history_modified_rows"]
    finally:
        conn.close()


class TestLegacyFingerprintProjection:
    def test_v3_missing_metadata_old_version_still_verifies(self, tmp_path):
        from app.database.schema import migrate_stepwise
        from app.diagnostics import release_migration as rm

        conn = _build_populated_v3(tmp_path / "old_snapshot.db")
        try:
            before = rm.inventory(conn)
            before["fingerprint_version"] = 3
            # Stored v3 snapshots predate present_columns metadata.
            for fp in before["fingerprints"].values():
                fp.pop("present_columns", None)
            migrate_stepwise(conn)
            result = rm.verify(conn, before=before)
            assert result["fingerprint_version_match"] is False
            assert result["ok"], result["history_modified_rows"]
            conn.execute("UPDATE tasks SET task_type='review' WHERE id=1")
            conn.commit()
            assert rm.verify(conn, before=before)["history_modified_rows"]["tasks"] == [1]
        finally:
            conn.close()

    @pytest.mark.parametrize("legacy_version", [3, None])
    def test_v3_short_projection_still_checks_migration_default(self, tmp_path,
                                                                 legacy_version):
        import hashlib
        from app.database.schema import migrate_stepwise
        from app.diagnostics import release_migration as rm

        conn = _build_populated_v3(tmp_path / "short.db")
        try:
            before = rm.inventory(conn)
            if legacy_version is None:
                before.pop("fingerprint_version")
            else:
                before["fingerprint_version"] = legacy_version
            fp = before["fingerprints"]["tasks"]
            columns = ["id", "title", "status"]
            row = conn.execute("SELECT id, title, status FROM tasks WHERE id=1").fetchone()
            digest = hashlib.sha256(
                ",".join(str(value) for value in row).encode("utf-8")
            ).hexdigest()
            fp.update(columns=columns, rows={"1": digest}, fields_hash=digest)
            fp.pop("present_columns")
            migrate_stepwise(conn)
            assert rm.verify(conn, before=before)["ok"] is True
            conn.execute("UPDATE tasks SET task_type='review' WHERE id=1")
            conn.commit()
            assert rm.verify(conn, before=before)["history_modified_rows"]["tasks"] == [1]
        finally:
            conn.close()

    def test_v6_snapshot_with_shorter_fingerprint_projection(self, conn):
        import hashlib
        from app.database.repository import TaskRepository
        from app.diagnostics import release_migration as rm

        task = TaskRepository(conn).create("observed", scheduled_date="2026-01-01")
        before = rm.inventory(conn)
        before["fingerprint_version"] = 6
        fp = before["fingerprints"]["tasks"]
        old_columns = ["id", "title", "status", "scheduled_date"]
        row = conn.execute(
            "SELECT id, title, status, scheduled_date FROM tasks WHERE id=?",
            (task.id,),
        ).fetchone()
        payload = ",".join("" if value is None else str(value) for value in row)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        fp.update(columns=old_columns, present_columns=old_columns,
                  rows={str(task.id): digest},
                  fields_hash=digest)
        assert rm.verify(conn, before=before)["ok"] is True
        conn.execute("UPDATE tasks SET title='changed' WHERE id=?", (task.id,))
        conn.commit()
        assert rm.verify(conn, before=before)["history_modified_rows"]["tasks"] == [task.id]

    @pytest.mark.parametrize("tamper", [
        "UPDATE tasks SET task_type='review' WHERE id=1",
        "UPDATE tasks SET title='changed' WHERE id=1",
    ])
    def test_migration_default_tamper_is_not_ignored(self, tmp_path, tamper):
        from app.database.schema import migrate_stepwise
        from app.diagnostics import release_migration as rm

        conn = _build_populated_v3(tmp_path / "tamper.db")
        try:
            before = rm.inventory(conn)
            migrate_stepwise(conn)
            conn.execute(tamper)
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert result["history_modified_rows"]["tasks"] == [1]
        finally:
            conn.close()

    def test_v2_assessment_default_is_owned_but_tamper_is_not(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.database.schema import migrate_stepwise
        from app.diagnostics import release_migration as rm

        conn = get_raw_connection(str(tmp_path / "v2.db"))
        try:
            migrate_stepwise(conn, target=2)
            conn.execute("INSERT INTO knowledge_points (name, created_at, updated_at) "
                         "VALUES ('Old KP', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO assessment_attempts "
                         "(knowledge_point_id, questions_json, created_at) "
                         "VALUES (1, '{}', '2026-01-01')")
            conn.commit()
            before = rm.inventory(conn)
            migrate_stepwise(conn)
            assert rm.verify(conn, before=before)["ok"] is True
            conn.execute("UPDATE assessment_attempts SET judge_status='judged' WHERE id=1")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert not result["ok"]
            assert result["history_modified_rows"]["assessment_attempts"] == [1]
        finally:
            conn.close()

    def test_legacy_v2_without_row_hashes_still_checks_known_defaults(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.database.schema import migrate_stepwise
        from app.diagnostics import release_migration as rm

        conn = get_raw_connection(str(tmp_path / "legacy_v2.db"))
        try:
            migrate_stepwise(conn, target=2)
            conn.execute("INSERT INTO knowledge_points (name, created_at, updated_at) "
                         "VALUES ('KP', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO assessment_attempts "
                         "(knowledge_point_id, questions_json, created_at) "
                         "VALUES (1, '{}', '2026-01-01')")
            conn.commit()
            before = rm.inventory(conn)
            before["fingerprint_version"] = 2
            for fp in before["fingerprints"].values():
                fp.pop("present_columns", None)
                fp.pop("rows", None)
                fp.pop("row_level", None)
            migrate_stepwise(conn)
            assert rm.verify(conn, before=before)["ok"] is True
            conn.execute("UPDATE assessment_attempts SET judge_status='failed' WHERE id=1")
            conn.commit()
            assert not rm.verify(conn, before=before)["ok"]
            assert "assessment_attempts" in rm.verify(conn, before=before)["history_fingerprint_changes"]
        finally:
            conn.close()

    def test_current_snapshot_remains_strict_and_growth_is_legal(self, conn):
        from app.diagnostics import release_migration as rm

        from app.database.repository import TaskRepository

        repo = TaskRepository(conn)
        original = repo.create("original", scheduled_date="2026-01-01", source="manual")
        before = rm.inventory(conn)
        repo.create("new row", scheduled_date="2026-01-02", source="manual")
        assert rm.verify(conn, before=before)["ok"] is True
        conn.execute("UPDATE tasks SET title='tampered' WHERE id=?", (original.id,))
        conn.commit()
        result = rm.verify(conn, before=before)
        assert not result["ok"] and result["history_modified_rows"]["tasks"] == [original.id]
        conn.execute("DELETE FROM tasks WHERE id=?", (original.id,))
        conn.commit()
        result = rm.verify(conn, before=before)
        assert not result["ok"] and result["history_missing_ids"]["tasks"] == [original.id]


class TestMigrationOwnedFieldsNoFalsePositive:
    def test_v14_to_v20_verify_ok(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm
        from app.main import _run_release_migrate

        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        ro = get_raw_connection(str(db), read_only=True)
        before = rm.inventory(ro)
        ro.close()

        conn = get_raw_connection(str(db))
        _run_release_migrate(conn, skip_preflight=True)
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is True, result
        assert result["history_fingerprint_changes"] == {}
        assert result["component_problems"] == []

    def test_component_and_activity_backfill_not_flagged(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm

        db = tmp_path / "m.db"
        topic_id, _ = _build_v14_with_movable_task(db)
        ro = get_raw_connection(str(db), read_only=True)
        before = rm.inventory(ro)
        ro.close()
        # v14 tasks 尚无 component_id 列（v16 才新增）→ 迁移前等价于 NULL
        before_cols = {
            r[1] for r in get_raw_connection(
                str(db), read_only=True
            ).execute("PRAGMA table_info(tasks)")
        }
        assert "component_id" not in before_cols

        conn = _migrate(db)
        after = conn.execute(
            "SELECT route_id, component_id, learning_activity_kind "
            "FROM tasks WHERE id=1"
        ).fetchone()
        # v16 backfill 已合法绑定 theory component
        assert after[1] is not None
        assert after[2] == "theory"
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["history_fingerprint_changes"] == {}
        assert result["ok"] is True

    def test_route_id_move_not_flagged(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm

        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        conn = _migrate(db)
        before = rm.inventory(conn)
        # 模拟 canonical MOVE 只改 route_id
        conn.execute("UPDATE tasks SET route_id = 999 WHERE id = 1")
        conn.commit()
        result = rm.verify(conn, before=before)
        conn.close()
        assert "tasks" not in result["history_fingerprint_changes"]

    def test_fingerprint_columns_exclude_migration_owned(self):
        from app.diagnostics.release_migration import FINGERPRINT_COLUMNS

        cols = FINGERPRINT_COLUMNS["tasks"]
        for owned in ("route_id", "component_id", "learning_activity_kind"):
            assert owned not in cols
        for immutable in ("id", "title", "status", "scheduled_date",
                          "topic_id", "source", "task_type",
                          "knowledge_point_id", "estimated_minutes",
                          "postpone_count"):
            assert immutable in cols


class TestImmutableFieldsStillProtected:
    def _conn_and_before(self, tmp_path):
        from app.diagnostics import release_migration as rm

        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        conn = _migrate(db)
        before = rm.inventory(conn)
        return conn, before

    @pytest.mark.parametrize("sql", [
        "UPDATE tasks SET title='HACK' WHERE id=1",
        "UPDATE tasks SET status='cancelled' WHERE id=1",
        "UPDATE tasks SET scheduled_date='2026-01-01' WHERE id=1",
        "UPDATE tasks SET topic_id=999 WHERE id=1",
        "UPDATE tasks SET knowledge_point_id=999 WHERE id=1",
        "UPDATE tasks SET source='manual' WHERE id=1",
        "UPDATE tasks SET task_type='review' WHERE id=1",
        "UPDATE tasks SET estimated_minutes=999 WHERE id=1",
        "UPDATE tasks SET postpone_count=7 WHERE id=1",
    ])
    def test_field_mutation_detected(self, tmp_path, sql):
        from app.diagnostics import release_migration as rm

        conn, before = self._conn_and_before(tmp_path)
        conn.execute(sql)
        conn.commit()
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is False
        assert "tasks" in result["history_fingerprint_changes"], sql

    def test_task_id_replacement_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn, before = self._conn_and_before(tmp_path)
        conn.execute("DELETE FROM tasks WHERE id=1")
        conn.execute(
            "INSERT INTO tasks (title,description,category,estimated_minutes,"
            "priority,status,source,task_type,scheduled_date,created_at,"
            "updated_at) VALUES ('replacement','','学习',30,1,'done',"
            "'manual','new','2026-09-11','','')"
        )
        conn.commit()
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is False
        assert "tasks" in result["history_id_changes"]

    def test_row_count_decrease_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn, before = self._conn_and_before(tmp_path)
        conn.execute("DELETE FROM tasks WHERE id=1")
        conn.commit()
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is False
        assert "tasks" in result["history_decreases"]


class TestComponentStructuralVerification:
    def _migrated_conn(self, tmp_path):
        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        return _migrate(db)

    def test_dangling_component_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated_conn(tmp_path)
        try:
            conn.execute(
                "UPDATE tasks SET component_id=999999, "
                "learning_activity_kind='theory' WHERE id=1"
            )
            conn.commit()
            result = rm.verify(conn)
            assert result["ok"] is False
            kinds = [
                issue
                for p in result["component_problems"]
                for issue in p["issues"]
            ]
            assert "component_missing" in kinds
        finally:
            conn.close()

    def test_component_topic_mismatch_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated_conn(tmp_path)
        try:
            task_topic = conn.execute(
                "SELECT topic_id FROM tasks WHERE id=1"
            ).fetchone()[0]
            other = conn.execute(
                "SELECT id, topic_id, activity_kind FROM "
                "topic_learning_components WHERE topic_id != ? LIMIT 1",
                (task_topic,),
            ).fetchone()
            assert other is not None
            conn.execute(
                "UPDATE tasks SET component_id=?, learning_activity_kind=? "
                "WHERE id=1",
                (other[0], other[2]),
            )
            conn.commit()
            result = rm.verify(conn)
            assert result["ok"] is False
            kinds = [
                issue
                for p in result["component_problems"]
                for issue in p["issues"]
            ]
            assert "component_topic_mismatch" in kinds
        finally:
            conn.close()

    def test_activity_kind_mismatch_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated_conn(tmp_path)
        try:
            comp = conn.execute(
                "SELECT c.id, c.activity_kind FROM tasks t "
                "JOIN topic_learning_components c ON c.id = t.component_id "
                "WHERE t.id=1"
            ).fetchone()
            assert comp is not None
            wrong = "experiment" if comp[1] != "experiment" else "code_reading"
            conn.execute(
                "UPDATE tasks SET learning_activity_kind=? WHERE id=1",
                (wrong,),
            )
            conn.commit()
            result = rm.verify(conn)
            assert result["ok"] is False
            kinds = [
                issue
                for p in result["component_problems"]
                for issue in p["issues"]
            ]
            assert "activity_kind_mismatch" in kinds
        finally:
            conn.close()

    def test_clean_db_component_problems_empty(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = self._migrated_conn(tmp_path)
        try:
            assert rm.component_consistency_problems(conn) == []
            assert rm.verify(conn)["component_problems"] == []
        finally:
            conn.close()


class TestBackwardCompatibleSnapshot:
    def test_old_snapshot_without_version_is_not_false_positive(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm

        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        ro = get_raw_connection(str(db), read_only=True)
        before = rm.inventory(ro)
        ro.close()
        # 模拟 aa14cda 时代 snapshot：无 fingerprint_version
        before.pop("fingerprint_version", None)
        conn = _migrate(db)
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["fingerprint_version_match"] is False
        assert result["history_fingerprint_changes"] == {}
        # ids_hash 仍比较（id 集合未变 → 通过）
        assert result["ok"] is True

    def test_old_snapshot_still_detects_id_replacement(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm

        db = tmp_path / "m.db"
        _build_v14_with_movable_task(db)
        conn = _migrate(db)
        before = rm.inventory(conn)
        before.pop("fingerprint_version", None)
        conn.execute("DELETE FROM tasks WHERE id=1")
        conn.execute(
            "INSERT INTO tasks (title,description,category,estimated_minutes,"
            "priority,status,source,task_type,scheduled_date,created_at,"
            "updated_at) VALUES ('x','','学习',30,1,'done','manual','new',"
            "'2026-09-11','','')"
        )
        conn.commit()
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is False
        assert "tasks" in result["history_id_changes"]


class TestHardeningNotRegressed:
    def test_hardening_api_intact(self):
        from app.diagnostics import release_migration as rm

        for name in ("backup_database", "dry_run_on_copy",
                     "source_preflight_problems", "component_consistency_problems",
                     "integrity_check", "foreign_key_check", "fingerprint",
                     "readonly_copy", "working_copy"):
            assert hasattr(rm, name), name
        assert rm.FINGERPRINT_VERSION >= 1
