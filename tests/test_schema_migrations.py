"""数据库迁移机制测试。

覆盖：
- 新数据库首次打开 -> 版本写入 SCHEMA_VERSION，基础表存在
- 旧数据库（无 user_version）-> 迁移后版本正确，历史数据保留
- v2 迁移：knowledge_points / assessment_attempts 表创建且不破坏旧数据
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


def test_v2_tables_exist_in_new_db(tmp_path):
    conn = get_connection(tmp_path / "v2.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "knowledge_points" in tables
        assert "assessment_attempts" in tables
        # 新库两张表均为空
        assert conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    finally:
        conn.close()


def test_v1_db_upgrade_creates_v2_tables_without_touching_tasks(tmp_path):
    """从 v1 升级到最新：新增两张表 + v3 判题列，历史任务不受影响。"""
    path = tmp_path / "v1.db"
    raw = sqlite3.connect(str(path))
    try:
        create_schema(raw)
        raw.execute("PRAGMA user_version = 1")
        raw.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source, topic_id)"
            " VALUES ('v1 任务', '', '学习', 45, 3, 'active', '2026-09-06', 0,"
            " '2026-09-06T10:00:00', '2026-09-06T10:00:00', 'generated', 27)"
        )
        raw.commit()
        assert get_schema_version(raw) == 1
    finally:
        raw.close()

    conn = get_connection(path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "knowledge_points" in tables
        assert "assessment_attempts" in tables
        # 历史任务原样保留
        row = conn.execute(
            "SELECT title, status, scheduled_date FROM tasks WHERE title='v1 任务'"
        ).fetchone()
        assert row["title"] == "v1 任务"
        assert row["status"] == "active"
        assert row["scheduled_date"] == "2026-09-06"
    finally:
        conn.close()


def test_v3_adds_judge_columns_to_assessment_attempts(tmp_path):
    conn = get_connection(tmp_path / "v3.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(assessment_attempts)"
            )
        }
        assert "judge_status" in cols
        assert "judge_error" in cols
        # 默认值正确
        conn.execute(
            "INSERT INTO assessment_attempts (knowledge_point_id, questions_json,"
            " created_at) VALUES (1, '[]', '2026-09-06T10:00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT judge_status, judge_error FROM assessment_attempts LIMIT 1"
        ).fetchone()
        assert row["judge_status"] == "pending"
        assert row["judge_error"] == ""
    finally:
        conn.close()


def test_v4_adds_review_schedule_and_task_columns(tmp_path):
    conn = get_connection(tmp_path / "v4.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "review_schedule" in tables
        task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "task_type" in task_cols
        assert "knowledge_point_id" in task_cols
        # 旧数据默认 task_type='new'，knowledge_point_id 为 NULL
        conn.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source) VALUES ('t','','学习',1,1,'active','2026-09-06',0,"
            " '2026-09-06T10:00:00','2026-09-06T10:00:00','manual')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT task_type, knowledge_point_id FROM tasks WHERE title='t'"
        ).fetchone()
        assert row["task_type"] == "new"
        assert row["knowledge_point_id"] is None
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "idem.db")
    try:
        assert migrate(conn) == SCHEMA_VERSION
        assert migrate(conn) == SCHEMA_VERSION
        assert get_schema_version(conn) == SCHEMA_VERSION
        # 冪等：两张 v2 表仍各只有一份
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('knowledge_points', 'assessment_attempts')"
        ).fetchone()[0]
        assert count == 2
        # v4 的 review_schedule 表只有一份
        rs = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='review_schedule'"
        ).fetchone()[0]
        assert rs == 1
    finally:
        conn.close()


def test_v5_adds_difficulty_column(tmp_path):
    conn = get_connection(tmp_path / "v5.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "difficulty" in task_cols
        conn.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source) VALUES ('t','','学习',1,1,'active','2026-09-06',0,"
            " '2026-09-06T10:00:00','2026-09-06T10:00:00','manual')"
        )
        conn.commit()
        row = conn.execute("SELECT difficulty FROM tasks WHERE title='t'").fetchone()
        assert row["difficulty"] == "practice"
    finally:
        conn.close()


def test_future_migration_applied_and_idempotent(tmp_path, monkeypatch):
    """模拟未来 v7 迁移：能按序执行，且重复 migrate 不重复执行。

    注：v6（skills/jds/learning_outcomes）已真实存在，因此这里用 v7 模拟。
    """
    path = tmp_path / "future.db"
    # 先建到真实最新版本（v1..v6）
    raw = sqlite3.connect(str(path))
    try:
        migrate(raw)
        assert get_schema_version(raw) == SCHEMA_VERSION
    finally:
        raw.close()

    calls = []

    def _v7(conn):
        calls.append(1)
        add_column_if_not_exists(
            conn, "tasks", "extra_flag", "TEXT NOT NULL DEFAULT 'x'"
        )

    monkeypatch.setitem(schema_module._MIGRATIONS, 7, _v7)
    monkeypatch.setattr(schema_module, "SCHEMA_VERSION", 7)

    conn = get_connection(path)
    try:
        assert get_schema_version(conn) == 7
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "extra_flag" in cols
        # 再次迁移：版本不变，真实 v7 迁移函数不再执行
        assert migrate(conn) == 7
        assert migrate(conn) == 7
        assert len(calls) == 1
    finally:
        conn.close()


def test_add_column_if_not_exists_idempotent(tmp_path):
    conn = get_connection(tmp_path / "col.db")
    try:
        assert add_column_if_not_exists(
            conn, "tasks", "extra_flag", "TEXT NOT NULL DEFAULT 'x'"
        ) is True
        assert add_column_if_not_exists(
            conn, "tasks", "extra_flag", "TEXT NOT NULL DEFAULT 'x'"
        ) is False
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "extra_flag" in cols
    finally:
        conn.close()


# ============================================================
# v6（Phase A：skills / jds / learning_outcomes）
# ============================================================


def test_v6_tables_exist_in_new_db(tmp_path):
    conn = get_connection(tmp_path / "v6.db")
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 6
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "skills" in tables
        assert "jds" in tables
        assert "learning_outcomes" in tables
        # 新表均为空
        assert conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jds").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM learning_outcomes"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_v5_db_upgrade_preserves_all_history(tmp_path):
    """v5 库（含 tasks/knowledge_points/assessment_attempts/review_schedule
    数据）升级到 v6：旧数据逐条保留，仅新增三张表。"""
    path = tmp_path / "v5.db"
    raw = sqlite3.connect(str(path))
    try:
        migrate(raw)
        assert get_schema_version(raw) == SCHEMA_VERSION
        # 造一些旧方向数据（v1..v5 表）
        raw.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source, topic_id, task_type, knowledge_point_id,"
            " difficulty)"
            " VALUES ('历史任务', 'desc', '学习', 60, 3, 'done', '2026-09-06', 2,"
            " '2026-09-06T10:00:00', '2026-09-06T10:00:00', 'generated', 27,"
            " 'new', NULL, 'practice')"
        )
        raw.execute(
            "INSERT INTO study_plans (name, description, start_date, end_date,"
            " status) VALUES ('pl','d','2026-09-01','2026-12-31','active')"
        )
        raw.execute(
            "INSERT INTO study_phases (plan_id, name, description, start_date,"
            " end_date, priority, goals) VALUES (1,'ph','d','2026-09-01',"
            "'2026-12-31',2,'g')"
        )
        raw.execute(
            "INSERT INTO study_topics (phase_id, name, description,"
            " estimated_minutes, priority, order_index)"
            " VALUES (1,'PyTorch 张量与自动求导（Tensor / autograd）','',60,3,0)"
        )
        raw.execute(
            "INSERT INTO knowledge_points (name, description, last_assessed_at,"
            " mastery_estimate, review_count, created_at, updated_at)"
            " VALUES ('pytorch.autograd','', '2026-09-06T10:00:00', 0.72, 1,"
            " '2026-09-06T10:00:00','2026-09-06T10:00:00')"
        )
        raw.execute(
            "INSERT INTO assessment_attempts (knowledge_point_id, questions_json,"
            " answers_json, ai_result_json, judge_status, created_at)"
            " VALUES (1,'[]','[]','{}','judged','2026-09-06T10:00:00')"
        )
        raw.execute(
            "INSERT INTO review_schedule (knowledge_point_id, scheduled_date,"
            " interval_days, status, created_at)"
            " VALUES (1,'2026-09-08',3,'pending','2026-09-06T10:00:00')"
        )
        raw.commit()
        # 手工把版本降级到 5（模拟只在 v5 的真实旧库）
        raw.execute("PRAGMA user_version = 5")
        raw.commit()
        assert get_schema_version(raw) == 5
    finally:
        raw.close()

    conn = get_connection(path)
    try:
        assert get_schema_version(conn) == SCHEMA_VERSION
        assert conn.execute(
            "SELECT COUNT(*) FROM skills"
        ).fetchone()[0] == 0

        # 旧数据逐条保留
        t = conn.execute(
            "SELECT title, status, scheduled_date, difficulty, postpone_count"
            " FROM tasks WHERE title='历史任务'"
        ).fetchone()
        assert t["title"] == "历史任务"
        assert t["status"] == "done"
        assert t["scheduled_date"] == "2026-09-06"
        assert t["difficulty"] == "practice"
        assert t["postpone_count"] == 2

        kp = conn.execute(
            "SELECT name, mastery_estimate FROM knowledge_points"
        ).fetchone()
        assert kp["name"] == "pytorch.autograd"
        assert abs(kp["mastery_estimate"] - 0.72) < 1e-6

        att = conn.execute(
            "SELECT judge_status FROM assessment_attempts"
        ).fetchone()
        assert att["judge_status"] == "judged"

        rs = conn.execute(
            "SELECT scheduled_date, interval_days FROM review_schedule"
        ).fetchone()
        assert rs["scheduled_date"] == "2026-09-08"
        assert rs["interval_days"] == 3

        # 新表可读写
        conn.execute(
            "INSERT INTO skills (name, tier, category, status, created_at,"
            " updated_at) VALUES ('Python','S','core','mastered',"
            " '2026-09-08T10:00:00','2026-09-08T10:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM skills"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_v6_tables_survive_repeat_migrate(tmp_path):
    conn = get_connection(tmp_path / "v6idem.db")
    try:
        migrate(conn)
        migrate(conn)
        for table in ("skills", "jds", "learning_outcomes"):
            n = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                " AND name=?", (table,)
            ).fetchone()[0]
            assert n == 1
    finally:
        conn.close()
