"""DailyPlannerService 测试：编排、校验、fallback、幂等。"""

from __future__ import annotations

import pytest

from app.ai.interface import AIServiceError
from app.ai.planner import AIPlanner
from app.ai.schemas import CarryOverTask, DailyPlan, RecommendedTask
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.study_plan_service import MAX_DAILY_STUDY_MINUTES, StudyPlanService
from app.services.task_service import TaskService


@pytest.fixture()
def plan_repo(repo):
    return StudyPlanRepository(repo.conn)


@pytest.fixture()
def sps(repo, plan_repo):
    svc = StudyPlanService(repo, plan_repo)
    svc.ensure_default_plan()
    return svc


class FakePlanner:
    """可配置的假 AIPlanner。"""

    def __init__(self, plan=None, error=None, configured=True):
        self._plan = plan
        self._error = error
        self._configured = configured
        self.inputs = []

    def is_configured(self):
        return self._configured

    def plan_next_day(self, context):
        self.inputs.append(context)
        if self._error is not None:
            raise self._error
        if self._plan is None:
            self._plan = _always_plan()
        return self._plan


def _rec(topic_id, title=None, minutes=45, priority=3):
    return RecommendedTask(
        topic_id=topic_id,
        title=title or f"主题{topic_id}",
        description="",
        estimated_minutes=minutes,
        priority=priority,
    )


def _always_plan(recommended=None, carry=(), minutes=90, reasoning="x",
                 adjustment="y", overrides=None):
    recs = recommended if recommended is not None else [_rec(1)]
    return DailyPlan(
        reasoning=reasoning,
        recommended_tasks=tuple(recs),
        carry_over_tasks=tuple(carry),
        daily_minutes=minutes,
        adjustment=adjustment,
    )


def _make_service(repo, plan_repo, sps, fake: FakePlanner):
    return DailyPlannerService(
        repo, plan_repo, planner=fake, study_plan_service=sps,
        max_daily_minutes=MAX_DAILY_STUDY_MINUTES,
    )


def _phase1_topic_ids(sps) -> list[int]:
    plan = sps.get_active_plan_full()
    return [t.id for t in plan.phases[0].topics]


class TestContextConstruction:
    def test_recent_7_days_correctly_built(self, repo, plan_repo, sps):
        svc = _make_service(repo, plan_repo, sps, FakePlanner())
        # 制造最近几天的任务
        ts = TaskService(repo)
        for day in ("2026-09-01", "2026-09-02"):
            t = ts.create_task("历史任务", scheduled_date=day, estimated_minutes=30)
            ts.complete_task(t.id)
        ctx = svc.build_context("2026-09-04")
        assert len(ctx.recent_7_days) == 7
        # anchor=2026-09-05，往前 7 天是 08-30~09-05？不含 anchor
        dates = [d.date for d in ctx.recent_7_days]
        assert "2026-09-01" in dates
        assert "2026-09-05" not in dates

    def test_context_has_phase_and_topics(self, repo, plan_repo, sps):
        svc = _make_service(repo, plan_repo, sps, FakePlanner())
        ctx = svc.build_context("2026-09-04")
        assert ctx.current_phase == "Python + Linux + Git"
        assert len(ctx.available_topics) >= 1
        assert ctx.current_daily_limit == MAX_DAILY_STUDY_MINUTES

    def test_postponed_tasks_in_context(self, repo, plan_repo, sps):
        ts = TaskService(repo)
        t = ts.create_task("延期任务", scheduled_date="2026-09-03")
        ts.mark_not_done(t.id, "原因")
        ts.postpone_task(t.id)  # -> 09-04
        svc = _make_service(repo, plan_repo, sps, FakePlanner())
        ctx = svc.build_context("2026-09-04")
        postponed_ids = {p.task_id for p in ctx.postponed_tasks}
        assert t.id in postponed_ids


class TestGenerateNormal:
    def test_normal_flow_creates_tasks(self, repo, plan_repo, sps, task_service):
        topic_ids = _phase1_topic_ids(sps)
        fake = FakePlanner(plan=_always_plan(recommended=[_rec(topic_ids[0])]))
        svc = _make_service(repo, plan_repo, sps, fake)

        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is False
        assert result["date"] == "2026-09-05"
        assert len(result["created"]) >= 1

        # 已创建真实任务
        created = result["created"][0]
        task = repo.get(created)
        assert task.source == "generated"
        assert task.scheduled_date == "2026-09-05"
        assert task.topic_id == topic_ids[0]

    def test_decision_saved(self, repo, plan_repo, sps):
        svc = _make_service(repo, plan_repo, sps, FakePlanner(_always_plan()))
        svc.generate_next_day_plan("2026-09-04")
        decision = svc.latest_plan_for_date("2026-09-05")
        assert decision is not None
        assert decision["source"] == "ai"
        assert "recommended_tasks" in decision["ai_response"]

    def test_called_twice_idempotent(self, repo, plan_repo, sps, task_service):
        topic_ids = _phase1_topic_ids(sps)
        fake = FakePlanner(plan=_always_plan(recommended=[_rec(topic_ids[0])]))
        svc = _make_service(repo, plan_repo, sps, fake)

        r1 = svc.generate_next_day_plan("2026-09-04")
        n1 = len(task_service.get_tasks_by_date("2026-09-05"))
        r2 = svc.generate_next_day_plan("2026-09-04")
        n2 = len(task_service.get_tasks_by_date("2026-09-05"))
        assert r2["existing"] is True
        assert n1 == n2


