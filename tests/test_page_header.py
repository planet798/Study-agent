"""SAPageHeader tests (UI-2)."""

from __future__ import annotations

import pytest

from app.ui.components.button import SAButton
from app.ui.components.page_header import SAPageHeader
from app.ui.design.icons import IconName
from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


def test_header_title_and_subtitle():
    header = SAPageHeader("今日", "2026-01-05")
    assert header.title() == "今日"
    assert header.subtitle() == "2026-01-05"
    header.set_title("学习路线")
    header.set_subtitle("管理学习路线与能力进度")
    assert header.title() == "学习路线"
    assert header.subtitle() == "管理学习路线与能力进度"


def test_header_subtitle_hidden_when_empty():
    header = SAPageHeader("x", "")
    assert header.subtitle_label().isHidden() is True
    header.set_subtitle("now")
    assert header.subtitle_label().isHidden() is False
    header.set_subtitle(None)
    assert header.subtitle_label().isHidden() is True


def test_header_subtitle_label_identity():
    header = SAPageHeader("x", "y")
    label = header.subtitle_label()
    header.set_subtitle("z")
    assert label.text() == "z"  # 同一 label 对象


def test_header_icon(qapp):
    header = SAPageHeader("今日")
    header.set_icon(IconName.HOME)
    assert header.leading_label().pixmap().isNull() is False


def test_header_trailing_action():
    header = SAPageHeader("x")
    btn = SAButton("动作", variant="secondary")
    assert header.add_trailing(btn) is btn


def test_conversation_header_allocates_text_width_and_restores_default(qtbot):
    header = SAPageHeader("SFT session", "A short learning route")
    qtbot.addWidget(header)
    header.set_icon(None)
    header.resize(900, 120)
    header.set_conversation_sizing(True)
    header.show()
    qtbot.waitUntil(lambda: header.subtitle_label().width() > 800)
    label = header.subtitle_label()
    assert label.heightForWidth(label.width()) <= label.fontMetrics().height() + 2
    header.set_conversation_sizing(False)
    assert header._row.stretch(1) == 0
    assert header._row.stretch(2) == 1
