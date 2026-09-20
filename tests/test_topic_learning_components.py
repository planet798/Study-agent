"""Topic Learning Component 数据层与迁移测试（Phase 2）。"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.topic_learning_repository import (
    TopicLearningComponentRepository,
)
from app.services.learning_activity import ALL_ACTIVITY_KINDS


@pytest.fixture()
def sample_topic(plan_repo):
    plan = plan_repo.create_plan("P", "2026-01-01", "2099-12-31")
    phase = plan_repo.create_phase(plan.id, "Ph", "2026-01-01", "2099-12-31")
    return plan_repo.create_topic(phase.id, "T")


class TestSchemaV16:
    def test_v16_schema_version_and_tables(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 16
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "topic_learning_components" in tables
        task_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        assert "component_id" in task_cols
        assert "learning_activity_kind" in task_cols

    def test_check_constraint_rejects_invalid_kind(self, conn, sample_topic):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO topic_learning_components "
                "(topic_id, activity_kind, enabled, required, order_index,"
                " created_at, updated_at) VALUES (?, 'bogus', 1, 1, 1, '', '')",
                (sample_topic.id,),
            )
        conn.rollback()


class TestRepository:
    def _repo(self, conn):
        return TopicLearningComponentRepository(conn)

    def test_five_valid_kinds(self, conn, sample_topic):
        repo = self._repo(conn)
        for kind in ALL_ACTIVITY_KINDS:
            c = repo.create(sample_topic.id, kind)
            assert c["activity_kind"] == kind
        assert len(repo.list_by_topic(sample_topic.id)) == 5

    def test_invalid_kind_rejected(self, conn, sample_topic):
        with pytest.raises(ValueError):
            self._repo(conn).create(sample_topic.id, "nope")

    def test_unique_topic_kind(self, conn, sample_topic):
        repo = self._repo(conn)
        repo.create(sample_topic.id, "theory")
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(sample_topic.id, "theory")

    def test_enabled_required_order(self, conn, sample_topic):
        repo = self._repo(conn)
        c = repo.create(sample_topic.id, "theory", enabled=True, required=True,
                        order_index=2)
        repo.update(c["id"], enabled=False, required=False, order_index=5)
        got = repo.get(c["id"])
        assert got["enabled"] is False
        assert got["required"] is False
        assert got["order_index"] == 5

    def test_delete_blocked_when_linked_task(self, conn, sample_topic, repo):
        c = self._repo(conn).create(sample_topic.id, "theory")
        repo.create("任务", scheduled_date="2026-01-05",
                    topic_id=sample_topic.id,
                    component_id=c["id"], learning_activity_kind="theory")
        component_repo = self._repo(conn)
        assert component_repo.has_linked_tasks(c["id"]) is True
        with pytest.raises(ValueError):
            component_repo.delete(c["id"])
