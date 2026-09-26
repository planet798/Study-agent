"""Phase B：v11 → v12 迁移测试（无损 / 幂等 / 回填）。

覆盖需求 37 的 1~2、16~26、31：
- 迁移后默认路线存在、默认 plan 绑定；
- 历史 phase/topic/task/kp ID 不变、任务不删除；
- generated / topic-linked manual / review task 回填 route；
- 历史未分类手动学习活动保持 NULL；
- topic-linked kp 回填、manual kp 保持 NULL；
- planner_decisions 回填；
- route_skills 由 skills.linked_topics 推导；
- 重复 migrate 幂等。
"""

from __future__ import annotations

import sqlite3

from app.database import schema as schema_module
from app.database.connection import get_connection
from app.database.learning_route_repository import (
    DEFAULT_ROUTE_LEARNING_NAME,
    LearningRouteRepository,
)
from app.database.schema import SCHEMA_VERSION, migrate


def _make_v11_db(path):
    """构造一个真实的 v11 数据库（运行 v2~v11 迁移）。"""
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    schema_module.create_schema(raw)
    for version in range(2, 12):
        schema_module._MIGRATIONS[version](raw)
    raw.execute("PRAGMA user_version = 11")
    raw.commit()

    # ---- 历史学习计划 ----
    raw.execute(
        "INSERT INTO study_plans (id, name, description, start_date, end_date, status)"
        " VALUES (1, 'USTC AI 研一大厂算法路线', '', '2026-09-01', '2027-08-31', 'active')"
    )
    raw.execute(
        "INSERT INTO study_phases (id, plan_id, name, description, start_date,"
        " end_date, priority, goals) VALUES (1, 1, '阶段一', '', '2026-09-01',"
        " '2026-12-31', 1, '')"
    )
    raw.execute(
        "INSERT INTO study_topics (id, phase_id, name, description,"
        " estimated_minutes, priority, order_index) VALUES (1, 1, 'Transformer',"
        " '', 60, 3, 0)"
    )
    raw.execute(
        "INSERT INTO study_topics (id, phase_id, name, description,"
        " estimated_minutes, priority, order_index) VALUES (2, 1, 'RAG', '', 60, 2, 1)"
    )

    # ---- 历史知识点 ----
    raw.execute(
        "INSERT INTO knowledge_points (id, topic_id, name, description,"
        " mastery_estimate, review_count, interval_days, created_at, updated_at)"
        " VALUES (1, 1, 'Transformer', '', 0.6, 1, 3, '2026-09-01', '2026-09-01')"
    )
    raw.execute(
        "INSERT INTO knowledge_points (id, topic_id, name, description,"
        " mastery_estimate, review_count, interval_days, created_at, updated_at)"
        " VALUES (2, NULL, '我的临时知识点', '', 0.0, 0, 0, '2026-09-01', '2026-09-01')"
    )

    # ---- 历史任务（v11 无 route_id）----
    def _task(tid, title, source, ttype, topic_id, kp_id, status="active"):
        raw.execute(
            "INSERT INTO tasks (id, title, description, category,"
            " estimated_minutes, priority, status, scheduled_date,"
            " postpone_count, created_at, updated_at, source, topic_id,"
            " task_type, knowledge_point_id, difficulty) VALUES (?, ?, '', '学习',"
            " 30, 1, ?, '2026-09-10', 0, '2026-09-10', '2026-09-10', ?, ?, ?, ?,"
            " 'practice')",
            (tid, title, status, source, topic_id, ttype, kp_id),
        )

    _task(1, "Agent Transformer", "generated", "new", 1, 1)
    _task(2, "手动关联 Transformer", "manual", "new", 2, None)
    _task(3, "普通 To-do", "manual", "manual", None, None)
    _task(4, "手动临时知识点", "manual", "new", None, 2)
    _task(5, "复习 Transformer", "review", "review", None, 1)
    raw.commit()

    # ---- planner decision ----
    raw.execute(
        "INSERT INTO planner_decisions (id, date, current_phase_id, input_context,"
        " ai_response, accepted_tasks, source, created_at) VALUES (1, '2026-09-10',"
        " 1, '{}', '{}', '[]', 'ai', '2026-09-10')"
    )

    # ---- skill（linked_topics 含 topic 1）----
    raw.execute(
        "INSERT INTO skills (id, name, tier, category, status, mastery_ref,"
        " jd_frequency, priority_score, prerequisites, linked_topics,"
        " shared_connector, created_at, updated_at) VALUES (1, 'Transformer', 'S',"
        " 'core', 'learning', '', '{}', 0.0, '[]', '[1]', 0, '2026-09-01',"
        " '2026-09-01')"
    )
    raw.commit()
    raw.close()


