"""测试辅助工具：可复用的日期注入、fixed-today 构建与延后任务制造。"""

from __future__ import annotations

from contextlib import contextmanager

from app.utils.date_utils import override_today

# 用于模拟"从 2026-09-04 进入 2026-09-05"的一组固定日期
D9_04 = "2026-09-04"
D9_05 = "2026-09-05"


def make_today_provider(date_str: str):
    """构造返回固定日期的 provider（供 MainWindow / service 注入）。"""
    return lambda: date_str


@contextmanager
def fixed_today(date_str: str):
    """与 override_today 等价，语义更明确的测试辅助。"""
    with override_today(date_str):
        yield


def postpone_task_to_tomorrow(task_service, task_id: int, reason: str = "没时间"):
    """把任务标记未完成并延期到下一天，返回延期后的任务。"""
    task_service.mark_not_done(task_id, reason)
    return task_service.postpone_task(task_id)
