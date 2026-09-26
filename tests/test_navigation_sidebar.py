"""SANavigationSidebar / SANavigationItem tests (UI-2)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from app.ui.app_shell import PAGE_SPECS, PageKey
from app.ui.components.navigation import (
    COLLAPSED_WIDTH,
    EXPANDED_WIDTH,
    SANavigationSidebar,
    key_value,
)
from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


@pytest.fixture()
def sidebar(qapp):
    return SANavigationSidebar(PAGE_SPECS)


def _main_texts(sidebar):
    texts = []
    for i in range(sidebar.items_layout.count()):
        w = sidebar.items_layout.itemAt(i).widget()
        if w is not None:
            texts.append(w.text())
    return texts


def test_nav_item_order(sidebar):
    assert _main_texts(sidebar) == ["今日", "学习路线", "实践项目"]


def test_settings_is_footer(sidebar):
    # Settings 不在主导航里，而是沉底独立区域。
    assert "设置" not in _main_texts(sidebar)
    footer = sidebar.footer_layout.itemAt(0).widget()
    assert footer is not None
    assert footer.text() == "设置"


def test_default_expanded_width(sidebar):
    assert sidebar.is_collapsed() is False
    assert sidebar.maximumWidth() == EXPANDED_WIDTH


def test_collapse_and_expand(sidebar):
    sidebar.set_collapsed(True)
    assert sidebar.is_collapsed() is True
    assert sidebar.maximumWidth() == COLLAPSED_WIDTH
    assert sidebar.brand_title.isHidden() is True
    sidebar.set_collapsed(False)
    assert sidebar.maximumWidth() == EXPANDED_WIDTH
    assert sidebar.brand_title.isHidden() is False


def test_collapsed_item_has_tooltip_and_accessible_name(sidebar):
    item = sidebar.item(PageKey.TODAY)
    sidebar.set_collapsed(True)
    assert item.text() == ""
    assert item.toolTip() == "今日"
    assert item.accessibleName() == "今日"
    sidebar.set_collapsed(False)
    assert item.text() == "今日"


def test_collapse_button_accessible(sidebar):
    assert sidebar.collapse_btn.accessibleName() == "收起侧栏"
    sidebar.set_collapsed(True)
    assert sidebar.collapse_btn.accessibleName() == "展开侧栏"
    assert sidebar.collapse_btn.toolTip() == "展开侧栏"


def test_selected_state_and_filled_icon(sidebar):
    today = sidebar.item(PageKey.TODAY)
    routes = sidebar.item(PageKey.ROUTES)
    regular_key = today.icon().cacheKey()

    sidebar.set_current(PageKey.ROUTES)
    assert routes.isChecked() is True
    assert today.isChecked() is False
    # 选中时使用 Filled icon（regular vs filled 资产不同）
    assert routes.icon().cacheKey() != regular_key


def test_sidebar_pages_are_today_routes_practice_and_settings(sidebar):
    assert {key_value(spec.key) for spec in PAGE_SPECS} == {
        "today", "routes", "practice", "settings"
    }
    assert not hasattr(PageKey, "MONTHLY")
    assert sidebar.item(PageKey.SETTINGS).text() == "设置"


def test_click_emits_page_requested(qtbot, sidebar):
    seen: list[str] = []
    sidebar.page_requested.connect(seen.append)
    item = sidebar.item(PageKey.ROUTES)
    qtbot.mouseClick(item, Qt.MouseButton.LeftButton)
    assert seen == [PageKey.ROUTES.value]
    assert item.isChecked() is True


def test_disabled_item_click_does_not_emit(qtbot, sidebar):
    seen: list[str] = []
    sidebar.page_requested.connect(seen.append)
    item = sidebar.item(PageKey.PRACTICE)
    sidebar.set_item_available(PageKey.PRACTICE, False)
    qtbot.mouseClick(item, Qt.MouseButton.LeftButton)
    assert seen == []


def test_key_value_normalizes_str_enum():
    assert key_value(PageKey.TODAY) == "today"
    assert key_value("today") == "today"