class TestValidation:
    def test_topic_not_in_current_phase_rejected(self, repo, plan_repo, sps):
        # 用 Phase2 的 topic 冒充（Phase2 是 10-16 开始，不在 09-05）
        plan = sps.get_active_plan_full()
        foreign_topic = plan.phases[1].topics[0].id
        fake = FakePlanner(plan=_always_plan(recommended=[_rec(foreign_topic)]))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True
        assert result["fallback_reason"] == "validation_failed"

    def test_task_id_not_exist_rejected(self, repo, plan_repo, sps):
        fake = FakePlanner(
            plan=_always_plan(carry=[CarryOverTask(task_id=99999, reason="x")])
        )
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True

    def test_daily_minutes_over_limit_rejected(self, repo, plan_repo, sps):
        fake = FakePlanner(plan=_always_plan(minutes=400))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True

    def test_done_topic_not_regenerated(self, repo, plan_repo, sps, task_service):
        topic_ids = _phase1_topic_ids(sps)
        # 先手工完成该 topic 的一个任务
        t = task_service.create_task("已完成主题", scheduled_date="2026-09-03",
                                     topic_id=topic_ids[0], source="generated")
        task_service.complete_task(t.id)

        fake = FakePlanner(plan=_always_plan(recommended=[_rec(topic_ids[0])]))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        # 完成主题被拒 => 整份回退；因为唯一的推荐被拒，回到规则生成
        assert result["fallback"] is True

    def test_carry_over_done_task_rejected(self, repo, plan_repo, sps, task_service):
        t = task_service.create_task("已完成", scheduled_date="2026-09-03")
        task_service.complete_task(t.id)
        fake = FakePlanner(
            plan=_always_plan(carry=[CarryOverTask(task_id=t.id, reason="x")])
        )
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True

    def test_no_duplicate_with_postponed(self, repo, plan_repo, sps, task_service):
        """已有延期任务排到目标日时，不重复生成同主题。"""
        topic_ids = _phase1_topic_ids(sps)
        # 制造一个 topic 已排到 09-05 的延期任务
        t = task_service.create_task("已延期", scheduled_date="2026-09-04",
                                     topic_id=topic_ids[0], source="generated")
        task_service.mark_not_done(t.id, "没完成")
        task_service.postpone_task(t.id)  # -> 09-05

        fake = FakePlanner(plan=_always_plan(recommended=[_rec(topic_ids[0])]))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        # 目标日已有同主题任务，AI 推荐被去重 => 生成 0 个？回退规则也会去重
        today = task_service.get_tasks_by_date("2026-09-05")
        counts = {}
        for task in today:
            counts[task.topic_id] = counts.get(task.topic_id, 0) + 1
        assert counts.get(topic_ids[0], 0) <= 1


class TestFallback:
    def test_ai_failure_falls_back_to_rules(self, repo, plan_repo, sps, task_service):
        fake = FakePlanner(error=AIServiceError("AI 请求超时"))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True
        assert result["fallback_reason"] == "ai_error"
        # 仍然生成了今天的任务（规则型）
        assert len(task_service.get_tasks_by_date("2026-09-05")) >= 1
        # 记录了 fallback 决策
        decision = svc.latest_plan_for_date("2026-09-05")
        assert decision["source"] == "fallback_rule"

    def test_not_configured_falls_back(self, repo, plan_repo, sps, task_service):
        fake = FakePlanner(configured=False)
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True
        assert result["fallback_reason"] == "ai_not_configured"
        assert len(task_service.get_tasks_by_date("2026-09-05")) >= 1

    def test_no_planner_falls_back(self, repo, plan_repo, sps, task_service):
        svc = DailyPlannerService(repo, plan_repo, planner=None,
                                  study_plan_service=sps)
        result = svc.generate_next_day_plan("2026-09-04")
        assert result["fallback"] is True
        assert len(task_service.get_tasks_by_date("2026-09-05")) >= 1


class TestBudgetAndPriority:
    def test_never_exceeds_limit(self, repo, plan_repo, sps):
        # 让 AI 推荐 Phase1 所有 topic（每个 <=60 分钟），daily_minutes=180
        plan = sps.get_active_plan_full()
        recs = [_rec(t.id, minutes=t.estimated_minutes, priority=t.priority)
                for t in plan.phases[0].topics]
        fake = FakePlanner(plan=_always_plan(recommended=recs,
                                             minutes=MAX_DAILY_STUDY_MINUTES))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        tasks = [repo.get(i) for i in result["created"]]
        total = sum(t.estimated_minutes for t in tasks if t)
        assert total <= MAX_DAILY_STUDY_MINUTES

    def test_only_current_phase_topics_used(self, repo, plan_repo, sps):
        plan = sps.get_active_plan_full()
        phase1_ids = {t.id for t in plan.phases[0].topics}
        all_recommended = [
            _rec(t.id) for ph in plan.phases[:2] for t in ph.topics  # 混入 Phase2
        ]
        fake = FakePlanner(plan=_always_plan(recommended=all_recommended,
                                             minutes=180))
        svc = _make_service(repo, plan_repo, sps, fake)
        result = svc.generate_next_day_plan("2026-09-04")
        # 外阶段 topic 导致校验失败 -> fallback（不从外部 topic 建任务）
        assert result["fallback"] is True
        for task_id in result.get("created", []):
            t = repo.get(task_id)
            assert t.topic_id in phase1_ids
