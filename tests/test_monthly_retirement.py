"""S2 regression: Monthly is retired as a product; legacy history survives."""
from __future__ import annotations

import importlib.util

from app.ai.prompt_registry import PromptOverrideRepository, PromptRegistry
from app.database.schema import SCHEMA_VERSION
from app.ui.app_shell import PAGE_SPECS, PageKey


def test_sidebar_and_main_window_have_no_monthly_page(make_window):
    window = make_window()
    assert [spec.key.value for spec in PAGE_SPECS] == [
        "today", "routes", "practice", "settings"
    ]
    assert not hasattr(PageKey, "MONTHLY")
    assert not hasattr(window, "monthly_page")
    assert not hasattr(window, "monthly_page_index")
    assert not hasattr(window, "nav_monthly_btn")
    assert window.nav_today_btn.text() == "今日"
    assert window.nav_routes_btn.text() == "学习路线"
    assert window.nav_practice_btn.text() == "实践项目"
    assert window.nav_ai_btn.text() == "设置"


def test_monthly_production_modules_are_retired():
    from app.main import main
    import inspect
    import re

    source = inspect.getsource(main)
    assert "from app.services.summary_service import SummaryService" not in source
    assert re.search(r"\bSummaryService\s*\(", source) is None
    assert "StatsService(" not in source
    assert "AISummaryGenerator(" not in source
    assert "summary_service" not in inspect.signature(
        __import__("app.ui.main_window", fromlist=["MainWindow"]).MainWindow.__init__
    ).parameters
    for module in (
        "app.services.summary_service",
        "app.services.stats_service",
        "app.ai.summary",
        "app.ui.summary_pages",
    ):
        assert importlib.util.find_spec(module) is None


def test_monthly_prompt_not_active_but_historical_override_survives(conn):
    conn.execute(
        "INSERT INTO prompt_overrides (prompt_key, content, updated_at) "
        "VALUES (?, ?, ?)",
        ("summary.monthly.system", "legacy system override", "history"),
    )
    conn.commit()

    registry = PromptRegistry(PromptOverrideRepository(conn))
    assert "summary.monthly.system" not in registry.keys()
    assert "summary.monthly.user" not in registry.keys()
    assert conn.execute(
        "SELECT content FROM prompt_overrides WHERE prompt_key = ?",
        ("summary.monthly.system",),
    ).fetchone()[0] == "legacy system override"


def test_monthly_history_schema_and_rows_survive_application_startup(
    conn, make_window,
):
    assert SCHEMA_VERSION == 20
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='monthly_summaries'"
    ).fetchone()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_summaries'"
    ).fetchone()
    history = ("2026-01-01", "2026-01-31", '{"legacy":true}', "old", "local", "history")
    for table in ("weekly_summaries", "monthly_summaries"):
        conn.execute(
            f"INSERT INTO {table} "
            "(period_start, period_end, stats_json, ai_summary_json, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", history,
        )
    conn.commit()

    make_window()
    row = conn.execute(
        "SELECT period_start, period_end, stats_json, ai_summary_json, source "
        "FROM monthly_summaries"
    ).fetchone()
    assert tuple(row) == (
        "2026-01-01", "2026-01-31", '{"legacy":true}', "old", "local"
    )
    weekly = conn.execute(
        "SELECT COUNT(*) FROM weekly_summaries"
    ).fetchone()[0]
    assert weekly == 1
    from app.diagnostics.release_migration import (
        FINGERPRINT_COLUMNS, HISTORY_TABLES,
    )
    assert "weekly_summaries" in HISTORY_TABLES
    assert "monthly_summaries" in HISTORY_TABLES
    assert "weekly_summaries" in FINGERPRINT_COLUMNS
    assert "monthly_summaries" in FINGERPRINT_COLUMNS
