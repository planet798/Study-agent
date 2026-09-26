"""TaskWidget UI-3 重设计测试（标签 / 操作层级 / Done≠Mastery）。"""

from __future__ import annotations

import pytest

from app.ui.design.theme_manager import ThemeManager
from app.ui.task_widget import TaskWidget

TODAY = "2026-01-05"


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


def _widget(qtbot, repo, **kwargs):
    t = repo.create("任务", scheduled_date=TODAY, **kwargs)
    w = TaskWidget(repo.get(t.id), route_name=kwargs.get("_route_name"))
    qtbot.addWidget(w)
    return w


def test_route_tag_accent(qtbot, repo, six_route_env):
    route = six_route_env.legacy_route
    t = repo.create("任务", scheduled_date=TODAY, route_id=route.id)
    w = TaskWidget(repo.get(t.id), route_name=route.name)
    qtbot.addWidget(w)
    assert w.route_tag_label.text() == route.name
    assert w.route_tag_label.variant() == "accent"


def test_unclassified_tag_neutral(qtbot, repo):
    t = repo.create("任务", scheduled_date=TODAY)
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert w.route_tag_label.text() == "未分类"
    assert w.route_tag_label.variant() == "neutral"


def test_source_tags(qtbot, repo):
    g = repo.create("g", scheduled_date=TODAY, source="generated")
    w = TaskWidget(repo.get(g.id))
    qtbot.addWidget(w)
    assert w.source_tag_label.text() == "Agent 规划"

    m = repo.create("m", scheduled_date=TODAY, source="manual")
    w2 = TaskWidget(repo.get(m.id))
    qtbot.addWidget(w2)
    assert w2.source_tag_label.text() == "自定义"

    k = repo.create("k", scheduled_date=TODAY, source="manual",
                    knowledge_point_id=1)
    w3 = TaskWidget(repo.get(k.id))
    qtbot.addWidget(w3)
    assert w3.source_tag_label.text() == "自定义知识"


def test_activity_tag(qtbot, repo):
    t = repo.create("任务", scheduled_date=TODAY, learning_activity_kind="theory")
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert w.activity_tag_label.text()
    assert w.activity_tag_label.isHidden() is False


def test_task_widget_has_no_review_tag(qtbot, repo):
    t = repo.create("历史复习", scheduled_date=TODAY, task_type="review",
                    source="daily_retention")
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert not hasattr(w, "review_tag_label")


def test_active_action_hierarchy(qtbot, repo):
    t = repo.create("任务", scheduled_date=TODAY, knowledge_point_id=1)
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert w.complete_btn.objectName() == "PrimaryButton"
    assert w.not_done_btn.objectName() == "DangerButton"
    assert w.assessment_btn.objectName() == "SecondaryButton"
    assert w.remove_btn.objectName() == "SAButton"  # subtle


def test_legacy_review_has_generic_remove_action(qtbot, repo):
    t = repo.create("历史复习", scheduled_date=TODAY, task_type="review")
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert getattr(w, "remove_btn", None) is not None


def test_done_formal_task_keeps_assessment(qtbot, repo):
    t = repo.create("任务", scheduled_date=TODAY, knowledge_point_id=1)
    repo.mark_done(t.id)
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    assert w.done_label.text() == "已完成"
    # Done ≠ Mastery：仍提供验收入口
    assert w.assessment_btn is not None


def test_signals_present(qtbot, repo):
    t = repo.create("任务", scheduled_date=TODAY, knowledge_point_id=1)
    w = TaskWidget(repo.get(t.id))
    qtbot.addWidget(w)
    seen = {}
    w.complete_requested.connect(lambda i: seen.setdefault("complete", i))
    w.assessment_requested.connect(lambda i: seen.setdefault("assess", i))
    w.complete_btn.click()
    w.assessment_btn.click()
    assert seen["complete"] == t.id
    assert seen["assess"] == t.id
