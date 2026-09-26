"""Verifier growth semantics：历史表“只增不减”子集校验。

背景：旧 verifier 用 whole-table ids_hash/fields_hash 比较 before/after。
正式 DB 完成 v14→v20 后只要正常新增业务行（tasks 48→54 等），整表 hash
必然变化 → verify 误报 False。

正确语义：before 已有历史行必须保留且 immutable 字段不变；after 新增行合法。
即 before IDs ⊆ after IDs，而不是相等。

本文件锁定当前 fingerprint v4 的子集语义与 legacy v1–v3 snapshot 兼容行为。
"""

from __future__ import annotations

import sqlite3

import pytest

TASK_COLS = (
    "title", "description", "category", "estimated_minutes", "priority",
    "status", "scheduled_date", "created_at", "updated_at",
)


def _insert_task(conn, i, *, title=None, route_id=None):
    cur = conn.execute(
        "INSERT INTO tasks (title,description,category,estimated_minutes,"
        "priority,status,scheduled_date,created_at,updated_at,route_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (title or f"task-{i}", "", "学习", 30, 1, "active",
         "2026-09-10", "2026-09-01T00:00:00", "2026-09-01T00:00:00", route_id),
    )
    return int(cur.lastrowid)


def _insert_kp(conn, i, *, name=None):
    cur = conn.execute(
        "INSERT INTO knowledge_points (name,description,mastery_estimate,"
        "review_count,interval_days,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (name or f"kp-{i}", "", 0.0, 0, 0,
         "2026-09-01T00:00:00", "2026-09-01T00:00:00"),
    )
    return int(cur.lastrowid)


def _insert_decision(conn, i, *, date=None):
    cur = conn.execute(
        "INSERT INTO planner_decisions (date,current_phase_id,input_context,"
        "ai_response,accepted_tasks,source,created_at) VALUES (?,?,?,?,?,?,?)",
        (date or f"2026-09-{i:02d}", None, "{}", "{}", "[]", "ai",
         "2026-09-01T00:00:00"),
    )
    return int(cur.lastrowid)


def _make_v20(path):
    from app.database.schema import migrate_stepwise

    conn = sqlite3.connect(str(path))
    migrate_stepwise(conn)
    conn.commit()
    return conn


def _seed(conn, *, tasks=5, kps=3, decisions=2):
    for i in range(tasks):
        _insert_task(conn, i)
    for i in range(kps):
        _insert_kp(conn, i)
    for i in range(decisions):
        _insert_decision(conn, i)
    conn.commit()


def _grow(conn, *, tasks=0, kps=0, decisions=0, start=100):
    for i in range(tasks):
        _insert_task(conn, start + i)
    for i in range(kps):
        _insert_kp(conn, start + i)
    for i in range(decisions):
        _insert_decision(conn, start + i)
    conn.commit()


def _strip_row_level(before, version=2):
    """把 v3 snapshot 降级成 legacy（v1/v2 风格）格式。"""
    for fp in (before.get("fingerprints") or {}).values():
        fp.pop("row_level", None)
        fp.pop("rows", None)
    before["fingerprint_version"] = version
    return before


class TestGrowthAllowed:
    """8. 新增行必须允许（旧行不变）。"""

    def test_new_tasks_allowed(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "g.db")
        try:
            _seed(conn, tasks=5)
            before = rm.inventory(conn)
            _grow(conn, tasks=6)
            result = rm.verify(conn, before=before)
            assert result["ok"] is True, result
            assert result["history_id_changes"] == {}
            assert result["history_fingerprint_changes"] == {}
            assert result["history_preserved"]["tasks"] == 5
            assert result["history_new_rows"]["tasks"] == 6
        finally:
            conn.close()

    def test_new_kps_allowed(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "g.db")
        try:
            _seed(conn, kps=3)
            before = rm.inventory(conn)
            _grow(conn, kps=2)
            result = rm.verify(conn, before=before)
            assert result["ok"] is True, result
            assert result["history_preserved"]["knowledge_points"] == 3
            assert result["history_new_rows"]["knowledge_points"] == 2
        finally:
            conn.close()

    def test_new_planner_decisions_allowed(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "g.db")
        try:
            _seed(conn, decisions=2)
            before = rm.inventory(conn)
            _grow(conn, decisions=3)
            result = rm.verify(conn, before=before)
            assert result["ok"] is True, result
            assert result["history_preserved"]["planner_decisions"] == 2
            assert result["history_new_rows"]["planner_decisions"] == 3
        finally:
            conn.close()

    def test_combined_growth_matches_production_scenario(self, tmp_path):
        """48→54 tasks / 25→27 KP / 20→23 decisions，旧行不变 → True。"""
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "g.db")
        try:
            _seed(conn, tasks=48, kps=25, decisions=20)
            before = rm.inventory(conn)
            _grow(conn, tasks=6, kps=2, decisions=3)
            result = rm.verify(conn, before=before)
            assert result["ok"] is True, result
            for table, expected in (
                ("tasks", 48),
                ("knowledge_points", 25),
                ("planner_decisions", 20),
            ):
                assert result["history_preserved"][table] == expected
            assert result["history_new_rows"]["tasks"] == 6
            assert result["history_new_rows"]["knowledge_points"] == 2
            assert result["history_new_rows"]["planner_decisions"] == 3
            assert result["history_missing_ids"] == {}
            assert result["history_modified_rows"] == {}
        finally:
            conn.close()


