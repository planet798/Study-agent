"""多路线全局每日调度（Phase D）。

职责分层（重要）：
    GlobalDailyScheduler  -> 决定“每个 route 今天分到几个 Agent slots”
    Route Planner         -> 只在该 route 内选择 Topic 并生成 Task

设计要点：
- 全局 Agent Daily Budget：所有路线共享，不按路线放大；
- 消耗 budget 的只有 scheduled_date==today、source=generated、
  task_type=new、status != cancelled；
- priority 是“分配权重”，不是硬 one-task-per-route，也不是 topic priority；
- 公平调度：service_ratio = (recent_generated + allocated_today) / priority，
  每分一个 slot 重新比较，天然避免低优先级永久饿死；
- 单路线失败（no_plan / route_complete / AI error）不影响其它路线。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..database.learning_route_repository import (
    ROUTE_TYPE_LEARNING,
    LearningRouteRepository,
)
from ..utils.date_utils import add_days
from .daily_planner_service import DailyPlannerService
from .study_plan_service import MAX_DAILY_STUDY_MINUTES, StudyPlanService

logger = logging.getLogger(__name__)

# 所有路线共享的 Agent 每日新知识任务上限（task 数量，不是分钟）
GLOBAL_AGENT_DAILY_BUDGET = 3
# 公平调度观察窗口（天）：足够反映近期分配，又能让 priority 修改较快生效
FAIRNESS_WINDOW_DAYS = 7


@dataclass
class RouteAllocation:
    """一次调度中某条路线的分配结果（可解释 / 可测试 / 可日志）。"""

    route_id: int
    route_name: str
    priority: int
    recent_generated_count: int = 0
    service_ratio: float = 0.0
    allocated_slots: int = 0
    skip_reason: str | None = None
    created_task_ids: list[int] = field(default_factory=list)


class GlobalDailyScheduler:
    def __init__(
        self,
        repo,
        plan_repo,
        route_repo: LearningRouteRepository,
        assessment_repo=None,
        skill_service=None,
        jd_service=None,
        planner=None,
        max_daily_minutes: int = MAX_DAILY_STUDY_MINUTES,
        budget: int = GLOBAL_AGENT_DAILY_BUDGET,
        fairness_window: int = FAIRNESS_WINDOW_DAYS,
    ):
        self.repo = repo
        self.plan_repo = plan_repo
        self.route_repo = route_repo
        self.assessment_repo = assessment_repo
        self.skill_service = skill_service
        self.jd_service = jd_service
        self.planner = planner
        self.max_daily_minutes = max_daily_minutes
        self.budget = int(budget)
        self.fairness_window = int(fairness_window)

    # ================= 路线状态 =================

    def _make_route_planner(self, route_id: int) -> DailyPlannerService:
        """为某条 route 构造 route-scoped Planner（绝不读其它路线）。"""
        sps = StudyPlanService(
            self.repo,
            self.plan_repo,
            max_daily_minutes=self.max_daily_minutes,
            assessment_repo=self.assessment_repo,
            skill_service=self.skill_service,
            route_id=route_id,
            learning_route_repo=self.route_repo,
            scope_tasks_by_route=True,
        )
        return DailyPlannerService(
            self.repo,
            self.plan_repo,
            planner=self.planner,
            study_plan_service=sps,
            max_daily_minutes=self.max_daily_minutes,
            assessment_repo=self.assessment_repo,
            skill_service=self.skill_service,
            jd_service=self.jd_service,
            scope_tasks_by_route=True,
        )

    def _done_topic_ids(self) -> set[int]:
        rows = self.repo.conn.execute(
            "SELECT DISTINCT topic_id FROM tasks WHERE status = 'done' "
            "AND topic_id IS NOT NULL"
        ).fetchall()
        return {int(r[0]) for r in rows}

    def route_skip_reason(self, route, plan_date: str) -> str | None:
        """None 表示可参与调度；否则是跳过原因（用于解释/日志）。"""
        if route.route_type != ROUTE_TYPE_LEARNING:
            return "group"
        if route.is_archived:
            return "archived"
        if not route.planning_enabled:
            return "planning_paused"
        plan = self.plan_repo.get_plan_by_route(route.id)
        if plan is None:
            return "no_plan"
        planner = self._make_route_planner(route.id)
        phase = planner.study_plan_service.get_current_phase(plan_date)
        if phase is None:
            topics = self.plan_repo.list_topics_by_route(route.id)
            done = self._done_topic_ids()
            if topics and all(t.id in done for t in topics):
                return "route_complete"
            return "no_available_topic"
        return None

    def plannable_routes(self, plan_date: str) -> list:
        """可参与自动规划的 learning route（active / planning_enabled / 有候选）。"""
        out = []
        for route in self.route_repo.list_learning_routes():
            if self.route_skip_reason(route, plan_date) is None:
                out.append(route)
        return out

    # ================= 主流程 =================

    def _recent_count(self, route_id: int, plan_date: str) -> int:
        start = add_days(plan_date, -(self.fairness_window - 1))
        return self.repo.count_recent_generated_by_route(
            route_id, start, plan_date
        )

    def _last_generated_date(self, route_id: int, plan_date: str) -> str:
        row = self.repo.conn.execute(
            "SELECT MIN(scheduled_date) AS d FROM ("
            "  SELECT scheduled_date FROM tasks WHERE route_id = ? "
            "  AND source = 'generated' AND task_type = 'new' "
            "  AND status != 'cancelled' AND scheduled_date <= ? "
            "  ORDER BY scheduled_date DESC LIMIT 1"
            ")",
            (int(route_id), plan_date),
        ).fetchone()
        return (row[0] if row and row[0] else "0000-00-00")

    def generate(self, plan_date: str, force: bool = False) -> dict:
        """运行全局调度：按公平规则分配 slots 并逐 route 生成任务。

        :param plan_date: 目标日期（今天）
        :param force: 由“重新规划今天”传入；调用方已按既有语义清理 active
            generated（保留 done / manual / cancelled）。
        :return: {"date","budget","existing","remaining","created",
                  "created_ids","allocations","plannable_route_ids"}
        """
        all_routes = self.route_repo.list_learning_routes()
        allocations: dict[int, RouteAllocation] = {}
        skip_map: dict[int, str] = {}
        for route in all_routes:
            reason = self.route_skip_reason(route, plan_date)
            skip_map[route.id] = reason
            allocations[route.id] = RouteAllocation(
                route_id=route.id,
                route_name=route.name,
                priority=int(route.priority or 3),
                skip_reason=reason,
            )

        eligible = [r for r in all_routes if skip_map.get(r.id) is None]
        existing_count = self.repo.count_generated_new_by_date(plan_date)
        existing_minutes = self.repo.sum_generated_new_minutes_by_date(plan_date)
        remaining = max(0, self.budget - existing_count)
        remaining_minutes = max(0, self.max_daily_minutes - existing_minutes)
        if remaining_minutes <= 0:
            # 分钟预算已满：任何候选都放不下（不能为了塞满 task 数突破分钟）
            for route in eligible:
                allocations[route.id].skip_reason = "minute_budget_exhausted"

        recent = {r.id: self._recent_count(r.id, plan_date) for r in eligible}
        allocated = {r.id: 0 for r in eligible}
        exhausted: set[int] = set()
        created_ids: list[int] = []
        allocated_minutes = 0

        while remaining > 0 and remaining_minutes > 0:
            cands = [r for r in eligible if r.id not in exhausted]
            if not cands:
                break

            def _ratio(route) -> float:
                return (recent[route.id] + allocated[route.id]) / max(
                    1, int(route.priority or 1)
                )

            # ratio 最低优先；tie：priority 高 → 更久未生成 → route_id 稳定
            route = min(
                cands,
                key=lambda r: (
                    _ratio(r),
                    -int(r.priority or 1),
                    self._last_generated_date(r.id, plan_date),
                    r.id,
                ),
            )
            planner = self._make_route_planner(route.id)
            try:
                res = planner.generate_for_route(
                    plan_date, max_tasks=1, force=True,
                    max_minutes=remaining_minutes,
                )
            except Exception as e:  # noqa: BLE001 - 单路线失败不拖垮整体
                logger.warning(
                    "route %s planner failed: %s", route.id, e
                )
                exhausted.add(route.id)
                allocations[route.id].skip_reason = "planner_error"
                continue

            ids = list(res.get("created_ids", []))
            if ids:
                spent = sum(
                    (self.repo.get(i).estimated_minutes or 0) for i in ids
                )
                allocated[route.id] += len(ids)
                remaining -= len(ids)
                remaining_minutes -= spent
                allocated_minutes += spent
                created_ids.extend(ids)
                allocations[route.id].created_task_ids.extend(ids)
            else:
                # 该路线已无候选（或候选都超出剩余分钟）：本轮不再尝试
                exhausted.add(route.id)
                if allocations[route.id].skip_reason is None:
                    res_reason = res.get("skip_reason")
                    if res.get("planning_paused"):
                        res_reason = "planning_paused"
                    allocations[route.id].skip_reason = (
                        res_reason or "no_available_topic"
                    )

        for route in eligible:
            alloc = allocations[route.id]
            alloc.recent_generated_count = recent[route.id]
            alloc.allocated_slots = allocated[route.id]
            alloc.service_ratio = (
                (recent[route.id] + allocated[route.id])
                / max(1, int(route.priority or 1))
            )

        result = {
            "date": plan_date,
            "budget": self.budget,
            "existing": existing_count,
            "remaining": remaining,
            "max_minutes": self.max_daily_minutes,
            "existing_minutes": existing_minutes,
            "remaining_minutes": remaining_minutes,
            "allocated_minutes": allocated_minutes,
            "created_ids": created_ids,
            "created": [self.repo.get(i) for i in created_ids],
            "allocations": list(allocations.values()),
            "plannable_route_ids": [r.id for r in eligible],
            "force": force,
        }
        self._log(result)
        return result

    def _log(self, result: dict) -> None:
        lines = [
            f"Daily scheduler {result['date']}",
            f"budget={result['budget']} existing={result['existing']} "
            f"remaining={result['remaining']} | minutes="
            f"{result.get('existing_minutes')}/{result.get('max_minutes')} "
            f"remaining_minutes={result.get('remaining_minutes')}",
        ]
        for alloc in result["allocations"]:
            lines.append(
                f"  {alloc.route_name}: priority={alloc.priority} "
                f"recent={alloc.recent_generated_count} "
                f"allocated={alloc.allocated_slots}"
                + (f" skip={alloc.skip_reason}" if alloc.skip_reason else "")
            )
        logger.info("\n".join(lines))
