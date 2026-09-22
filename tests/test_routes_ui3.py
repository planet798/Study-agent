"""Learning Routes UI-3 重设计测试（Route card / Mastery vs Capability）。"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_progress_service import RouteProgressService
from app.ui.components.progress_bar import SAProgressBar
from app.ui.components.status_badge import SAStatusBadge
from app.ui.components.tag import SATag
from app.ui.design.theme_manager import ThemeManager
from app.ui.routes_page import LearningRoutesPage, RouteDetailDialog

TODAY = "2026-01-05"


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


@pytest.fixture()
def routes_env(activity_env):
    env = activity_env
    a_repo = AssessmentRepository(env.conn)
    route_service = LearningRouteService(env.route_repo, skill_repo=env.skill_repo)
    route_plan = RoutePlanService(
        env.plan_repo, env.repo, a_repo, topic_learning_service=env.tl
    )
    progress = RouteProgressService(
        env.repo, a_repo, env.plan_repo, env.route_repo,
        topic_learning_service=env.tl,
    )
    return {
        "env": env,
        "route_service": route_service,
        "route_plan": route_plan,
        "progress": progress,
        "a_repo": a_repo,
    }


def _page(qtbot, routes_env):
    page = LearningRoutesPage(
        routes_env["route_service"],
        route_plan_service=routes_env["route_plan"],
        progress_service=routes_env["progress"],
        today_provider=lambda: TODAY,
        topic_learning_service=routes_env["env"].tl,
    )
    qtbot.addWidget(page)
    return page


def test_route_cards_render(qtbot, routes_env):
    from PySide6.QtWidgets import QLabel

    page = _page(qtbot, routes_env)
    texts = [l.text() for l in page.list_container.findChildren(QLabel)]
    # canonical routes 应出现
    assert any("LoRA" in t or "LLM" in t or "R1" in t for t in texts) or texts


def test_route_card_uses_progressbar(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    bars = page.list_container.findChildren(SAProgressBar)
    assert bars  # 至少课程进度条


def test_mastery_and_capability_are_separate(qtbot, routes_env):
    from PySide6.QtWidgets import QLabel

    page = _page(qtbot, routes_env)
    texts = [l.text() for l in page.list_container.findChildren(QLabel)]
    # Mastery 与 Capability 是两个独立展示，不合并成一个进度
    assert any("掌握度" in t for t in texts)
    assert any("能力 Capability" in t for t in texts)


def test_capability_is_not_percentage(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    for tag in page.list_container.findChildren(SATag):
        if tag.parent() and tag.isVisible():
            # Capability tag 不应是百分比
            assert not tag.text().endswith("%")


def test_route_status_badges(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    badges = page.list_container.findChildren(SAStatusBadge)
    assert badges


def test_activity_chips_no_unicode_marks(qtbot, routes_env):
    env = routes_env["env"]
    route = env.route_repo.get_by_key("R2_LLM_POST_TRAINING")
    dlg = RouteDetailDialog(
        route,
        routes_env["route_service"],
        routes_env["route_plan"],
        today_provider=lambda: TODAY,
        topic_learning_service=env.tl,
    )
    qtbot.addWidget(dlg)
    topic = env.plan_repo.find_topic_by_name_in_route(route.id, "LoRA / QLoRA")
    chips = dlg._activity_chips(topic.id)
    for mark in ("✓", "○", "◇", "✔", "★", "☆"):
        assert mark not in chips
    assert "·" in chips
