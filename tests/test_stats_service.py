"""StatsService 测试：周/月/趋势/习惯指标。"""

from __future__ import annotations

import pytest

from app.services.stats_service import StatsService
from app.services.task_service import TaskService


@pytest.fixture()
def stats(task_service):
    return StatsService(task_service.repo)


class TestWeeklyStats:
    def test_weekly_basic(self, repo):
        ts = TaskService(repo)
        # 周一~周三各任务
        ts.create_task("A1", scheduled_date="2026-01-05", estimated_minutes=30)
        a2 = ts.create_task("A2", scheduled_date="2026-01-05", estimated_minutes=45)
        ts.create_task("B1", scheduled_date="2026-01-06", estimated_minutes=60)
        ts.complete_task(a2.id)

        ss = StatsService(repo)
        w = ss.get_weekly_stats("2026-01-05", "2026-01-11")
        assert w["total_tasks"] == 3
        assert w["completed_tasks"] == 1
        assert w["completion_rate"] == pytest.approx(33.3, abs=0.1)
        assert w["completed_minutes"] == 45
        assert w["estimated_minutes"] == 135
        assert w["study_days"] == 1  # 只完成了 1 天

    def test_weekly_ignores_outside_period(self, repo):
        ts = TaskService(repo)
        ts.create_task("外面", scheduled_date="2026-01-12")
        ss = StatsService(repo)
        w = ss.get_weekly_stats("2026-01-05", "2026-01-11")
        assert w["total_tasks"] == 0
        assert w["study_days"] == 0

    def test_category_stats(self, repo):
        ts = TaskService(repo)
        t1 = ts.create_task("py", scheduled_date="2026-01-05", category="Python")
        ts.create_task("sql", scheduled_date="2026-01-05", category="算法")
        ts.complete_task(t1.id)
        ss = StatsService(repo)
        w = ss.get_weekly_stats("2026-01-05", "2026-01-11")
        cat = {c["category"]: c for c in w["category_stats"]}
        assert "Python" in cat and "算法" in cat
        assert cat["Python"]["completed"] == 1
        assert cat["Python"]["completion_rate"] == pytest.approx(100.0)

    def test_topic_stats(self, repo, plan_repo):
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        plan = sps.get_active_plan_full()
        topic = plan.phases[0].topics[0]

        ts = TaskService(repo)
        t1 = ts.create_task(topic.name, scheduled_date="2026-01-05",
                            topic_id=topic.id, estimated_minutes=30)
        ts.complete_task(t1.id)
        ts.create_task("重复", scheduled_date="2026-01-06", topic_id=topic.id)

        ss = StatsService(repo)
        w = ss.get_weekly_stats("2026-01-05", "2026-01-11")
        assert len(w["topic_stats"]) == 1
        tstats = w["topic_stats"][0]
        assert tstats["topic_id"] == topic.id
        assert tstats["topic_name"] == topic.name
        assert tstats["total"] == 2
        assert tstats["completed"] == 1

    def test_postponed_count(self, repo):
        ts = TaskService(repo)
        t1 = ts.create_task("延期", scheduled_date="2026-01-05")
        ts.mark_not_done(t1.id, "没完成")
        ts.postpone_task(t1.id)
        ss = StatsService(repo)
        w = ss.get_weekly_stats("2026-01-05", "2026-01-11")
        assert w["postponed_tasks"] == 1


class TestMonthlyStats:
    def test_monthly_basic(self, repo):
        ts = TaskService(repo)
        t1 = ts.create_task("a", scheduled_date="2026-02-01", estimated_minutes=30)
        ts.create_task("b", scheduled_date="2026-02-15", estimated_minutes=60)
        ts.complete_task(t1.id)

        ss = StatsService(repo)
        m = ss.get_monthly_stats(2026, 2)
        assert m["total_tasks"] == 2
        assert m["completed_tasks"] == 1
        assert m["study_days"] == 1
        assert m["completed_minutes"] == 30

    def test_monthly_range_excludes_other_months(self, repo):
        ts = TaskService(repo)
        ts.create_task("一月的", scheduled_date="2026-01-31")
        ts.create_task("三月的", scheduled_date="2026-03-01")
        ss = StatsService(repo)
        m = ss.get_monthly_stats(2026, 2)
        assert m["total_tasks"] == 0

    def test_monthly_most_postponed_topic(self, repo, plan_repo):
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        plan = sps.get_active_plan_full()
        topic = plan.phases[0].topics[0]

        ts = TaskService(repo)
        t = ts.create_task("反复", scheduled_date="2026-02-01", topic_id=topic.id)
        for _ in range(3):
            ts.mark_not_done(t.id, "原因")
            ts.postpone_task(t.id)
        ss = StatsService(repo)
        m = ss.get_monthly_stats(2026, 2)
        assert m["most_postponed_topic"] is not None
        assert m["most_postponed_topic"]["count"] >= 3
        assert m["most_postponed_topic"]["topic_name"] == topic.name


class TestTrend:
    def test_trend_points_and_avgs(self, repo):
        ts = TaskService(repo)
        # 造 3 天数据（锚点 2026-01-06 往前）
        for day in ("2026-01-04", "2026-01-05", "2026-01-06"):
            t = ts.create_task("每日", scheduled_date=day, estimated_minutes=30)
            ts.complete_task(t.id)
        ss = StatsService(repo)
        trend = ss.get_learning_trend(days=7, end_date="2026-01-06")
        assert len(trend["points"]) == 7
        assert trend["points"][-1]["date"] == "2026-01-06"
        # 完成率平均（3 天完成 / 7 天窗口）
        assert trend["avg_completion_7"] == pytest.approx(3 / 7, abs=0.01)
        assert trend["avg_study_minutes_7"] == pytest.approx(90 / 7, abs=0.1)
        assert trend["completion_trend"] in ("上升", "下降", "持平")

    def test_trend_series_length(self, repo):
        ss = StatsService(repo)
        trend = ss.get_learning_trend(days=30, end_date="2026-03-30")
        assert len(trend["completion_series"]) == 30
        assert len(trend["study_minutes_series"]) == 30
        assert len(trend["postpone_series"]) == 30


class TestHabitStats:
    def test_habit_basic(self, repo):
        ts = TaskService(repo)
        t1 = ts.create_task("a", scheduled_date="2026-01-03", category="Python")
        ts.complete_task(t1.id)
        t2 = ts.create_task("b", scheduled_date="2026-01-04", category="Python")
        ts.complete_task(t2.id)
        ss = StatsService(repo)
        h = ss.get_habit_stats(end_date="2026-01-05")
        assert h["avg_completion_rate"] == pytest.approx(100.0)
        assert h["max_streak_days"] == 2
        assert h["current_streak_days"] == 0  # 01-05 没完成

    def test_habit_empty(self, repo):
        ss = StatsService(repo)
        h = ss.get_habit_stats()
        assert h["max_streak_days"] == 0
        assert h["avg_daily_tasks"] == 0.0

    def test_habit_most_postponed_category(self, repo):
        ts = TaskService(repo)
        t = ts.create_task("a", scheduled_date="2026-01-03", category="英语")
        for _ in range(3):
            ts.mark_not_done(t.id, "原因")
            ts.postpone_task(t.id)
        ss = StatsService(repo)
        h = ss.get_habit_stats(end_date="2026-01-06")
        assert h["most_postponed_category"] == "英语"
        assert h["avg_task_postpone_count"] == pytest.approx(3.0)
