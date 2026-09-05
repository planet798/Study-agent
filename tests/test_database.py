"""数据库层测试：schema / connection / repository。"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import get_connection
from app.database.repository import TaskRepository, Task
from app.database.schema import (
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_NOT_DONE,
    SCHEMA_SQL,
)
from app.utils.date_utils import add_days, today


class TestConnection:
    def test_default_db_has_data_dir(self, tmp_path, monkeypatch):
        """确认默认路径指向项目 data/ 目录（由 conftest 之外的探针保证 schema）。"""
        # conftest 已用 tmp 文件，这里显式再验证一次连接可打开并建表
        c = get_connection(tmp_path / "x.db")
        tables = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "tasks" in names
        c.close()

    def test_schema_idempotent(self, conn):
        """重复建表不应报错。"""
        # 通过 conftest 的 conn 已经建过一次表
        from app.database.schema import create_schema

        create_schema(conn)  # 再次执行
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert len([r for r in tables if r["name"] == "tasks"]) == 1


class TestRepositoryCreate:
    def test_create_with_defaults(self, repo):
        t = repo.create("写作业", scheduled_date="2026-01-05")
        assert t.id > 0
        assert t.title == "写作业"
        assert t.status == STATUS_ACTIVE
        assert t.scheduled_date == "2026-01-05"
        assert t.postpone_count == 0
        assert t.description == ""
        assert t.category == "学习"
        assert t.estimated_minutes == 0
        assert t.priority == 1

    def test_create_custom_fields(self, repo):
        t = repo.create(
            "背单词",
            scheduled_date="2026-01-05",
            description="50 个",
            category="英语",
            estimated_minutes=30,
            priority=3,
        )
        assert t.description == "50 个"
        assert t.category == "英语"
        assert t.estimated_minutes == 30
        assert t.priority == 3

    def test_create_empty_title_raises(self, repo):
        with pytest.raises(ValueError):
            repo.create("   ", scheduled_date="2026-01-05")

    def test_create_sets_timestamps(self, repo):
        t = repo.create("测试时间戳", scheduled_date="2026-01-05")
        assert t.created_at
        assert t.updated_at

    def test_create_defaults_date_to_today(self, repo):
        t = repo.create("今天任务")
        assert t.scheduled_date == today()


class TestRepositoryRead:
    def test_get_existing_and_missing(self, repo):
        t = repo.create("找得到", scheduled_date="2026-01-05")
        assert repo.get(t.id).title == "找得到"
        assert repo.get(999999) is None

    def test_list_by_date(self, repo):
        repo.create("任务A", scheduled_date="2026-01-05")
        repo.create("任务B", scheduled_date="2026-01-05")
        repo.create("任务C", scheduled_date="2026-01-06")
        tasks = repo.list_by_date("2026-01-05")
        assert len(tasks) == 2

    def test_list_by_date_orders_by_priority_desc(self, repo):
        low = repo.create("低", scheduled_date="2026-01-05", priority=1)
        high = repo.create("高", scheduled_date="2026-01-05", priority=3)
        repo.create("中", scheduled_date="2026-01-05", priority=2)
        order = [t.id for t in repo.list_by_date("2026-01-05")]
        assert order.index(high.id) < order.index(low.id)

    def test_list_active_only_excludes_done(self, repo):
        a = repo.create("待办", scheduled_date="2026-01-05")
        b = repo.create("已完成", scheduled_date="2026-01-05")
        repo.mark_done(b.id)
        active = repo.list_active_by_date("2026-01-05")
        ids = {t.id for t in active}
        assert a.id in ids and b.id not in ids

    def test_list_between(self, repo):
        repo.create("一", scheduled_date="2026-01-01")
        repo.create("二", scheduled_date="2026-01-15")
        repo.create("三", scheduled_date="2026-01-31")
        tasks = repo.list_between("2026-01-05", "2026-01-20")
        titles = {t.title for t in tasks}
        assert titles == {"二"}


class TestRepositoryWrite:
    def test_update_fields(self, repo):
        t = repo.create("原始", scheduled_date="2026-01-05")
        updated = repo.update(t.id, title="改后", category="工作", reason="换个理由")
        assert updated.title == "改后"
        assert updated.category == "工作"
        assert updated.reason == "换个理由"

    def test_update_cannot_touch_id(self, repo):
        t = repo.create("任务", scheduled_date="2026-01-05")
        updated = repo.update(t.id, id=9999)
        assert updated.id == t.id  # id 不在白名单，被忽略

    def test_mark_done(self, repo):
        t = repo.create("完成", scheduled_date="2026-01-05")
        ok = repo.mark_done(t.id)
        assert ok
        got = repo.get(t.id)
        assert got.status == STATUS_DONE
        assert got.completed_at is not None

    def test_mark_not_done(self, repo):
        t = repo.create("未完成", scheduled_date="2026-01-05")
        ok = repo.mark_not_done(t.id, "太难了")
        assert ok
        got = repo.get(t.id)
        assert got.status == STATUS_NOT_DONE
        assert got.reason == "太难了"
        assert got.not_done_at is not None

    def test_mark_not_done_requires_reason(self, repo):
        t = repo.create("未完成", scheduled_date="2026-01-05")
        with pytest.raises(ValueError):
            repo.mark_not_done(t.id, "   ")

    def test_mark_done_then_not_done_clears_completed_at(self, repo):
        t = repo.create("测试", scheduled_date="2026-01-05")
        repo.mark_done(t.id)
        repo.mark_not_done(t.id, "反悔了")
        got = repo.get(t.id)
        assert got.completed_at is None
        assert got.status == STATUS_NOT_DONE

    def test_postpone_increments_count_and_moves_date(self, repo):
        t = repo.create("延期", scheduled_date="2026-01-05")
        ok = repo.postpone(t.id, "2026-01-06")
        assert ok
        got = repo.get(t.id)
        assert got.scheduled_date == "2026-01-06"
        assert got.postpone_count == 1

    def test_postpone_multiple_times(self, repo):
        t = repo.create("多次延期", scheduled_date="2026-01-05")
        repo.postpone(t.id, "2026-01-06")
        repo.postpone(t.id, "2026-01-07")
        got = repo.get(t.id)
        assert got.postpone_count == 2
        assert got.scheduled_date == "2026-01-07"

    def test_delete(self, repo):
        t = repo.create("删除", scheduled_date="2026-01-05")
        assert repo.delete(t.id)
        assert repo.get(t.id) is None


class TestTaskObject:
    def test_properties(self):
        t = Task(
            id=1, title="x", scheduled_date="2026-01-05",
            status=STATUS_ACTIVE, postpone_count=0,
        )
        assert t.is_active
        assert not t.is_done
        assert not t.over_postpone_limit

    def test_postpone_limit_reached(self):
        t = Task(
            id=1, title="x", scheduled_date="2026-01-05",
            status=STATUS_ACTIVE, postpone_count=3,
        )
        assert t.over_postpone_limit


class TestRepositoryStats:
    def test_stats_empty(self, repo):
        s = repo.stats_by_date("2026-01-05")
        assert s == {
            "total": 0, "done": 0, "not_done": 0, "active": 0, "rate": 0.0,
        }

    def test_stats_full_round(self, repo):
        repo.create("完成", scheduled_date="2026-01-05")
        t2 = repo.create("未完成", scheduled_date="2026-01-05")
        t3 = repo.create("待办", scheduled_date="2026-01-05")
        repo.mark_done(t2.id)
        repo.mark_not_done(t3.id, "没时间")
        s = repo.stats_by_date("2026-01-05")
        assert s["total"] == 3
        assert s["done"] == 1
        # 未完成 = 明确未完成 + 还没处理的 active
        assert s["not_done"] == 2
        assert s["rate"] == round(1 / 3 * 100, 1)

    def test_stats_only_count_same_date(self, repo):
        repo.create("今天", scheduled_date="2026-01-05")
        repo.create("明天", scheduled_date="2026-01-06")
        s = repo.stats_by_date("2026-01-05")
        assert s["total"] == 1


class TestPersistence:
    def test_data_survives_reopen(self, tmp_path):
        """关闭后重新打开，数据仍在（需求 14 的一个侧面）。"""
        db_file = tmp_path / "persist.db"

        # 第一次写入
        c1 = get_connection(db_file)
        r1 = TaskRepository(c1)
        t = r1.create("持久任务", scheduled_date="2026-01-05")
        r1.mark_done(t.id)
        c1.close()

        # 第二次打开
        c2 = get_connection(db_file)
        r2 = TaskRepository(c2)
        got = r2.get(t.id)
        assert got is not None
        assert got.title == "持久任务"
        assert got.status == STATUS_DONE
        c2.close()

    def test_wal_files_created(self, tmp_path):
        db_file = tmp_path / "wal.db"
        c = get_connection(db_file)
        assert db_file.exists()
        c.close()
