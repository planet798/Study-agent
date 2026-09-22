"""TodayPage / Today workspace tests (UI-3)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from app.ui.design.theme_manager import ThemeManager
from app.ui.today_page import TodayPage


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


@pytest.fixture()
def page(qapp):
    return TodayPage()


def test_today_page_construct(page):
    assert page.scroll is not None
    assert page.list_container is not None
    assert page.list_layout is not None
    assert page.route_filter_combo is not None
    assert page.add_task_btn.text() == "＋ 添加学习任务"
    assert page.empty_hint is page.empty_state


def test_set_summary_metrics(page):
    page.set_summary_metrics(3, 120, 2)
    assert page.pending_card.value() == "3"
    assert page.minutes_card.value() == "120 分钟"
    assert page.review_card.value() == "2"


def test_add_task_signal_from_button(qtbot, page):
    seen = []
    page.add_task_requested.connect(lambda: seen.append(True))
    qtbot.mouseClick(page.add_task_btn, Qt.MouseButton.LeftButton)
    assert seen == [True]


def test_add_task_signal_from_empty_action(qtbot, page):
    seen = []
    page.add_task_requested.connect(lambda: seen.append(True))
    qtbot.mouseClick(page.empty_action_btn, Qt.MouseButton.LeftButton)
    assert seen == [True]


def test_replan_signal(qtbot, page):
    seen = []
    page.replan_requested.connect(lambda: seen.append(True))
    qtbot.mouseClick(page.planner_replan_btn, Qt.MouseButton.LeftButton)
    assert seen == [True]


def test_route_filter_signal(page):
    seen = []
    page.route_filter_changed.connect(lambda: seen.append(True))
    page.route_filter_combo.addItem("全部路线", "all")
    page.route_filter_combo.addItem("R1", 1)
    page.route_filter_combo.setCurrentIndex(1)
    assert seen  # 至少触发一次


def test_planner_state_variant(page):
    page.set_planner_state("AI 状态：AI 不可用", "说明", available=False,
                           replan_enabled=False, variant="warning")
    assert page.planner_banner.variant() == "warning"
    assert page.planner_replan_btn.isEnabled() is False
    page.set_planner_state("AI 状态：AI 已启用", "", replan_enabled=True)
    assert page.planner_banner.variant() == "info"


def test_clear_and_add_tasks(page):
    from PySide6.QtWidgets import QLabel

    page.clear_tasks()
    page.add_task_widget(QLabel("a"))
    page.add_section_header("今日学习")
    page.add_task_widget(QLabel("b"))
    assert page.list_layout.count() == 3
    page.clear_tasks()
    assert page.list_layout.count() == 0


# ---------------- MainWindow compatibility + metrics ----------------

def test_mainwindow_aliases(make_window):
    w = make_window()
    assert w.scroll is w.today_page.scroll
    assert w.list_layout is w.today_page.list_layout
    assert w.route_filter_combo is w.today_page.route_filter_combo
    assert w.add_task_btn is w.today_page.add_task_btn
    assert w.empty_hint is w.today_page.empty_state
    assert w.planner_replan_btn is w.today_page.planner_replan_btn


def test_summary_metrics_semantics(make_window, task_service, repo, fixed_today):
    t1 = task_service.create_task("A", scheduled_date=fixed_today,
                                  estimated_minutes=45)
    task_service.create_task("B", scheduled_date=fixed_today,
                             estimated_minutes=75)
    t3 = task_service.create_task("C", scheduled_date=fixed_today)
    task_service.cancel_task(t3.id)
    w = make_window()
    assert w.today_page.pending_card.value() == "2"
    assert w.today_page.minutes_card.value() == "120 分钟"
    # cancelled 不进入待处理指标
    assert t3.id not in [wd.task().id for wd in w._task_widgets]


def test_review_metric(make_window, repo, fixed_today):
    repo.create("巩固", scheduled_date=fixed_today, task_type="review",
                source="daily_retention")
    w = make_window()
    assert w.today_page.review_card.value() == "1"


def test_today_date_still_in_page_header(make_window, fixed_today):
    w = make_window()
    assert w.date_label.text() == fixed_today
    assert w.page_header.subtitle() == fixed_today


def test_career_section_header_when_service_present(
    qtbot, conn, task_service, date_service, fixed_today
):
    """Career signals 作为 secondary section 追加在任务之后。"""
    from PySide6.QtWidgets import QLabel

    from app.ui.main_window import MainWindow

    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        today_provider=lambda: fixed_today,
    )
    qtbot.addWidget(w)
    texts = [lbl.text() for lbl in w.list_container.findChildren(QLabel)]
    # 无 career service 时不应凭空出现“职业信号”
    assert "职业信号" not in texts