class TestDeletionDetected:
    """6. 删除旧行必须失败（即使 count 未下降）。"""

    def test_task_deleted_and_replacement_same_count_fails(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "d.db")
        try:
            ids = [_insert_task(conn, i) for i in range(5)]
            conn.commit()
            before = rm.inventory(conn)
            # 删除 id[2] + 新增一条 → count 仍为 5
            conn.execute("DELETE FROM tasks WHERE id=?", (ids[2],))
            _insert_task(conn, 999)
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert "tasks" in result["history_missing_ids"]
            assert ids[2] in result["history_missing_ids"]["tasks"]
            assert "tasks" in result["history_id_changes"]
        finally:
            conn.close()

    def test_kp_deleted_and_replacement_same_count_fails(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "d.db")
        try:
            ids = [_insert_kp(conn, i) for i in range(3)]
            conn.commit()
            before = rm.inventory(conn)
            conn.execute("DELETE FROM knowledge_points WHERE id=?", (ids[1],))
            _insert_kp(conn, 999, name="replacement")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert ids[1] in result["history_missing_ids"]["knowledge_points"]
        finally:
            conn.close()

    def test_planner_decision_deleted_and_replacement_same_count_fails(
        self, tmp_path
    ):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "d.db")
        try:
            ids = [_insert_decision(conn, i) for i in range(1, 4)]
            conn.commit()
            before = rm.inventory(conn)
            conn.execute("DELETE FROM planner_decisions WHERE id=?", (ids[1],))
            _insert_decision(conn, 999, date="2027-01-01")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert ids[1] in result["history_missing_ids"]["planner_decisions"]
        finally:
            conn.close()


class TestModificationDetected:
    """7. 修改旧行必须失败（即使同时新增行）。"""

    def test_task_immutable_field_changed_with_growth_fails(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "m.db")
        try:
            _seed(conn, tasks=5)
            before = rm.inventory(conn)
            conn.execute("UPDATE tasks SET title='HACK' WHERE id=1")
            _grow(conn, tasks=1)
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert "tasks" in result["history_fingerprint_changes"]
            assert 1 in result["history_modified_rows"]["tasks"]
        finally:
            conn.close()

    def test_kp_protected_field_changed_fails(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "m.db")
        try:
            _seed(conn, kps=3)
            before = rm.inventory(conn)
            conn.execute(
                "UPDATE knowledge_points SET mastery_estimate=0.9 WHERE id=1"
            )
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert 1 in result["history_modified_rows"]["knowledge_points"]
        finally:
            conn.close()

    def test_planner_decision_protected_field_changed_fails(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "m.db")
        try:
            _seed(conn, decisions=2)
            before = rm.inventory(conn)
            conn.execute(
                "UPDATE planner_decisions SET source='manual' WHERE id=1"
            )
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert 1 in result["history_modified_rows"]["planner_decisions"]
        finally:
            conn.close()

    def test_new_rows_do_not_affect_history_hash(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "m.db")
        try:
            _seed(conn, tasks=4, kps=2, decisions=1)
            before = rm.inventory(conn)
            _grow(conn, tasks=10, kps=10, decisions=10)
            result = rm.verify(conn, before=before)
            assert result["ok"] is True
            assert result["history_modified_rows"] == {}
            assert result["history_missing_ids"] == {}
            # 新增行数只体现在 history_new_rows，不影响历史保留数
            assert result["history_preserved"]["tasks"] == 4
        finally:
            conn.close()


