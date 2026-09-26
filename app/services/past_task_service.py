"""跨日未确认正式任务（昨日任务补确认）业务层。

背景：用户当天实际完成了学习任务却忘记点【完成】，系统会把该 task 当作
未处理，从而影响 phase / coverage / Planner。为在“自动归档为
not_done”与“生成今日计划”之前拦截，这里提供：

- find_unresolved(today)：找出今天以前仍未明确处理（status=active）且用户可执行
  的任务（Agent new / manual learning activity / manual knowledge / legacy extra），按日期从旧到新排序；
cancelled（用户主动移除）不在 active 之内，天然不会被询问。
- apply_decisions(decisions)：把用户在补确认窗口的选择，**复用现有
  TaskService 业务逻辑**落库（完成=complete_task，未完成=mark_not_done）。

边界：不询问 cancelled / done / not_done / 正式 review / 系统内部任务；
不猜状态、不写 mastery、不创建 assessment。
"""

from __future__ import annotations

from ..database.repository import TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from ..utils.date_utils import today as _today
from .task_service import ALLOWED_TRANSITIONS, TaskService

# 补确认时“未完成”使用的说明（仍走 mark_not_done 流程）
PAST_NOT_DONE_REASON = "跨日补确认：用户选择未完成"

DECISION_DONE = "done"
DECISION_NOT_DONE = "not_done"

# 需要（且允许）补确认的用户可执行任务：Agent new / manual learning activity /
# manual knowledge。正式 review 与系统任务不在其中。
# 注：legacy extra 已移除功能，不再阻塞启动（由 legacy cleanup 置 cancelled）。
_EXECUTABLE_TASK_TYPES = {"new", "manual"}
_EXECUTABLE_SOURCES = {"generated", "manual"}


class PastTaskConfirmationService:
    def __init__(
        self, repo: TaskRepository, task_service: TaskService | None = None
    ):
        self.repo = repo
        self.task_service = task_service or TaskService(repo)

    # ---------- 查询 ----------

    def find_unresolved(self, today: str | None = None) -> list:
        """今天以前仍未明确处理的用户可执行任务（按日期从旧到新）。

        条件：scheduled_date < today AND status=active，且
        task_type ∈ {new, manual}、source ∈ {generated, manual}。
        不返回 done / not_done / cancelled（已明确或已移除），
        也不返回历史 review 或 legacy extra。
        """
        date = today or _today()
        tasks = [
            t for t in self.repo.list_active_before(date)
            if t.task_type in _EXECUTABLE_TASK_TYPES
            and t.source in _EXECUTABLE_SOURCES
        ]
        tasks.sort(key=lambda t: (t.scheduled_date, t.id))
        return tasks

    # ---------- 提交 ----------

    def apply_decisions(self, decisions: dict[int, str]) -> dict:
        """按 {task_id: 'done' | 'not_done'} 落库（复用 TaskService）。

        先整体校验所有目标状态转换合法，再依次执行，避免中途才失败。
        任一步失败都会抛出（调用方不得继续进入今日流程）。
        """
        items = list((decisions or {}).items())
        # 1) 先校验（不改数据）
        for task_id, decision in items:
            if decision not in (DECISION_DONE, DECISION_NOT_DONE):
                raise ValueError(f"未知的确认选项: {decision!r}")
            task = self.task_service.get_task(task_id)
            target = (
                STATUS_DONE if decision == DECISION_DONE
                else STATUS_NOT_DONE
            )
            if target not in ALLOWED_TRANSITIONS.get(task.status, set()):
                raise ValueError(
                    f"任务（id={task_id}）当前状态 {task.status} "
                    f"无法变更为 {target}"
                )
        # 2) 依次执行现有业务流程
        applied: list[int] = []
        for task_id, decision in items:
            if decision == DECISION_DONE:
                self.task_service.complete_task(task_id)
            else:
                self.task_service.mark_not_done(task_id, PAST_NOT_DONE_REASON)
            applied.append(task_id)
        return {"applied": applied}
