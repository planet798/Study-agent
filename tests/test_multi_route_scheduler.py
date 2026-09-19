"""Phase D：多路线全局 Scheduler（budget / fairness / eligibility）。

覆盖需求 46 的 1~23、44~45。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_scheduler import GlobalDailyScheduler
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-09-15"


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
                                 assessment_repo=arepo, budget=3)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "sps": sps, "sched": sched,
        "default": route_repo.get_default_learning_route(),
    }


def _add_route(env, name, topics, priority=5, planning_enabled=True,
               with_plan=True):
    rl = env["route_service"].create_learning_route(
        name, priority=priority, planning_enabled=planning_enabled
    )
    if with_plan:
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
        for i, t in enumerate(topics):
            env["route_plan"].add_topic(rl.id, phase.id, t, order_index=i)
    return rl


def _created_by_route(env, out):
    counts: dict[int, int] = {}
    for tid in out["created_ids"]:
        rid = env["repo"].get(tid).route_id
        counts[rid] = counts.get(rid, 0) + 1
    return counts


# ================= 1~9：eligibility =================

class TestEligibility:
    def test_single_default_route_compat(self, env):
        out = env["sched"].generate(TODAY)
        assert out["budget"] == 3
        assert out["created_ids"]
        assert len(out["created_ids"]) <= 3
        for tid in out["created_ids"]:
            assert env["repo"].get(tid).route_id == env["default"].id

    def test_two_routes_both_planned(self, env):
        rl = _add_route(env, "强化学习", ["MDP", "Policy", "PPO"])
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert rl.id in counts and env["default"].id in counts

    def test_three_routes(self, env):
        a = _add_route(env, "强化学习", ["MDP"])
        b = _add_route(env, "数据结构与算法", ["Hash"])
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert {a.id, b.id, env["default"].id} <= set(counts)

    def test_group_not_plannable(self, env):
        g = env["route_service"].create_group("求职准备X")
        out = env["sched"].generate(TODAY)
        assert g.id not in out["plannable_route_ids"]
        alloc = {a.route_id: a for a in out["allocations"]}
        assert alloc.get(g.id) is None or alloc[g.id].skip_reason is None

    def test_paused_not_plannable(self, env):
        rl = _add_route(env, "强化学习", ["MDP"], planning_enabled=False)
        assert rl.id not in [r.id for r in env["sched"].plannable_routes(TODAY)]
        out = env["sched"].generate(TODAY)
        assert all(env["repo"].get(i).route_id != rl.id for i in out["created_ids"])

    def test_archived_not_plannable(self, env):
        rl = _add_route(env, "强化学习", ["MDP"])
        env["route_service"].archive_route(rl.id)
        assert rl.id not in [r.id for r in env["sched"].plannable_routes(TODAY)]

    def test_no_plan_skipped(self, env):
        rl = _add_route(env, "C++", [], with_plan=False)
        assert env["sched"].route_skip_reason(
            env["route_repo"].get(rl.id), TODAY
        ) == "no_plan"
        out = env["sched"].generate(TODAY)  # 不报错
        assert all(env["repo"].get(i).route_id != rl.id for i in out["created_ids"])

    def test_route_complete_skipped(self, env):
        rl = _add_route(env, "强化学习", ["MDP", "Policy"])
        topics = env["plan_repo"].list_topics_by_route(rl.id)
        for t in topics:
            task = env["repo"].create(
                f"done {t.name}", scheduled_date="2026-09-10",
                topic_id=t.id, route_id=rl.id,
            )
            env["repo"].mark_done(task.id)
        assert env["sched"].route_skip_reason(
            env["route_repo"].get(rl.id), TODAY
        ) == "route_complete"

    def test_empty_route_does_not_break_others(self, env):
        empty = _add_route(env, "C++", ["Placeholder"])
        # 删掉唯一 topic 使其无候选
        topics = env["plan_repo"].list_topics_by_route(empty.id)
        env["plan_repo"].delete_topic(topics[0].id)
        rl = _add_route(env, "强化学习", ["MDP"])
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert rl.id in counts  # RL 仍正常


# ================= 10~15：budget =================

class TestBudget:
    def test_global_budget_not_amplified(self, env):
        for i in range(4):
            _add_route(env, f"R{i}", ["A", "B", "C"], priority=5)
        out = env["sched"].generate(TODAY)
        assert len(out["created_ids"]) == 3

    def test_existing_generated_consumes_budget(self, env):
        env["repo"].create("既有 Agent", scheduled_date=TODAY,
                           source="generated", task_type="new",
                           route_id=env["default"].id)
        _add_route(env, "强化学习", ["MDP", "Policy"])
        out = env["sched"].generate(TODAY)
        assert out["existing"] == 1
        assert len(out["created_ids"]) <= 2

    def test_manual_does_not_consume(self, env):
        env["repo"].create("手动", scheduled_date=TODAY, source="manual",
                           task_type="manual")
        assert env["repo"].count_generated_new_by_date(TODAY) == 0

    def test_review_does_not_consume(self, env):
        env["repo"].create("复习", scheduled_date=TODAY, source="review",
                           task_type="review")
        assert env["repo"].count_generated_new_by_date(TODAY) == 0

    def test_cancelled_does_not_consume(self, env):
        t = env["repo"].create("取消", scheduled_date=TODAY,
                               source="generated", task_type="new",
                               route_id=env["default"].id)
        env["repo"].cancel(t.id)
        assert env["repo"].count_generated_new_by_date(TODAY) == 0


# ================= 16~23：fairness =================

class TestFairness:
    def test_priority_affects_allocation(self, env):
        high = _add_route(env, "高优先", ["A", "B", "C"], priority=5)
        low = _add_route(env, "低优先", ["A", "B", "C"], priority=1)
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert counts.get(high.id, 0) >= counts.get(low.id, 0)

    def test_low_priority_not_starved_over_time(self, env):
        high = _add_route(env, "高", [f"T{i}" for i in range(40)], priority=5)
        mid = _add_route(env, "中", [f"T{i}" for i in range(40)], priority=4)
        low = _add_route(env, "低", [f"T{i}" for i in range(40)], priority=2)
        tally = {high.id: 0, mid.id: 0, low.id: 0, env["default"].id: 0}
        date = TODAY
        for _ in range(10):
            out = env["sched"].generate(date)
            for tid in out["created_ids"]:
                task = env["repo"].get(tid)
                tally[task.route_id] = tally.get(task.route_id, 0) + 1
                env["repo"].mark_done(tid)
            date = _next(date)
        assert tally[low.id] > 0  # 低优先级不被永久饿死
        assert tally[high.id] >= tally[low.id]

    def test_new_route_cold_start(self, env):
        # 已有其它路线大量历史
        a = _add_route(env, "老路线", ["A", "B", "C"], priority=3)
        for i in range(5):
            env["repo"].create(f"历史{i}", scheduled_date=_prev(TODAY, i + 1),
                               source="generated", task_type="new",
                               route_id=a.id)
        new = _add_route(env, "新路线", ["MDP", "PPO"], priority=5)
        out = env["sched"].generate(TODAY)
        assert new.id in _created_by_route(env, out)

    def test_priority_change_immediate(self, env):
        a = _add_route(env, "A", ["A1", "A2", "A3"], priority=1)
        b = _add_route(env, "B", ["B1", "B2", "B3"], priority=1)
        env["route_service"].set_priority(a.id, 5)
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert counts.get(a.id, 0) >= counts.get(b.id, 0)

    def test_one_route_can_get_multiple_slots(self, env):
        env["route_service"].pause_planning(env["default"].id)
        rl = _add_route(env, "强化学习", ["MDP", "Policy", "Value", "Q"], priority=5)
        out = env["sched"].generate(TODAY)
        counts = _created_by_route(env, out)
        assert counts.get(rl.id, 0) >= 2

    def test_same_route_no_duplicate_topic(self, env):
        env["route_service"].pause_planning(env["default"].id)
        rl = _add_route(env, "强化学习", ["MDP", "Policy", "Value", "Q"])
        out = env["sched"].generate(TODAY)
        titles = [env["repo"].get(i).title for i in out["created_ids"]]
        assert len(titles) == len(set(titles))

    def test_same_topic_name_across_routes_ok(self, env):
        env["route_service"].pause_planning(env["default"].id)
        a = _add_route(env, "RL", ["基础"], priority=5)
        b = _add_route(env, "C++", ["基础"], priority=5)
        out = env["sched"].generate(TODAY)
        routes = {env["repo"].get(i).route_id for i in out["created_ids"]}
        assert {a.id, b.id} <= routes


# ================= 44~45：manual / postponed 去重 =================

class TestDuplicateGuards:
    def test_manual_linked_topic_prevents_auto_duplicate(self, env):
        rl = _add_route(env, "强化学习", ["MDP", "Policy"])
        mdp = [t for t in env["plan_repo"].list_topics_by_route(rl.id)
               if t.name == "MDP"][0]
        env["repo"].create("手动 MDP", scheduled_date=TODAY,
                           source="manual", task_type="new",
                           topic_id=mdp.id, route_id=rl.id)
        planner = env["sched"]._make_route_planner(rl.id)
        res = planner.generate_for_route(TODAY, max_tasks=2, force=True)
        assert all(env["repo"].get(i).topic_id != mdp.id
                   for i in res["created_ids"])

    def test_postponed_topic_not_duplicated(self, env):
        rl = _add_route(env, "强化学习", ["MDP", "Policy"])
        mdp = [t for t in env["plan_repo"].list_topics_by_route(rl.id)
               if t.name == "MDP"][0]
        old = env["repo"].create("旧 MDP", scheduled_date="2026-09-10",
                                 source="generated", task_type="new",
                                 topic_id=mdp.id, route_id=rl.id)
        env["repo"].postpone(old.id, TODAY)
        planner = env["sched"]._make_route_planner(rl.id)
        res = planner.generate_for_route(TODAY, max_tasks=2, force=True)
        assert all(env["repo"].get(i).topic_id != mdp.id
                   for i in res["created_ids"])


def _next(date_str: str) -> str:
    from app.utils.date_utils import add_days

    return add_days(date_str, 1)


def _prev(date_str: str, n: int) -> str:
    from app.utils.date_utils import add_days

    return add_days(date_str, -n)
