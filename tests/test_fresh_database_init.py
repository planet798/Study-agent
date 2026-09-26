"""Fast fresh-DB initializer 的保证测试（开发基础设施，非业务 Phase）。

锁定：
1. fresh DB 的 schema_version == SCHEMA_VERSION
2. 当前完整 schema（表 / 索引 / 约束）与真实迁移路径完全一致
3. 不执行任何历史迁移步骤
4. 拒绝非空库 / 已迁移库
5. legacy v14 -> v20 仍完整逐级执行
6. release migration / production 连接不使用 fast initializer
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest

import app.database.schema as schema_mod
from app.database import connection as connection_mod
from app.database.connection import (
    get_connection,
    get_fresh_connection,
    resolve_db_path,
)
from app.database.schema import (
    SCHEMA_VERSION,
    get_schema_version,
    initialize_fresh_database,
    is_empty_database,
    migrate_stepwise,
)


def _schema_objects(conn) -> list[str]:
    """规范化后的 sqlite_master.sql 列表（表 -> 索引 -> 其它）。"""
    return [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type "
            "  WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, rowid"
        )
    ]


def _table_names(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _index_names(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _data_tables(conn) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for name in sorted(_table_names(conn)):
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {name}")]
        if rows:
            out[name] = rows
    return out


# ------------------------------------------------------------
# 1~3：当前 schema 完整性
# ------------------------------------------------------------


def test_fresh_db_sets_current_schema_version(tmp_path):
    conn = get_fresh_connection(tmp_path / "fresh.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_fresh_db_schema_matches_real_migration_path(tmp_path):
    real_path = resolve_db_path(tmp_path / "real.db")
    raw = sqlite3.connect(str(real_path))
    migrate_stepwise(raw)

    fast = get_fresh_connection(tmp_path / "fast.db")
    try:
        # 表 / 索引 / 约束 / 触发器 的完整 DDL 必须逐字一致
        assert _schema_objects(fast) == _schema_objects(raw)
        assert _table_names(fast) == _table_names(raw)
        assert _index_names(fast) == _index_names(raw)
        assert len(_table_names(fast)) > 0
        assert len(_index_names(fast)) > 0
    finally:
        fast.close()
        raw.close()


def test_fresh_db_reproduces_real_migration_seed_rows(tmp_path):
    real_path = resolve_db_path(tmp_path / "real.db")
    raw = sqlite3.connect(str(real_path))
    migrate_stepwise(raw)

    fast = get_fresh_connection(tmp_path / "fast.db")
    try:
        real = _data_tables(raw)
        got = _data_tables(fast)
        assert set(real) == set(got)
        # 只比较结构相关列，忽略迁移执行时刻的 created_at / updated_at
        for table in real:
            assert len(real[table]) == len(got[table])
            for r_row, f_row in zip(real[table], got[table]):
                assert r_row[:10] == f_row[:10], table
    finally:
        fast.close()
        raw.close()


# ------------------------------------------------------------
# 4：不执行历史迁移
# ------------------------------------------------------------


class _ExplodingMigrations(dict):
    def __init__(self):
        super().__init__()
        self.accessed: list[int] = []

    def get(self, key, default=None):  # noqa: D102
        self.accessed.append(key)
        raise AssertionError(f"fast initializer 不应访问迁移 v{key}")


def test_fast_initializer_does_not_run_migrations(tmp_path, monkeypatch):
    # 先确保快照已构建（快照本身由真实迁移路径反推，只构建一次）
    schema_mod._fresh_snapshot()

    exploding = _ExplodingMigrations()
    monkeypatch.setattr(schema_mod, "_MIGRATIONS", exploding)

    conn = get_fresh_connection(tmp_path / "fresh.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        assert exploding.accessed == []
    finally:
        conn.close()


# ------------------------------------------------------------
# 5：拒绝非空 / 已迁移库
# ------------------------------------------------------------


def test_initialize_refuses_already_migrated_db(tmp_path):
    path = resolve_db_path(tmp_path / "prod.db")
    raw = sqlite3.connect(str(path))
    migrate_stepwise(raw)
    assert not is_empty_database(raw)
    with pytest.raises(RuntimeError):
        initialize_fresh_database(raw)
    raw.close()


def test_initialize_refuses_db_with_existing_tables(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "legacy.db"))
    raw.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        initialize_fresh_database(raw)
    raw.close()


# ------------------------------------------------------------
# 6：legacy v14 -> v20 仍逐级真实执行
# ------------------------------------------------------------


def test_legacy_v14_to_v20_still_runs_every_step(tmp_path):
    path = resolve_db_path(tmp_path / "v14.db")
    raw = sqlite3.connect(str(path))
    migrate_stepwise(raw, target=14)
    assert get_schema_version(raw) == 14
    raw.close()

    reopened = sqlite3.connect(str(path))
    steps: list[int] = []
    migrate_stepwise(reopened, on_step=steps.append)
    assert steps == list(range(15, SCHEMA_VERSION + 1))
    assert get_schema_version(reopened) == SCHEMA_VERSION
    reopened.close()


def test_migration_registry_covers_every_version():
    # v21：Agent-1 新增 agent_sessions / agent_messages 正式数据模型
    assert SCHEMA_VERSION >= 21
    assert sorted(schema_mod._MIGRATIONS) == list(range(2, SCHEMA_VERSION + 1))


# ------------------------------------------------------------
# 7：production 路径不使用 fast initializer
# ------------------------------------------------------------


def test_get_connection_still_uses_real_migrate():
    src = inspect.getsource(connection_mod.get_connection)
    assert "migrate(conn)" in src
    assert "initialize_fresh_database" not in src


def test_release_migration_module_does_not_use_fast_initializer():
    from app.diagnostics import release_migration

    src = inspect.getsource(release_migration)
    assert "initialize_fresh_database" not in src
    assert "get_fresh_connection" not in src


def test_production_get_connection_migrates_empty_db(tmp_path):
    conn = get_connection(tmp_path / "prod.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()
