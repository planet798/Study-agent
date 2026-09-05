"""任务业务逻辑层。

职责：
- 业务规则与状态流转校验（此处），数据访问（repository）分离。
- 明确区分三种状态：
    active   = 待办（当日任务，尚未处理）
    done     = 用户明确完成
    not_done = 用户明确提交了"未完成原因"
- 延期（postpone）本质上是对 not_done 任务的下一步处理：
    把任务改期到下一天并恢复为 active，postpone_count + 1。
"""

from __future__ import annotations

from ..database.repository import Task, TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from ..utils.date_utils import add_days


class TaskNotFoundError(Exception):
    """任务不存在。"""


class InvalidTransitionError(Exception):
    """非法的状态转换。"""


# 允许的状态转换表：{当前状态: 可转换到的状态集合}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    STATUS_ACTIVE: {STATUS_DONE, STATUS_NOT_DONE},  # 待办：可完成 / 可标记未完成
    STATUS_NOT_DONE: {STATUS_ACTIVE},               # 未完成：只能延期回待办
    STATUS_DONE: set(),                             # 已完成：终态，不可再转换
}


class TaskService:
    """对 Task 的业务操作入口。"""

    def __init__(self, repo: TaskRepository):
        self.repo = repo

    # ---------- 查询 ----------

    def get_task(self, task_id: int) -> Task:
        task = self.repo.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在: id={task_id}")
        return task

    def get_status(self, task_id: int) -> str:
        """获取任务当前状态：active / done / not_done。"""
        return self.get_task(task_id).status

    def get_details(self, task_id: int) -> dict:
        """获取任务完成/未完成详情。"""
        t = self.get_task(task_id)
        return {
            "task_id": t.id,
            "title": t.title,
            "status": t.status,
            "is_done": t.status == STATUS_DONE,
            "is_not_done": t.status == STATUS_NOT_DONE,
            "completed_at": t.completed_at,
            "not_done_at": t.not_done_at,
            "reason": t.reason,
            "postpone_count": t.postpone_count,
        }

    def get_tasks_by_date(self, date_str: str) -> list[Task]:
        """指定日期的全部任务（含各种状态）。"""
        return self.repo.list_by_date(date_str)

    def get_active_tasks_by_date(self, date_str: str) -> list[Task]:
        """指定日期仍待办（active）的任务。"""
        return self.repo.list_active_by_date(date_str)

    def get_daily_stats(self, date_str: str) -> dict:
        """指定日期的统计：total / done / not_done / active / rate。

        供 UI 展示，通过 service 间接访问 repository，保持层次清晰。
        """
        return self.repo.stats_by_date(date_str)

    def get_study_time_stats(self, date_str: str) -> dict:
        """指定日期的预计/实际学习时间统计（分钟）。

        - total_minutes: 全部任务的预计时间之和
        - done_minutes: 已完成任务的预计时间之和
        """
        tasks = self.repo.list_by_date(date_str)
        total = sum(t.estimated_minutes for t in tasks)
        done = sum(t.estimated_minutes for t in tasks if t.status == STATUS_DONE)
        return {"total_minutes": total, "done_minutes": done}

    # ---------- 业务操作 ----------

    def _transition(self, task_id: int, to_status: str) -> Task:
        """校验并执行状态转换，返回最新 Task。"""
        task = self.get_task(task_id)
        allowed = ALLOWED_TRANSITIONS.get(task.status, set())
        if to_status not in allowed:
            raise InvalidTransitionError(
                f"非法状态转换: {task.status} -> {to_status} (任务 id={task_id})"
            )
        return task

    def create_task(
        self,
        title: str,
        scheduled_date: str | None = None,
        description: str = "",
        category: str = "学习",
        estimated_minutes: int = 0,
        priority: int = 1,
        source: str = "manual",
        topic_id: int | None = None,
    ) -> Task:
        """创建任务，默认放到 scheduled_date 当天。"""
        return self.repo.create(
            title=title,
            scheduled_date=scheduled_date,
            description=description,
            category=category,
            estimated_minutes=estimated_minutes,
            priority=priority,
            source=source,
            topic_id=topic_id,
        )

    def complete_task(self, task_id: int) -> Task:
        """标记任务完成：active -> done。"""
        self._transition(task_id, STATUS_DONE)
        self.repo.mark_done(task_id)
        return self.get_task(task_id)

    def mark_not_done(self, task_id: int, reason: str) -> Task:
        """标记未完成：active -> not_done，reason 必须非空。"""
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("未完成原因不能为空")
        self._transition(task_id, STATUS_NOT_DONE)
        self.repo.mark_not_done(task_id, reason)
        return self.get_task(task_id)

    def postpone_task(self, task_id: int) -> Task:
        """延期任务到下一天。

        延期是对 not_done 任务的下一步处理：
        - 仅当任务当前为 not_done 时允许延期；
        - scheduled_date 顺延一天，状态恢复为 active（进入明天待办）；
        - postpone_count 自动 + 1（repository.postpone 负责累加）。
        """
        # 状态校验：not_done -> active 是唯一合法的延期路径
        self._transition(task_id, STATUS_ACTIVE)
        task = self.get_task(task_id)
        new_date = add_days(task.scheduled_date, 1)
        self.repo.postpone(task_id, new_date)
        # postpone 仅改日期与次数，这里显式恢复为待办状态
        self.repo.set_status(task_id, STATUS_ACTIVE)
        return self.get_task(task_id)

    def auto_archive(self, task_id: int, reason: str) -> Task:
        """把过期遗留的待办任务直接归档为 not_done。

        用于日期切换时自动处理"前一天未完成且未延期"的任务。
        允许任意待办状态直接归档，reason 由调用方提供（系统原因）。
        """
        task = self.get_task(task_id)
        if task.status == STATUS_DONE:
            # 已完成任务不再归档
            return task
        self.repo.mark_not_done(task_id, reason)
        return self.get_task(task_id)