"""Foundation component tests (UI-1)."""

from __future__ import annotations

import pytest

from app.ui.components.button import SAButton, SAIconButton
from app.ui.components.card import SACard
from app.ui.components.empty_state import SAEmptyState
from app.ui.components.section_header import SASectionHeader
from app.ui.components.status_badge import SAStatusBadge
from app.ui.components.tag import SATag
from app.ui.design import colors
from app.ui.design.icons import IconName
from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def _clean_singleton(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


# ---------------- SAButton ----------------

def test_sa_button_variants_objectname():
    assert SAButton("a", variant="primary").objectName() == "PrimaryButton"
    assert SAButton("b", variant="secondary").objectName() == "SecondaryButton"
    assert SAButton("c", variant="danger").objectName() == "DangerButton"
    assert SAButton("d", variant="subtle").objectName() == "SAButton"


def test_sa_button_invalid_variant():
    with pytest.raises(ValueError):
        SAButton("x", variant="ghost")
    with pytest.raises(ValueError):
        SAButton("x", size="large")


def test_sa_button_secondary_palette_follows_theme():
    tm = ThemeManager.instance()
    btn = SAButton("done", variant="secondary")
    assert btn.palette().buttonText().color().name().lower() == \
        colors.LIGHT_COLORS["accent"].lower()

    tm.set_theme("dark")
    assert btn.palette().buttonText().color().name().lower() == \
        colors.DARK_COLORS["accent"].lower()


def test_sa_button_primary_palette_is_on_accent():
    btn = SAButton("go", variant="primary")
    assert btn.palette().buttonText().color().name().lower() == \
        colors.LIGHT_COLORS["text_on_accent"].lower()


def test_sa_button_disabled_state():
    btn = SAButton("go", variant="primary")
    btn.setEnabled(False)
    assert not btn.isEnabled()


def test_sa_button_set_variant_updates_objectname():
    btn = SAButton("x", variant="subtle")
    btn.set_variant("primary")
    assert btn.objectName() == "PrimaryButton"
    assert btn.variant() == "primary"


# ---------------- SAIconButton ----------------

def test_sa_icon_button_accessible_and_tooltip():
    btn = SAIconButton(IconName.SETTINGS, tooltip="设置")
    assert btn.accessibleName() == "设置"
    assert btn.toolTip() == "设置"
    assert not btn.icon().isNull()


def test_sa_icon_button_derives_accessible_name():
    btn = SAIconButton(IconName.ADD)
    assert btn.accessibleName() == "add"
    assert btn.toolTip() == "add"


def test_sa_icon_button_not_fixed_width():
    btn = SAIconButton(IconName.MORE, icon_size=18)
    assert btn.minimumSizeHint().width() > 0
    assert btn.sizeHint().width() >= btn.minimumSizeHint().width()
    # 不应使用固定死宽（maximumWidth 仍为 Qt 默认上限）
    assert btn.maximumWidth() > 1000


def test_sa_icon_button_danger_variant():
    btn = SAIconButton(IconName.DELETE, variant="danger")
    assert btn.palette().buttonText().color().name().lower() == \
        colors.LIGHT_COLORS["danger"].lower()
    with pytest.raises(ValueError):
        SAIconButton(IconName.DELETE, variant="primary")


# ---------------- SACard ----------------

def test_sa_card_variants():
    for variant in ("default", "interactive", "selected"):
        card = SACard(variant=variant)
        assert card.variant() == variant
        assert card.property("saCardVariant") == variant
    with pytest.raises(ValueError):
        SACard(variant="pill")


def test_sa_card_add_widget():
    from PySide6.QtWidgets import QLabel

    card = SACard()
    w = card.add_widget(QLabel("hi"))
    assert card.body_layout.count() == 1
    assert w.parent() is card


# ---------------- SATag ----------------

def test_sa_tag_variants():
    for variant in ("neutral", "accent", "success", "warning", "danger", "info"):
        tag = SATag("x", variant=variant)
        assert tag.variant() == variant
        assert tag.property("saTagVariant") == variant
    with pytest.raises(ValueError):
        SATag("x", variant="rainbow")


# ---------------- SAStatusBadge ----------------

def test_sa_status_badge():
    badge = SAStatusBadge("active")
    assert badge.status() == "active"
    assert badge.text() == "进行中"
    badge.set_status("completed")
    assert badge.text() == "已完成"
    with pytest.raises(ValueError):
        SAStatusBadge("unknown_status")


# ---------------- SAEmptyState ----------------

def test_sa_empty_state_action_optional():
    state = SAEmptyState(title="空", description="没有内容")
    assert state.action() is None

    action = SAButton("新建", variant="primary")
    state.set_action(action)
    assert state.action() is action

    state2 = SAEmptyState(title="x", action=SAButton("a"))
    assert state2.action() is not None


def test_sa_empty_state_without_icon():
    state = SAEmptyState(title="only title")
    assert state is not None


# ---------------- SASectionHeader ----------------

def test_sa_section_header():
    trailing = SAButton("更多", variant="subtle")
    header = SASectionHeader("Focus", subtitle="今天", trailing=trailing)
    assert header.title() == "Focus"
    assert header.trailing() is trailing

    header.set_subtitle(None)
    header.set_title("Review")
    assert header.title() == "Review"
