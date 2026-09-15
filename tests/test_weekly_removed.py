"""周总结已完整移除的回归测试。"""

from __future__ import annotations

import app.ai.schemas as schemas
import app.ai.summary as ai_summary
import app.services.summary_service as summary_service
from app.database.study_plan_repository import SummaryCacheRepository
from app.services.stats_service import StatsService
from app.services.summary_service import SummaryService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow
from app.ui.summary_pages import MonthlySummaryPage

import app.ui.summary_pages as summary_pages

TODAY = "2026-01-05"


def test_no_weekly_nav_or_page(qtbot, repo, task_service, date_service):
    svc = SummaryService(StatsService(repo), SummaryCacheRepository(repo.conn))
    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        today_provider=lambda: TODAY,
        summary_service=svc,
    )
    qtbot.addWidget(w)
    assert not hasattr(w, "nav_weekly_btn")
    assert not hasattr(w, "weekly_page")
    assert w.nav_today_btn.text() == "今日"
    assert w.nav_monthly_btn.text() == "月总结"
    assert w.nav_monthly_btn.isEnabled() is True
    # 今日页正常
    assert w.stack.currentIndex() == 0


def test_no_weekly_page_class():
    assert not hasattr(summary_pages, "WeeklySummaryPage")
    assert MonthlySummaryPage is not None


def test_no_weekly_entrypoints():
    assert not hasattr(SummaryService, "get_weekly_summary")
    assert not hasattr(ai_summary.AISummaryGenerator, "generate_weekly")
    assert not hasattr(schemas, "WeeklySummary")
    assert not hasattr(schemas, "parse_weekly_summary")
    assert not hasattr(schemas, "parse_weekly_from_json")
    import app.ai.prompts as prompts

    assert not hasattr(prompts, "build_weekly_summary_prompt")


def test_monthly_service_still_works(repo):
    ts = TaskService(repo)
    t = ts.create_task("任务", scheduled_date="2026-01-05")
    ts.complete_task(t.id)
    svc = SummaryService(StatsService(repo), SummaryCacheRepository(repo.conn))
    result = svc.get_monthly_summary(2026, 1)
    assert result["stats"]["total_tasks"] == 1
    assert result["stats"]["completed_tasks"] == 1


def test_stats_generic_capability_kept(repo):
    ts = TaskService(repo)
    t = ts.create_task("任务", scheduled_date="2026-01-05", estimated_minutes=30)
    ts.complete_task(t.id)
    stats = StatsService(repo).get_weekly_stats("2026-01-05", "2026-01-11")
    assert stats["total_tasks"] == 1
    assert stats["completed_tasks"] == 1