def _default_route_id(conn):
    repo = LearningRouteRepository(conn)
    return repo.get_default_learning_route().id


class TestMigrationV12:
    def test_migration_and_default_routes(self, tmp_path):
        path = tmp_path / "v11.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            assert SCHEMA_VERSION >= 12
            repo = LearningRouteRepository(conn)
            routes = repo.list_all()
            assert len(routes) == 2
            default = repo.get_default_learning_route()
            assert default.name == DEFAULT_ROUTE_LEARNING_NAME
            # 默认 plan 绑定默认 route
            plan_route = conn.execute(
                "SELECT route_id FROM study_plans WHERE id = 1"
            ).fetchone()[0]
            assert plan_route == default.id
        finally:
            conn.close()

    def test_ids_and_rows_preserved(self, tmp_path):
        path = tmp_path / "ids.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 5
            assert conn.execute(
                "SELECT id FROM study_phases ORDER BY id"
            ).fetchall()[0][0] == 1
            assert [r[0] for r in conn.execute(
                "SELECT id FROM study_topics ORDER BY id")] == [1, 2]
            assert [r[0] for r in conn.execute(
                "SELECT id FROM knowledge_points ORDER BY id")] == [1, 2]
            # 任务 ID 保持
            assert [r[0] for r in conn.execute(
                "SELECT id FROM tasks ORDER BY id")] == [1, 2, 3, 4, 5]
        finally:
            conn.close()

    def test_task_route_backfill(self, tmp_path):
        path = tmp_path / "tasks.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            default = _default_route_id(conn)
            rows = {
                r["id"]: r["route_id"]
                for r in conn.execute("SELECT id, route_id FROM tasks")
            }
            assert rows[1] == default      # generated/new
            assert rows[2] == default      # manual linked topic
            assert rows[3] is None         # historical unclassified manual learning activity
            assert rows[4] is None         # manual temp kp
            assert rows[5] == default      # review (kp route)
        finally:
            conn.close()

    def test_kp_route_backfill(self, tmp_path):
        path = tmp_path / "kp.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            default = _default_route_id(conn)
            rows = {
                r["id"]: r["route_id"]
                for r in conn.execute("SELECT id, route_id FROM knowledge_points")
            }
            assert rows[1] == default
            assert rows[2] is None
        finally:
            conn.close()

    def test_planner_decision_backfill(self, tmp_path):
        path = tmp_path / "pd.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            default = _default_route_id(conn)
            route_id = conn.execute(
                "SELECT route_id FROM planner_decisions WHERE id = 1"
            ).fetchone()[0]
            assert route_id == default
        finally:
            conn.close()

    def test_route_skills_backfill(self, tmp_path):
        path = tmp_path / "rs.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            default = _default_route_id(conn)
            rows = conn.execute(
                "SELECT route_id, skill_id FROM route_skills"
            ).fetchall()
            assert [(r[0], r[1]) for r in rows] == [(default, 1)]
        finally:
            conn.close()

    def test_repeat_migrate_idempotent(self, tmp_path):
        path = tmp_path / "idem.db"
        _make_v11_db(path)
        conn = get_connection(path)
        try:
            assert migrate(conn) == SCHEMA_VERSION
            assert migrate(conn) == SCHEMA_VERSION
            assert conn.execute(
                "SELECT COUNT(*) FROM learning_routes"
            ).fetchone()[0] == 2
            assert conn.execute(
                "SELECT COUNT(*) FROM route_skills"
            ).fetchone()[0] == 1
            # 任务/知识点不重复、不删除
            assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 5
            assert conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE route_id IS NOT NULL"
            ).fetchone()[0] == 3
        finally:
            conn.close()
