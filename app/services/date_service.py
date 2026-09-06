"""日期切换业务逻辑层。

职责：程序启动时调用 process_date_transition(current_date)，
把"今天"的正确性交给它负责，保证：

- 只处理一次：通过 app_meta.last_processed_date 记录上次处理到的日期；
- 幂等：同一个 current_date 调用多次，数据库结果完全一致；
- 延期任务自动进入第二天：延期动作（postpone_task）本身就把
  scheduled_date 顺延一天，因此第二天启动时它天然位于"今天"；
- 前一天未被处理、也未手动延期/完成的遗留任务（过期 active）：
  自动归档为 not_done（记录系统原因），并在最后处理日期记录中推进；
- 为以后"自动生成每日任务"预留扩展点（_generate_tasks_for_day）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..database.repository import TaskRepository
from ..database.schema import STATUS_ACTIVE
from ..utils.date_utils import add_days
from .task_service import TaskService

if TYPE_CHECKING:
    from .daily_planner_service import DailyPlannerService
    from .study_plan_service import StudyPlanService

# app_meta 中记录"最后处理日期"的键
LAST_PROCESSED_DATE_KEY = "last_processed_date"

# 跨天遗留任务自动归档时使用的系统原因
AUTO_ARCHIVE_REASON = "跨天未处理（未手动延期），系统自动归档"


class DateService:
    def __init__(
        self,
        repo: TaskRepository,
        task_service: TaskService | None = None,
        study_plan_service: "StudyPlanService | None" = None,
        daily_planner_service: "DailyPlannerService | None" = None,
    ):
        self.repo = repo
        self.task_service = task_service or TaskService(repo)
        # 规则型每日任务生成服务（可选注入；不传则不做自动生成）
        self.study_plan_service = study_plan_service
        # AI 动态规划服务（可选注入；优先级高于规则型）
        self.daily_planner_service = daily_planner_service

    # ---------- 状态查询 ----------

    def get_last_processed_date(self) -> str | None:
        """读取数据库记录的最后处理日期。"""
        return self.repo.get_meta(LAST_PROCESSED_DATE_KEY)

    # ---------- 日期切换 ----------

    def process_date_transition(self, current_date: str) -> dict:
        """执行日期切换。

        :param current_date: 今天（YYYY-MM-DD）
        :return: 处理结果 dict，含 processed / reason / archived 等信息
        """
        last = self.get_last_processed_date()
        result = {
            "processed": False,
            "last_processed_date": last,
            "current_date": current_date,
            "archived": [],
            "generated": [],
        }

        # 情况1：今天已经处理过 —— 幂等，不做日期切换
        if last == current_date:
            result["reason"] = "already_processed"
            # 恢复：若今天已处理过但没有任何任务（例如上次生成因阶段全部完成而为空），
            # 则再尝试生成一次；当天已有任务时此分支不做任何事，保证幂等。
            if not self.repo.list_by_date(current_date):
                generated = self._generate_tasks_for_day(current_date)
                result["generated"] = [t.id for t in generated]
            return result

        # 情况2：首次运行 —— 记录今天作为处理基准，无历史遗留
        if last is None:
            self.repo.set_meta(LAST_PROCESSED_DATE_KEY, current_date)
            generated = self._generate_tasks_for_day(current_date)
            result.update(
                processed=True,
                reason="first_run",
                last_processed_date=current_date,
                generated=[t.id for t in generated],
            )
            return result

        # 情况3：进入新的一天（last < current_date）
        #   a) 处理前一天未完成的任务：遗留的过期 active 任务 → 归档 not_done
        #      （不移动日期，因此"未延期的任务不会自动进入下一天"）
        leftover = self.repo.list_active_before(current_date)
        for t in leftover:
            self._archive_stale_task(t.id)
        result["archived"] = [t.id for t in leftover]

        #   b) 延期任务已通过 postpone_task 顺延到今天，无需额外搬运
        #   c) 推进最后处理日期
        self.repo.set_meta(LAST_PROCESSED_DATE_KEY, current_date)

        #   d) 根据学习计划生成今日任务（幂等，不重复生成）
        generated = self._generate_tasks_for_day(current_date)

        result.update(
            processed=True,
            reason="new_day",
            last_processed_date=current_date,
            generated=[t.id for t in generated],
        )
        return result

    # ---------- 内部扩展点 ----------

    def _archive_stale_task(self, task_id: int) -> None:
        """把过期遗留任务归档为 not_done（业务规则仍走 task_service）。"""
        task = self.task_service.get_task(task_id)
        if task.status != STATUS_ACTIVE:
            # 已完成或已归档的任务不动
            return
        self.task_service.auto_archive(task_id, AUTO_ARCHIVE_REASON)

    def _generate_tasks_for_day(self, current_date: str) -> list:
        """为 current_date 生成每日任务（幂等，不重复生成）。

        顺序保障（在 process_date_transition 内）:
        日期状态处理 -> 延期/归档任务处理 -> 此处生成新任务。

        策略（优先级从高到低）：
        1. 若注入了 daily_planner_service（AI），先试 AI 动态规划，
           失败时内部自动 fallback 到规则型生成；
        2. 否则若注入了 study_plan_service，使用规则型 generate_daily_tasks；
        3. 两者都未注入则不做任何事，纯本地功能不受影响。
        """
        if self.daily_planner_service is not None:
            # AI 规划器输入"昨天"，规划出"今天"（本身就是为下一天规划）
            yesterday = add_days(current_date, -1)
            result = self.daily_planner_service.generate_next_day_plan(yesterday)
            created = result.get("created", [])
            return self.repo_rows_by_ids(created)
        if self.study_plan_service is None:
            return []
        result = self.study_plan_service.generate_daily_tasks(current_date)
        return result.get("generated", [])

    def repo_rows_by_ids(self, ids: list[int]):
        """按 id 列表读取任务，供返回。"""
        out = []
        for tid in ids:
            t = self.repo.get(tid)
            if t is not None:
                out.append(t)
        return out
