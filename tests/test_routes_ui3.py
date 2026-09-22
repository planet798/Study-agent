"""Learning Routes UI-3 / UI-3.1 测试（Route card / Capability distribution）。"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_progress_service import (
    KnowledgeStatus,
    RouteProgress,
    RouteProgressService,
)
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


def _texts(page):
    from PySide6.QtWidgets import QLabel

    return [l.text() for l in page.list_container.findChildren(QLabel)]


def _tag_texts(page):
    return [t.text() for t in page.list_container.findChildren(SATag)]


# ---------------- Overview ----------------

def test_route_cards_render_canonical_keys(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    texts = _texts(page)
    for prefix in ("R1", "R2", "R3", "R4", "R5", "R6"):
        assert prefix in texts, f"missing route key {prefix}"


def test_route_card_uses_progressbar(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    bars = page.list_container.findChildren(SAProgressBar)
    assert bars  # 至少课程进度条


def test_mastery_and_capability_are_separate(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    texts = _texts(page)
    assert any("掌握度" in t for t in texts)
    assert any("能力证据 Capability" in t for t in texts)


def test_capability_summary_has_no_route_level_label(qtbot, routes_env):
    """Route card 不得把某个 KP 的 capability 当作 route capability。"""
    page = _page(qtbot, routes_env)
    for text in _tag_texts(page):
        for label in ("知道概念", "能够解释", "能够写代码",
                      "完成独立实验", "已在真实项目中使用"):
            assert label not in text


def test_no_capability_percentage(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    for tag in page.list_container.findChildren(SATag):
        assert not tag.text().endswith("%")


def test_route_status_badges(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    assert page.list_container.findChildren(SAStatusBadge)


# ---------------- Capability distribution ----------------

def _fake_rp(levels, *, evidence_count=None, reverse=False):
    knowledge = []
    for i, level in enumerate(levels):
        knowledge.append(KnowledgeStatus(
            knowledge_point_id=100 + i,
            name=f"KP{i}",
            topic_id=i,
            status="已验收",
            mastery=0.5 if i % 2 == 0 else None,
            capability_level=level,
            capability_label=f"label{level}" if level else "",
            has_capability_evidence=level > 0,
        ))
    if reverse:
        knowledge = list(reversed(knowledge))
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for level in levels:
        if level > 0:
            counts[level] = counts.get(level, 0) + 1
    return RouteProgress(
        route_id=1,
        topic_total=len(levels),
        topic_covered=len(levels),
        assessment_evidence_count=sum(1 for l in levels if l > 0),
        capability_evidence_count=(evidence_count if evidence_count is not None
                                   else sum(1 for l in levels if l > 0)),
        capability_level_counts=counts,
        knowledge=knowledge,
    )


def test_distribution_empty(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    rp = _fake_rp([0, 0])
    page._route_progress = lambda route_id: rp
    page.refresh()
    assert "暂无能力证据" in _texts(page)


def test_distribution_single_l2(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    page._route_progress = lambda route_id: _fake_rp([2])
    page.refresh()
    tags = _tag_texts(page)
    assert "1 个知识点" in tags
    assert "L2 × 1" in tags


def test_distribution_two_levels(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    page._route_progress = lambda route_id: _fake_rp([2, 4])
    page.refresh()
    tags = _tag_texts(page)
    assert "L2 × 1" in tags
    assert "L4 × 1" in tags
    assert "2 个知识点" in tags


def test_distribution_counts_aggregate(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    page._route_progress = lambda route_id: _fake_rp([3, 3, 5])
    page.refresh()
    tags = _tag_texts(page)
    assert "L3 × 2" in tags
    assert "L5 × 1" in tags


def test_distribution_order_independent(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    page._route_progress = lambda route_id: _fake_rp([2, 4], reverse=True)
    page.refresh()
    tags = _tag_texts(page)
    assert "L2 × 1" in tags and "L4 × 1" in tags


def test_distribution_helper_order_independent():
    a = LearningRoutesPage._route_capability_distribution(_fake_rp([2, 4]))
    b = LearningRoutesPage._route_capability_distribution(
        _fake_rp([2, 4], reverse=True)
    )
    assert a == b == [(2, 1), (4, 1)]


def test_mastery_progressbar_still_independent(qtbot, routes_env):
    page = _page(qtbot, routes_env)
    page._route_progress = lambda route_id: _fake_rp([2, 4])
    page.refresh()
    assert page.list_container.findChildren(SAProgressBar)  # Mastery/课程进度条仍在


# ---------------- Activity chips ----------------

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


# ---------------- RouteDetail structure (UI-3.1) ----------------

def _detail(qtbot, routes_env, route_key="R2_LLM_POST_TRAINING"):
    env = routes_env["env"]
    route = env.route_repo.get_by_key(route_key)
    dlg = RouteDetailDialog(
        route,
        routes_env["route_service"],
        routes_env["route_plan"],
        progress_service=routes_env["progress"],
        today_provider=lambda: TODAY,
        topic_learning_service=env.tl,
    )
    qtbot.addWidget(dlg)
    return dlg


def _dlg_texts(dlg):
    from PySide6.QtWidgets import QLabel

    return [l.text() for l in dlg.findChildren(QLabel)]


def test_detail_sections_present(qtbot, routes_env):
    texts = _dlg_texts(_detail(qtbot, routes_env))
    for section in ("概览", "学习进度", "掌握与复习",
                    "能力证据 Capability", "课程覆盖"):
        assert any(section in t for t in texts), section


def test_detail_no_legacy_bracket_prefixes(qtbot, routes_env):
    joined = "\n".join(_dlg_texts(_detail(qtbot, routes_env)))
    for legacy in ("【路线进度】", "【掌握】", "【学习活动】",
                   "【知识掌握 / 能力】", "【复习状态】"):
        assert legacy not in joined


def test_detail_overview_card_fields(qtbot, routes_env):
    texts = _dlg_texts(_detail(qtbot, routes_env))
    assert "当前阶段" in texts
    assert "当前 Topic" in texts
    assert "目标" in texts


def test_detail_action_hierarchy(qtbot, routes_env):
    from PySide6.QtWidgets import QPushButton

    dlg = _detail(qtbot, routes_env)
    by_text = {b.text(): b for b in dlg.findChildren(QPushButton)}
    assert by_text["AI 生成学习计划"].objectName() == "PrimaryButton"
    assert by_text["归档路线"].objectName() == "SAButton"  # subtle


def test_detail_capability_no_route_level_label(qtbot, routes_env):
    """Capability card 只显示 evidence count / distribution，不显示 route 单一等级。"""
    dlg = _detail(qtbot, routes_env)
    tags = [t.text() for t in dlg.findChildren(SATag)]
    # 不出现百分比
    for t in tags:
        assert not t.endswith("%")
