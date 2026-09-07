"""数据库迁移机制测试。

覆盖：
- 新数据库首次打开 -> 版本写入 SCHEMA_VERSION，基础表存在
- 旧数据库（无 user_version）-> 迁移后版本正确，历史数据保留
- migrate 幂等：重复调用不报错、版本不变
- 未来迁移登记机制：新增迁移能被按序执行，且自身幂等
- add_column_if_not_exists 幂等加列
"""

from __future__ import annotations

import sqlite3

from app.database import schema as schema_module
from app.database.connection import get_connection
from app.database.schema import (
    SCHEMA_VERSION,
    add_column_if_not_exists,
    create_schema,
    get_schema_version,
    migrate,
)


def test_new_db_gets_current_schema_version(tmp_path):
    conn = get_connection(tmp_path / "new.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        # 基础表已建
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for expected in ("tasks", "app_meta", "study_plans", "study_phases",
                         "study_topics", "planner_decisions"):
            assert expected in tables
    finally:
        conn.close()


def test_old_db_without_version_preserves_data(tmp_path):
    path = tmp_path / "old.db"
    # 模拟旧版本代码：只 create_schema、不写 user_version
    raw = sqlite3.connect(str(path))
    try:
        create_schema(raw)
        raw.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source, topic_id)"
            " VALUES ('历史任务', '', '学习', 30, 2, 'done', '2026-09-05', 0,"
            " '2026-09-05T10:00:00', '2026-09-05T10:00:00', 'generated', 1)"
        )
        raw.commit()
        assert get_schema_version(raw) == 0
    finally:
        raw.close()

    # 新代码打开同一数据库 -> 迁移，历史任务仍在
    conn = get_connection(path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        row = conn.execute(
            "SELECT title, status, scheduled_date FROM tasks WHERE title='历史任务'"
        ).fetchone()
        assert row is not None
        assert row["title"] == "历史任务"
        assert row["status"] == "done"
        assert row["scheduled_date"] == "2026-09-05"
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "idem.db")
    try:
        assert migrate(conn) == SCHEMA_VERSION
        assert migrate(conn) == SCHEMA_VERSION
        assert get_schema_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_future_migration_applied_and_idempotent(tmp_path, monkeypatch):
    """模拟未来 v2 迁移：能按序执行，且重复 migrate 不重复执行。"""
    path = tmp_path / "future.db"
    # 先建一个 v1 库，并设为 v1（模拟旧库）
    raw = sqlite3.connect(str(path))
    try:
        create_schema(raw)
        raw.execute("PRAGMA user_version = 1")
        raw.commit()
    finally:
        raw.close()

    calls = []

    def _v2(conn):
        calls.append(1)
        add_column_if_not_exists(
            conn, "tasks", "task_type", "TEXT NOT NULL DEFAULT 'new'"
        )

    monkeypatch.setitem(schema_module._MIGRATIONS, 2, _v2)
    monkeypatch.setattr(schema_module, "SCHEMA_VERSION", 2)

    conn = get_connection(path)
    try:
        assert get_schema_version(conn) == 2
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "task_type" in cols
        # 旧数据默认填充 'new'，不破坏历史行
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        # 手动插一行，验证默认值生效
        conn.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source) VALUES ('t','','学习',1,1,'active','2026-09-06',0,"
            " '2026-09-06T10:00:00','2026-09-06T10:00:00','manual')"
        )
        conn.commit()
        row = conn.execute("SELECT task_type FROM tasks WHERE title='t'").fetchone()
        assert row["task_type"] == "new"

        # 再次迁移：版本不变，迁移函数不再执行
        assert migrate(conn) == 2
        assert migrate(conn) == 2
        assert len(calls) == 1
    finally:
        conn.close()


def test_add_column_if_not_exists_idempotent(tmp_path):
    conn = get_connection(tmp_path / "col.db")
    try:
        assert add_column_if_not_exists(
            conn, "tasks", "task_type", "TEXT NOT NULL DEFAULT 'new'"
        ) is True
        assert add_column_if_not_exists(
            conn, "tasks", "task_type", "TEXT NOT NULL DEFAULT 'new'"
        ) is False
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "task_type" in cols
    finally:
        conn.close()
