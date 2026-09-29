"""Route ownership, canonical carry-over transitions and actual minute budgets."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.schemas import CarryOverTask, DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService, _is_recent_unfinished
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_scheduler import GlobalDailyScheduler
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService

DAY = "2026-09-15"
PREVIOUS = "2026-09-14"


@pytest.fixture()
def env(repo):
    plans = StudyPlanRepository(repo.conn)
    routes = LearningRouteRepository(repo.conn)
    route_service = LearningRouteService(routes)
    route_plan = RoutePlanService(plans, repo, AssessmentRepository(repo.conn))
    a = route_service.create_learning_route("Planner A")
    b = route_service.create_learning_route("Planner B")
    topics = {}
    for route in (a, b):
        route_plan.ensure_manual_plan(route.id, route.name)
        phase = route_plan.add_phase(route.id, "Phase")
        topics[route.id] = [route_plan.add_topic(route.id, phase.id, f"{route.name}-{i}",
                                                estimated_minutes=30, order_index=i)
                            for i in range(3)]
    scheduler = GlobalDailyScheduler(repo, plans, routes, budget=3, max_daily_minutes=60)
    return SimpleNamespace(repo=repo, plans=plans, routes=routes, a=a, b=b,
                           topics=topics, scheduler=scheduler, tasks=TaskService(repo))


def _plan(recs=(), carries=(), declared=30):
    return DailyPlan("reason", tuple(recs), tuple(carries), declared, "adjustment")


def _rec(topic, minutes=30):
    return RecommendedTask(topic_id=topic.id, title=topic.name, estimated_minutes=minutes)


def _carry(task):
    return CarryOverTask(task_id=task.id, reason="unfinished")


def _task(env, *, route=None, date=PREVIOUS, minutes=30, status="active",
          source="generated"):
    task = env.repo.create("carry", scheduled_date=date, source=source,
                           route_id=route, estimated_minutes=minutes)
    if status == "not_done":
        env.tasks.mark_not_done(task.id, "reason")
    elif status == "done":
        env.tasks.complete_task(task.id)
    elif status == "cancelled":
        env.tasks.cancel_task(task.id)
    return env.repo.get(task.id)


def _validate(env, plan, *, route=None, max_minutes=None, max_tasks=None):
    planner = (env.scheduler._make_route_planner(route.id) if route is not None else
               DailyPlannerService(env.repo, env.plans, study_plan_service=StudyPlanService(
                   env.repo, env.plans, route_id=env.a.id,
                   learning_route_repo=env.routes), max_daily_minutes=60))
    return planner._validate_and_create(plan, DAY, max_minutes=max_minutes,
                                        max_tasks=max_tasks)


@pytest.mark.parametrize("foreign_route", ["b", None])
def test_scoped_rejects_foreign_and_null_route_without_writes(env, foreign_route):
    route_id = getattr(env, foreign_route).id if foreign_route else None
    task = _task(env, route=route_id)
    before = env.repo.get(task.id)
    valid, created, problems = _validate(
        env, _plan([_rec(env.topics[env.a.id][0])], [_carry(task)]), route=env.a)
    assert not valid and not created and "不属于当前路线" in problems[0]
    assert env.repo.get(task.id) == before
    assert env.repo.list_by_date(DAY) == []  # valid recommendation also not written


@pytest.mark.parametrize("state", ["active", "not_done"])
def test_same_route_old_carry_uses_canonical_state_rules(env, state):
    task = _task(env, route=env.a.id, status=state)
    valid, ids, problems = _validate(env, _plan(carries=[_carry(task)]), route=env.a)
    assert valid and not problems and ids == [task.id]
    updated = env.repo.get(task.id)
    assert updated.status == "active" and updated.scheduled_date == DAY
    assert updated.postpone_count == task.postpone_count + 1


@pytest.mark.parametrize("state", ["done", "cancelled"])
def test_terminal_carry_rejected_without_other_mutation(env, state):
    task = _task(env, route=env.a.id, status=state)
    before = env.repo.get(task.id)
    valid, ids, problems = _validate(
        env, _plan([_rec(env.topics[env.a.id][0])], [_carry(task)]), route=env.a)
    assert not valid and not ids and problems
    assert env.repo.get(task.id) == before and env.repo.list_by_date(DAY) == []


def test_unknown_state_helper_rejects_and_unscoped_compatibility_remains(env):
    assert not _is_recent_unfinished(SimpleNamespace(status="unknown", scheduled_date=PREVIOUS), DAY)
    foreign = _task(env, route=env.b.id)
    null_route = _task(env, route=None)
    valid, ids, problems = _validate(env, _plan(carries=[_carry(foreign), _carry(null_route)]))
    assert valid and not problems and ids == [foreign.id, null_route.id]
    assert env.repo.get(foreign.id).status == env.repo.get(null_route.id).status == "active"
    assert env.repo.get(foreign.id).scheduled_date == DAY


def test_duplicate_carry_ids_rejected_before_any_mutation(env):
    task = _task(env, route=env.a.id)
    valid, ids, problems = _validate(env, _plan(carries=[_carry(task), _carry(task)]), route=env.a)
    assert not valid and not ids and "重复延期" in problems[0]
    assert env.repo.get(task.id).scheduled_date == PREVIOUS


def test_declared_minutes_cannot_hide_over_budget_recommendations(env):
    topics = env.topics[env.a.id]
    valid, ids, problems = _validate(env, _plan([_rec(topics[0]), _rec(topics[1])], declared=30),
                                     route=env.a, max_minutes=None)
    # Exact 60-minute boundary is allowed even if the model declared 30.
    assert valid and len(ids) == 2 and not problems
    assert sum(env.repo.get(i).estimated_minutes for i in ids) == 60


def test_declared_30_cannot_authorize_two_30_minute_tasks_with_30_capacity(env):
    planner = env.scheduler._make_route_planner(env.a.id)
    planner.max_daily_minutes = 30
    valid, ids, problems = planner._validate_and_create(
        _plan([_rec(env.topics[env.a.id][0]),
               _rec(env.topics[env.a.id][1])], declared=30), DAY)
    assert not valid and not ids and problems
    assert env.repo.list_by_date(DAY) == []


def test_standalone_rejects_actual_total_over_limit_without_partial_writes(env):
    recs = [_rec(topic, 30) for topic in env.topics[env.a.id]]
    valid, ids, problems = _validate(env, _plan(recs, declared=30), route=env.a)
    assert not valid and not ids and "预算" in problems[0]
    assert env.repo.list_by_date(DAY) == []


def test_existing_generated_commitment_limits_standalone_not_manual(env):
    _task(env, route=env.a.id, date=DAY, minutes=50)
    _task(env, route=env.a.id, date=DAY, minutes=100, source="manual")
    valid, ids, problems = _validate(env, _plan([_rec(env.topics[env.a.id][0], 20)]), route=env.a)
    assert not valid and not ids and problems
    assert len(env.repo.list_by_date(DAY)) == 2


@pytest.mark.parametrize("limit,proposed", [(20, 30), (30, 30)])
def test_route_limit_skips_oversize_and_allows_exact_boundary(env, limit, proposed):
    valid, ids, problems = _validate(env, _plan([_rec(env.topics[env.a.id][0], proposed)]),
                                     route=env.a, max_minutes=limit, max_tasks=1)
    assert valid and not problems and len(ids) == (1 if limit == proposed else 0)
    assert sum(env.repo.get(i).estimated_minutes for i in ids) <= limit


def test_route_combined_minutes_and_oversized_first_candidate(env):
    topics = env.topics[env.a.id]
    valid, ids, _ = _validate(env, _plan([_rec(topics[0], 30), _rec(topics[1], 30)]),
                              route=env.a, max_minutes=40, max_tasks=2)
    assert valid and len(ids) == 1 and env.repo.get(ids[0]).estimated_minutes == 30

    # A proposal too large for the slot does not prevent a later fit.
    other = env.scheduler._make_route_planner(env.b.id)
    valid, ids, _ = other._validate_and_create(
        _plan([_rec(env.topics[env.b.id][0], 30), _rec(env.topics[env.b.id][1], 10)]),
        DAY, max_minutes=20, max_tasks=1)
    assert valid and len(ids) == 1 and env.repo.get(ids[0]).estimated_minutes == 10


def test_carry_and_recommendation_compete_for_remaining_capacity(env):
    task = _task(env, route=env.a.id, minutes=20)
    plan = _plan([_rec(env.topics[env.a.id][0], 30)], [_carry(task)])
    valid, ids, _ = _validate(env, plan, route=env.a, max_minutes=40, max_tasks=2)
    assert valid and ids == [task.id]
    assert env.repo.get(task.id).scheduled_date == DAY
    assert sum(env.repo.get(i).estimated_minutes for i in ids) <= 40


def test_previous_date_carry_counts_toward_standalone_budget(env):
    carry = _task(env, route=env.a.id, minutes=40)
    plan = _plan([_rec(env.topics[env.a.id][0], 30)], [_carry(carry)])
    valid, ids, problems = _validate(env, plan, route=env.a)
    assert not valid and not ids and problems
    assert env.repo.get(carry.id).scheduled_date == PREVIOUS
    assert env.repo.list_by_date(DAY) == []


def test_already_scheduled_carry_has_zero_delta_and_no_duplicate_id(env):
    carry = _task(env, route=env.a.id, date=DAY, minutes=50, status="not_done")
    valid, ids, problems = _validate(env, _plan([_rec(env.topics[env.a.id][0], 10)],
                                                [_carry(carry)]), route=env.a)
    assert valid and not problems and len(ids) == 1
    assert env.repo.get(carry.id).status == "active"
    assert env.repo.get(carry.id).postpone_count == carry.postpone_count
    assert env.repo.sum_generated_new_minutes_by_date(DAY) == 60


def test_already_active_on_plan_date_is_not_counted_twice(env):
    carry = _task(env, route=env.a.id, date=DAY, minutes=50)
    valid, ids, problems = _validate(env, _plan(carries=[_carry(carry)]),
                                     route=env.a, max_minutes=10, max_tasks=1)
    assert valid and not ids and not problems
    assert env.repo.get(carry.id) == carry
    assert env.repo.sum_generated_new_minutes_by_date(DAY) == 50


def test_invalid_proposal_still_falls_back_without_mutating_foreign_task(env):
    foreign = _task(env, route=env.b.id)
    class Planner:
        def is_configured(self):
            return True
        def plan_next_day(self, context):
            return _plan([_rec(env.topics[env.a.id][0])], [_carry(foreign)])
    env.scheduler.planner = Planner()
    result = env.scheduler._make_route_planner(env.a.id).generate_for_route(
        DAY, max_tasks=1, max_minutes=60)
    assert result["fallback"] and result["fallback_reason"] == "validation_failed"
    assert env.repo.get(foreign.id).scheduled_date == PREVIOUS


def test_scheduler_never_spends_more_than_passed_slot_capacity(env, monkeypatch):
    class Planner:
        def is_configured(self):
            return True
        def plan_next_day(self, context):
            return _plan([_rec(env.topics[env.a.id][0], 40),
                          _rec(env.topics[env.a.id][1], 20)])
    env.scheduler.planner = Planner()
    original = env.scheduler._make_route_planner
    allocations = []
    def make_planner(route_id):
        planner = original(route_id)
        original_generate = planner.generate_for_route
        def generate(*args, **kwargs):
            result = original_generate(*args, **kwargs)
            allocations.append((kwargs["max_minutes"], result["created_ids"]))
            return result
        planner.generate_for_route = generate
        return planner
    monkeypatch.setattr(env.scheduler, "_make_route_planner", make_planner)
    result = env.scheduler.generate(DAY)
    for capacity, ids in allocations:
        assert sum(env.repo.get(i).estimated_minutes for i in ids) <= capacity
    assert result["remaining_minutes"] >= 0