class TestSnapshotFormat:
    """3/14/15. fingerprint v4 + legacy 兼容。"""

    def test_inventory_is_v4_with_row_level_hashes(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "f.db")
        try:
            _seed(conn, tasks=3)
            inv = rm.inventory(conn)
            assert inv["fingerprint_version"] == 4
            fp = inv["fingerprints"]["tasks"]
            assert fp["row_level"] is True
            assert len(fp["rows"]) == 3
            assert all(isinstance(v, str) for v in fp["rows"].values())
        finally:
            conn.close()

    def test_legacy_v2_snapshot_growth_no_false_positive(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "l.db")
        try:
            _seed(conn, tasks=5, kps=3, decisions=2)
            before = _strip_row_level(rm.inventory(conn), version=2)
            _grow(conn, tasks=6, kps=2, decisions=3)
            result = rm.verify(conn, before=before)
            assert result["fingerprint_version_match"] is False
            assert result["ok"] is True, result
            assert result["history_id_changes"] == {}
            assert result["history_fingerprint_changes"] == {}
            # 不伪造完整校验：显式标注
            assert result["historical_row_field_check"]["tasks"] == (
                "not_available_for_legacy_snapshot"
            )
            assert result["history_new_rows"]["tasks"] == 6
        finally:
            conn.close()

    def test_legacy_v2_snapshot_equal_count_mutation_detected(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "l.db")
        try:
            _seed(conn, tasks=5)
            before = _strip_row_level(rm.inventory(conn), version=2)
            conn.execute("UPDATE tasks SET title='HACK' WHERE id=1")
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is False
            assert "tasks" in result["history_fingerprint_changes"]
        finally:
            conn.close()


class TestMigrationOwnedFieldsRemain:
    """9. migration-owned 字段仍不进入 immutable fingerprint。"""

    def test_migration_owned_columns_excluded(self):
        from app.diagnostics.release_migration import FINGERPRINT_COLUMNS

        for col in ("route_id", "component_id", "learning_activity_kind"):
            assert col not in FINGERPRINT_COLUMNS["tasks"]

    def test_route_id_change_not_flagged_with_growth(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "o.db")
        try:
            _seed(conn, tasks=3)
            before = rm.inventory(conn)
            conn.execute("UPDATE tasks SET route_id=NULL WHERE id=1")
            conn.execute(
                "UPDATE tasks SET component_id=NULL, "
                "learning_activity_kind=NULL WHERE id=1"
            )
            _grow(conn, tasks=1)
            conn.commit()
            result = rm.verify(conn, before=before)
            assert result["ok"] is True, result
            assert "tasks" not in result["history_fingerprint_changes"]
        finally:
            conn.close()


class TestStructuralChecksRemain:
    """13. route / component 结构校验不回退。"""

    def test_verify_result_contains_structural_checks(self, tmp_path):
        from app.diagnostics import release_migration as rm

        conn = _make_v20(tmp_path / "s.db")
        try:
            _seed(conn, tasks=2)
            result = rm.verify(conn)
            assert result["ok"] is True
            for key in ("integrity_check", "foreign_key_problems",
                        "route_problems", "evidence_problems",
                        "component_problems"):
                assert key in result
        finally:
            conn.close()


class TestSyntheticMigrationWithGrowth:
    """16/17. 合成 v14→v20 立即 verify，以及迁移后正常写入仍 True。"""

    def _build_v14(self, path):
        from app.database.connection import get_raw_connection
        from app.database.schema import migrate_stepwise

        conn = get_raw_connection(str(path))
        migrate_stepwise(conn, target=14)
        for i in range(2):
            _insert_task(conn, i)
        for i in range(2):
            _insert_kp(conn, i)
        conn.commit()
        return conn

    def test_v14_to_v20_immediate_verify_true(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm
        from app.main import _run_release_migrate

        db = tmp_path / "v14.db"
        conn = self._build_v14(db)
        before = rm.inventory(conn)
        conn.close()

        conn = get_raw_connection(str(db))
        _run_release_migrate(conn, skip_preflight=True)
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is True, result
        assert result["history_missing_ids"] == {}
        assert result["history_modified_rows"] == {}

    def test_v14_to_v20_plus_normal_writes_still_true(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm
        from app.main import _run_release_migrate

        db = tmp_path / "v14.db"
        conn = self._build_v14(db)
        before = rm.inventory(conn)
        conn.close()

        conn = get_raw_connection(str(db))
        _run_release_migrate(conn, skip_preflight=True)
        # 迁移后系统正常产生新业务数据
        _grow(conn, tasks=6, kps=2, decisions=3)
        result = rm.verify(conn, before=before)
        conn.close()
        assert result["ok"] is True, result
        assert result["history_preserved"]["tasks"] == 2
        assert result["history_new_rows"]["tasks"] == 6
        assert result["history_new_rows"]["knowledge_points"] >= 2
        assert result["history_new_rows"]["planner_decisions"] == 3
