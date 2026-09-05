"""task_service 业务逻辑测试。"""

from __future__ import annotations

import pytest

from app.database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from app.services.task_service import InvalidTransitionError, TaskNotFoundError


class TestCreateTask:
    def test_create_task(self, task_service):
        t = task_service.create_task(
            "复习数学", scheduled_date="2026-01-05", priority=3
        )
        assert t.title == "复习数学"
        assert t.scheduled_date == "2026-01-05"
        assert t.status == STATUS_ACTIVE
        assert t.priority == 3

    def test_get_tasks_by_date(self, task_service):
        task_service.create_task("A", scheduled_date="2026-01-05")
        task_service.create_task("B", scheduled_date="2026-01-05")
        task_service.create_task("C", scheduled_date="2026-01-06")
        tasks = task_service.get_tasks_by_date("2026-01-05")
        assert len(tasks) == 2

    def test_get_active_tasks_by_date_filters_status(self, task_service):
        a = task_service.create_task("A", scheduled_date="2026-01-05")
        task_service.complete_task(a.id)
        task_service.create_task("B", scheduled_date="2026-01-05")
        active = task_service.get_active_tasks_by_date("2026-01-05")
        assert len(active) == 1
        assert active[0].title == "B"


class TestCompleteTask:
    def test_complete_active_task(self, task_service):
        t = task_service.create_task("做题", scheduled_date="2026-01-05")
        done = task_service.complete_task(t.id)
        assert done.status == STATUS_DONE
        assert done.completed_at is not None

    def test_get_status(self, task_service):
        t = task_service.create_task("看状态", scheduled_date="2026-01-05")
        assert task_service.get_status(t.id) == STATUS_ACTIVE
        task_service.complete_task(t.id)
        assert task_service.get_status(t.id) == STATUS_DONE


class TestMarkNotDone:
    def test_reason_required(self, task_service):
        t = task_service.create_task("未完成", scheduled_date="2026-01-05")
        with pytest.raises(ValueError):
            task_service.mark_not_done(t.id, "")

    def test_valid_not_done(self, task_service):
        t = task_service.create_task("未完成", scheduled_date="2026-01-05")
        got = task_service.mark_not_done(t.id, "今天有事")
        assert got.status == STATUS_NOT_DONE
        assert got.reason == "今天有事"

    def test_get_details_after_not_done(self, task_service):
        t = task_service.create_task("详情", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "没复习完")
        d = task_service.get_details(t.id)
        assert d["is_not_done"] is True
        assert d["reason"] == "没复习完"
        assert d["not_done_at"] is not None
        assert d["is_done"] is False


class TestPostpone:
    def test_postpone_moves_to_next_day_and_increments(self, task_service):
        t = task_service.create_task("延期任务", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "没完成")
        got = task_service.postpone_task(t.id)
        assert got.scheduled_date == "2026-01-06"
        assert got.status == STATUS_ACTIVE
        assert got.postpone_count == 1

    def test_postpone_count_accumulates(self, task_service):
        t = task_service.create_task("多次延期", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "一")
        task_service.postpone_task(t.id)          # -> 01-06, count=1
        task_service.mark_not_done(t.id, "二")
        task_service.postpone_task(t.id)          # -> 01-07, count=2
        task_service.mark_not_done(t.id, "三")
        got = task_service.postpone_task(t.id)    # -> 01-08, count=3
        assert got.scheduled_date == "2026-01-08"
        assert got.postpone_count == 3

    def test_postpone_requires_not_done_previous_step(self, task_service):
        # active 状态不能直接延期 —— 延期是对 not_done 的下一步处理
        t = task_service.create_task("直接延期", scheduled_date="2026-01-05")
        with pytest.raises(InvalidTransitionError):
            task_service.postpone_task(t.id)


class TestInvalidTransitions:
    def test_complete_already_done(self, task_service):
        t = task_service.create_task("做一次", scheduled_date="2026-01-05")
        task_service.complete_task(t.id)
        with pytest.raises(InvalidTransitionError):
            task_service.complete_task(t.id)

    def test_mark_not_done_on_done(self, task_service):
        t = task_service.create_task("已完成", scheduled_date="2026-01-05")
        task_service.complete_task(t.id)
        with pytest.raises(InvalidTransitionError):
            task_service.mark_not_done(t.id, "不应该")

    def test_postpone_done_task(self, task_service):
        t = task_service.create_task("已完成的", scheduled_date="2026-01-05")
        task_service.complete_task(t.id)
        with pytest.raises(InvalidTransitionError):
            task_service.postpone_task(t.id)

    def test_complete_from_not_done(self, task_service):
        # 未完成任务必须先延期，不能直接从 not_done 变 done
        t = task_service.create_task("X", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "原因")
        with pytest.raises(InvalidTransitionError):
            task_service.complete_task(t.id)

    def test_mark_not_done_twice(self, task_service):
        t = task_service.create_task("Y", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "原因")
        with pytest.raises(InvalidTransitionError):
            task_service.mark_not_done(t.id, "再次")


class TestNotFound:
    def test_get_task_missing(self, task_service):
        with pytest.raises(TaskNotFoundError):
            task_service.get_task(9999)

    def test_operate_on_missing_task(self, task_service):
        with pytest.raises(TaskNotFoundError):
            task_service.complete_task(9999)
