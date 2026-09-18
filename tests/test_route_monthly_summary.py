"""Phase E：月总结 route-aware（分组 / 未分类 / cache / AI prompt）。

覆盖需求 36 的 32~36。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.database.study_plan_repository import SummaryCacheRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_progress_service import RouteProgressService
from app.services.stats_service import StatsService
from app.services.summary_service import SummaryService

MONTH = ("2026-09-01", "2026-09-30")
DONE_DATE = "2026-09-10"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    progress = RouteProgressService(repo, arepo, plan_repo, route_repo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "progress": progress,
    }


def _service(env, ai=None):
    return SummaryService(
        stats_service=StatsService(env["repo"]),
        cache_repo=SummaryCacheRepository(env["conn"]),
        ai_generator=ai,
        route_progress_service=env["progress"],
    )


def _route(env, name, topics=("MDP",)):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    ts = [env["route_plan"].add_topic(rl.id, phase.id, t) for t in topics]
    return rl, ts


class FakeAI:
    def __init__(self):
        self.seen = None
        self.calls = 0

    def is_configured(self):
        return True

    def generate_monthly(self, stats):
        from app.ai.schemas import MonthlySummary

        self.calls += 1
        self.seen = stats
        return MonthlySummary(
            overview="o", progress="p", strengths=("s",), weaknesses=("w",),
            recommendations=("r",), next_month_focus=("n",),
        )


# ================= 32~33：分组 / 未分类 =================

class TestRouteGrouping:
    def test_route_stats_grouped(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy"])
        t = env["repo"].create("learn MDP", scheduled_date=DONE_DATE,
                               topic_id=ts[0].id, route_id=rl.id)
        env["repo"].mark_done(t.id)
        stats = _service(env).get_monthly_summary(2026, 9)["stats"]
        route_stats = stats["route_stats"]
        rl_stat = next(r for r in route_stats if r["route_id"] == rl.id)
        assert rl_stat["done_tasks"] == 1
        assert rl_stat["covered_topics"] == 1

    def test_unclassified_bucket(self, env):
        t = env["repo"].create("manual", scheduled_date=DONE_DATE,
                               source="manual", task_type="manual")
        env["repo"].mark_done(t.id)
        stats = _service(env).get_monthly_summary(2026, 9)["stats"]
        unclassified = [r for r in stats["route_stats"]
                        if r["route_id"] is None]
        assert unclassified and unclassified[0]["route_name"] == "未分类"
        assert unclassified[0]["done_tasks"] == 1

    def test_empty_buckets_skipped(self, env):
        stats = _service(env).get_monthly_summary(2026, 9)["stats"]
        assert stats["route_stats"] == []


# ================= 34：weekly 不恢复 =================

class TestNoWeekly:
    def test_no_weekly_summary(self):
        assert not hasattr(SummaryService, "get_weekly_summary")


# ================= 35：AI 只收到真实 route stats =================

class TestAiPrompt:
    def test_ai_receives_route_stats(self, env):
        rl, ts = _route(env, "RL", ["MDP"])
        t = env["repo"].create("learn", scheduled_date=DONE_DATE,
                               topic_id=ts[0].id, route_id=rl.id)
        env["repo"].mark_done(t.id)
        ai = FakeAI()
        svc = _service(env, ai=ai)
        svc.get_monthly_summary(2026, 9)
        assert ai.seen is not None
        assert "route_stats" in ai.seen
        names = [r["route_name"] for r in ai.seen["route_stats"]]
        assert "RL" in names
        # AI 只拿到结构化 route stats，不包含其它路线
        assert all(isinstance(r, dict) for r in ai.seen["route_stats"])

    def test_ai_cache_reused_without_changes(self, env):
        _route(env, "RL", ["MDP"])
        ai = FakeAI()
        svc = _service(env, ai=ai)
        svc.get_monthly_summary(2026, 9)
        svc.get_monthly_summary(2026, 9)
        assert ai.calls == 1  # 统计未变 → 命中缓存


# ================= 36：cache fingerprint route-aware =================

class TestCacheFingerprint:
    def test_new_task_invalidates_cache(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy"])
        svc = _service(env)
        first = svc.get_monthly_summary(2026, 9)
        assert first["cached"] is False
        second = svc.get_monthly_summary(2026, 9)
        assert second["cached"] is True
        # 新增学习记录 → route 统计变化 → 缓存失效
        t = env["repo"].create("new", scheduled_date="2026-09-12",
                               topic_id=ts[0].id, route_id=rl.id)
        env["repo"].mark_done(t.id)
        third = svc.get_monthly_summary(2026, 9)
        assert third["cached"] is False
        rl_stat = next(r for r in third["stats"]["route_stats"]
                       if r["route_id"] == rl.id)
        assert rl_stat["done_tasks"] == 1

    def test_cache_survives_without_data_change(self, env):
        _route(env, "RL", ["MDP"])
        svc = _service(env)
        svc.get_monthly_summary(2026, 9)
        assert svc.get_monthly_summary(2026, 9)["cached"] is True
