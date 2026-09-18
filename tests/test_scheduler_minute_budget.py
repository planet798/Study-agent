"""Phase D.1：Scheduler 全局分钟预算（不改变 fairness）。

覆盖需求：
1. 3 个任务但总分钟 <=180
2. task budget 未满但分钟已满 → 停止
3. 分钟有余但 task budget 满 → 停止
4. existing done generated 消耗分钟
5. cancelled 不消耗分钟
6. manual 不消耗
7. force replan 正确重算
8. 多路线不能各自拿 180min
9. fairness 不回归
10. 单路线兼容
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_scheduler import GlobalDailyScheduler
from app.services.study_plan_service import StudyPlanService
from app.utils.date_utils import add_days

TODAY = "2026-09-15"
MAX_MINUTES = 180


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    sched = GlobalDailyScheduler(repo, plan_repo, route_repo,
                                 assessment_repo=arepo, budget=3,
                                 max_daily_minutes=MAX_MINUTES)
    # 默认路线默认暂停，便于测试自定义路线；需要时手动恢复
    route_service.pause_planning(route_repo.get_default_learning_route().id)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "sps": sps, "sched": sched,
        "default": route_repo.get_default_learning_route(),
    }


def _add_route(env, name, topics, priority=5, planning_enabled=True):
    """topics: list[(title, minutes)]。"""
    rl = env["route_service"].create_learning_route(
        name, priority=priority, planning_enabled=planning_enabled
    )
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    for i, (title, minutes) in enumerate(topics):
        env["route_plan"].add_topic(rl.id, phase.id, title,
                                    estimated_minutes=minutes, order_index=i)
    return rl


def _minutes(env, out):
    return sum((env["repo"].get(i).estimated_minutes or 0)
               for i in out["created_ids"])


def _existing(env, title, minutes, route_id, status="active", source="generated",
              task_type="new"):
    t = env["repo"].create(title, scheduled_date=TODAY, source=source,
                           task_type=task_type, route_id=route_id,
                           estimated_minutes=minutes)
    if status == "done":
        env["repo"].mark_done(t.id)
    elif status == "cancelled":
        env["repo"].cancel(t.id)
    return t


class TestMinuteBudget:
    def test_three_tasks_within_180(self, env):
        _add_route(env, "RL", [("A", 60), ("B", 60), ("C", 60)])
        out = env["sched"].generate(TODAY)
        assert len(out["created_ids"]) == 3
        assert _minutes(env, out) == 180
        assert out["remaining_minutes"] == 0

    def test_minute_budget_full_stops(self, env):
        rl = _add_route(env, "RL", [("A", 60), ("B", 60)])
        _existing(env, "既有 Agent", 150, rl.id, status="active")
        out = env["sched"].generate(TODAY)
        assert out["created_ids"] == []
        assert out["remaining_minutes"] == 30
        alloc = {a.route_id: a for a in out["allocations"]}
        assert alloc[rl.id].skip_reason == "minute_budget_exhausted"

    def test_task_budget_full_stops_even_with_minutes(self, env):
        rl = _add_route(env, "RL", [("A", 10), ("B", 10)])
        for i in range(3):
            _existing(env, f"既有{i}", 10, rl.id)
        out = env["sched"].generate(TODAY)
        assert out["created_ids"] == []
        assert out["remaining"] == 0
        assert out["remaining_minutes"] == MAX_MINUTES - 30

    def test_done_generated_consumes_minutes(self, env):
        done_route = _add_route(env, "Done", [("D", 90)])
        _existing(env, "已完成 Agent", 90, done_route.id, status="done")
        fit = _add_route(env, "Fit", [("F", 60)], priority=5)
        big = _add_route(env, "Big", [("B", 120)], priority=1)
        out = env["sched"].generate(TODAY)
        titles = [env["repo"].get(i).title for i in out["created_ids"]]
        assert "F" in titles and "B" not in titles
        assert _minutes(env, out) + 90 <= MAX_MINUTES
        alloc = {a.route_id: a for a in out["allocations"]}
        assert alloc[big.id].skip_reason == "minute_budget_exhausted"

    def test_cancelled_does_not_consume_minutes(self, env):
        rl = _add_route(env, "RL", [("A", 60)])
        _existing(env, "取消的 Agent", 180, rl.id, status="cancelled")
        out = env["sched"].generate(TODAY)
        assert out["existing_minutes"] == 0
        assert len(out["created_ids"]) == 1

    def test_manual_does_not_consume_minutes(self, env):
        rl = _add_route(env, "RL", [("A", 60)])
        _existing(env, "手动任务", 180, rl.id, source="manual",
                  task_type="manual")
        out = env["sched"].generate(TODAY)
        assert out["existing_minutes"] == 0
        assert len(out["created_ids"]) == 1

    def test_force_replan_recomputes_minutes(self, env):
        rl = _add_route(env, "RL", [("Big", 120)])
        _existing(env, "done Agent", 60, rl.id, status="done")
        active = _existing(env, "active Agent", 120, rl.id)
        # 模拟 force replan：删除 active generated
        env["repo"].delete(active.id)
        out = env["sched"].generate(TODAY, force=True)
        # done 60 仍消耗，剩余 120 -> 可生成 120
        assert len(out["created_ids"]) == 1
        assert _minutes(env, out) == 120
        assert out["existing_minutes"] == 60

    def test_multi_route_cannot_each_take_180(self, env):
        a = _add_route(env, "A", [("A1", 180)], priority=5)
        b = _add_route(env, "B", [("B1", 180)], priority=5)
        out = env["sched"].generate(TODAY)
        assert len(out["created_ids"]) == 1
        assert _minutes(env, out) == 180

    def test_skip_reason_minute_exhausted_single_route(self, env):
        rl = _add_route(env, "RL", [("A", 100)])
        _existing(env, "既有", 100, rl.id)
        out = env["sched"].generate(TODAY)
        assert out["created_ids"] == []
        alloc = {a.route_id: a for a in out["allocations"]}
        assert alloc[rl.id].skip_reason == "minute_budget_exhausted"


class TestFairnessNotRegressed:
    def test_fairness_still_distributes(self, env):
        high = _add_route(env, "High", [("H", 30)] * 40, priority=5)
        low = _add_route(env, "Low", [("L", 30)] * 40, priority=1)
        tally = defaultdict(int)
        date = TODAY
        for _ in range(6):
            out = env["sched"].generate(date)
            for tid in out["created_ids"]:
                tally[env["repo"].get(tid).route_id] += 1
                env["repo"].mark_done(tid)
            date = add_days(date, 1)
        assert tally[low.id] > 0
        assert tally[high.id] >= tally[low.id]

    def test_single_route_compat(self, env):
        env["route_service"].resume_planning(env["default"].id)
        out = env["sched"].generate(TODAY)
        assert out["created_ids"]
        assert len(out["created_ids"]) <= 3
        assert _minutes(env, out) <= MAX_MINUTES
        assert all(env["repo"].get(i).route_id == env["default"].id
                   for i in out["created_ids"])
