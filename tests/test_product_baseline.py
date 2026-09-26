"""S6 frozen product boundary: stable structure, not source-string snapshots."""
from __future__ import annotations

import pytest

from importlib.util import find_spec

from PySide6.QtWidgets import QLabel

from app.ai.prompt_registry import PromptRegistry
from app.database.schema import SCHEMA_VERSION
from app.ui.app_shell import PAGE_SPECS, PageKey


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    """Keep UI smoke tests from leaking a Dark system palette to other files."""
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


def test_sidebar_and_today_surface(make_window):
    assert [s.key for s in PAGE_SPECS] == [
        PageKey.TODAY, PageKey.ROUTES, PageKey.PRACTICE, PageKey.SETTINGS
    ]
    assert PAGE_SPECS[-1].footer
    window = make_window()
    assert not hasattr(window, "monthly_page")
    assert not hasattr(window.today_page, "review_card")
    assert not hasattr(window, "_career_panel_added")
    assert not hasattr(window, "jd_summary_service")
    assert window.today_page.pending_card.label() == "待处理"
    assert window.today_page.minutes_card.label() == "预计时长"
    assert window.today_page.add_task_btn.text() == "＋ 添加学习任务"
    assert window.today_page.planner_replan_btn.text() == "重新规划今天"
    texts = {l.text() for l in window.today_page.findChildren(QLabel)}
    assert "今日学习" in texts
    assert not texts.intersection({"今日复习", "职业信号", "技能概览", "月度回顾"})
    window.close()
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


def test_retired_modules_absent_and_active_services_present():
    for name in (
        "app.services.review_service", "app.services.summary_service",
        "app.services.stats_service", "app.ai.summary", "app.ui.summary_pages",
        "app.ui.career_dialogs",
    ):
        assert find_spec(name) is None, name

    from app.services.task_review_service import TaskReviewService
    from app.services.assessment_service import AssessmentService
    from app.services.skill_service import SkillService
    from app.services.jd_summary_service import JdSummaryService
    from app.services.market_signal import MarketSignal
    from app.services.practice_project_service import PracticeProjectService
    from app.services.capability_service import CapabilityService
    from app.ui.ai_worker import AIReviewWorker
    from app.ui.dialogs import AIReviewDialog

    for cls in (TaskReviewService, AssessmentService, SkillService,
                JdSummaryService, MarketSignal, PracticeProjectService,
                CapabilityService, AIReviewWorker, AIReviewDialog):
        assert isinstance(cls, type)


def test_historical_schema_and_verifier_remain(conn):
    from app.diagnostics.release_migration import (
        FINGERPRINT_COLUMNS, FINGERPRINT_VERSION, HISTORY_TABLES,
    )

    assert SCHEMA_VERSION == 20
    assert FINGERPRINT_VERSION == 4
    for table in ("review_schedule", "weekly_summaries", "monthly_summaries"):
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert table in HISTORY_TABLES and table in FINGERPRINT_COLUMNS
    cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_points)")}
    assert {"review_count", "next_review_date", "interval_days"} <= cols
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert {"task_type", "source", "route_id"} <= task_cols


def test_active_prompts_are_not_monthly_prompts():
    keys = set(PromptRegistry().keys())
    assert not any(k.startswith("summary.monthly.") for k in keys)
    for key in (
        "task_review.system", "task_review.user", "planner.system", "planner.user",
        "assessment.generate.system", "assessment.judge.user",
        "route_builder.system", "route_builder.user", "jd_parse.system", "jd_parse.user",
    ):
        assert key in keys


def test_manual_learning_api_and_legacy_alias(conn):
    from app.database.repository import TaskRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.manual_task_service import ManualTaskService

    repo = TaskRepository(conn)
    service = ManualTaskService(repo, assessment_repo=AssessmentRepository(conn))
    assert callable(service.create_learning_activity)
    assert callable(service.create_knowledge_task)
    assert callable(service.create_todo)  # legacy script compatibility, not production UI
    activity = service.create_learning_activity("study", scheduled_date="2026-09-15")
    knowledge = service.create_knowledge_task("Attention", scheduled_date="2026-09-15")
    assert (activity.source, activity.task_type, activity.knowledge_point_id) == (
        "manual", "manual", None
    )
    assert (knowledge.source, knowledge.task_type) == ("manual", "new")
    assert knowledge.knowledge_point_id is not None
