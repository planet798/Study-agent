"""AppShell tests (UI-2)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from app.ui.app_shell import PAGE_SPECS, AppShell, PageKey
from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


@pytest.fixture()
def shell(qapp):
    return AppShell(PAGE_SPECS)


def test_shell_composition(shell):
    assert shell.sidebar is not None
    assert shell.page_header is not None
    assert shell.stack is not None
    assert shell.stack.count() == 0


def test_add_page_returns_index(shell):
    a = shell.add_page(QLabel("a"))
    b = shell.add_page(QLabel("b"))
    assert (a, b) == (0, 1)
    assert shell.stack.count() == 2


def test_page_requested_forwarding(qtbot, shell):
    seen: list[str] = []
    shell.page_requested.connect(seen.append)
    qtbot.mouseClick(shell.sidebar.item(PageKey.PRACTICE), Qt.MouseButton.LeftButton)
    assert seen == [PageKey.PRACTICE.value]


def test_set_page_header(shell):
    shell.set_page_header(PageKey.ROUTES)
    assert shell.page_header.title() == "学习路线"
    assert shell.page_header.subtitle() == "管理学习路线与能力进度"
    shell.set_page_header(PageKey.SETTINGS)
    assert shell.page_header.title() == "设置"


def test_set_page_header_with_subtitle_override(shell):
    shell.set_page_header(PageKey.TODAY, subtitle="2026-01-05")
    assert shell.page_header.title() == "今日"
    assert shell.page_header.subtitle() == "2026-01-05"


def test_set_item_available(shell):
    shell.set_item_available(PageKey.MONTHLY, False)
    assert shell.sidebar.item(PageKey.MONTHLY).isEnabled() is False


def test_select_page(shell):
    shell.select_page(PageKey.PRACTICE)
    assert shell.sidebar.current_key() == PageKey.PRACTICE.value
