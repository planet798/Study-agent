"""AgentComposer interaction contract (UX-2): shortcuts, hint, counter, states."""

from __future__ import annotations

from PySide6.QtCore import Qt

from app.ui.agent_composer import (
    COUNTER_VISIBLE_THRESHOLD,
    MAX_AGENT_INPUT_CHARS,
    AgentComposer,
)


def test_composer_placeholder_and_shortcut_hint(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    assert "围绕当前任务" in composer.input_edit.placeholderText()
    assert "Ask AI" not in composer.input_edit.placeholderText()
    assert composer.hint_label.text() == "Ctrl+Enter 发送 · Enter 换行"
    assert composer.send_button.text() == "发送"


def test_enter_inserts_newline_and_does_not_send(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.show()
    sent = []
    composer.send_clicked.connect(lambda: sent.append(True))
    composer.input_edit.setPlainText("line one")
    qtbot.keyClick(composer.input_edit, Qt.Key.Key_Return)
    assert sent == []
    assert composer.input_edit.toPlainText().count("\n") == 1


def test_ctrl_enter_sends(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.show()
    sent = []
    composer.send_clicked.connect(lambda: sent.append(True))
    composer.input_edit.setPlainText("hello")
    qtbot.keyClick(composer.input_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [True]


def test_send_button_emits_signal(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    sent = []
    composer.send_clicked.connect(lambda: sent.append(True))
    qtbot.mouseClick(composer.send_button, Qt.MouseButton.LeftButton)
    assert sent == [True]


def test_near_limit_counter_only_appears_close_to_max(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.set_text("short")
    composer.update_counter()
    assert composer.counter_label.isHidden()
    composer.set_text("x" * (COUNTER_VISIBLE_THRESHOLD - 1))
    composer.update_counter()
    assert composer.counter_label.isHidden()
    composer.set_text("x" * COUNTER_VISIBLE_THRESHOLD)
    composer.update_counter()
    assert not composer.counter_label.isHidden()
    assert composer.counter_label.text() == f"{COUNTER_VISIBLE_THRESHOLD} / {MAX_AGENT_INPUT_CHARS}"


def test_over_limit_warning_is_explicit(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.set_warning_visible(True)
    assert not composer.warning_label.isHidden()
    assert "过长" in composer.warning_label.text()
    composer.set_warning_visible(False)
    assert composer.warning_label.isHidden()


def test_composer_enable_states(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.set_input_enabled(False)
    composer.set_send_enabled(False)
    assert not composer.input_edit.isEnabled()
    assert not composer.send_button.isEnabled()
    composer.set_input_enabled(True)
    composer.set_send_enabled(True)
    assert composer.input_edit.isEnabled()
    assert composer.send_button.isEnabled()


def test_composer_clear_and_text_roundtrip(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.set_text("draft")
    assert composer.text() == "draft"
    composer.clear()
    assert composer.text() == ""


def test_composer_grows_to_cap_and_clear_shrinks(qtbot):
    composer = AgentComposer()
    qtbot.addWidget(composer)
    composer.resize(600, 150)
    composer.show()
    edit = composer.input_edit
    qtbot.wait(30)
    initial = edit.height()
    assert 2 * edit.fontMetrics().lineSpacing() <= initial < 4 * edit.fontMetrics().lineSpacing() + 20
    composer.set_text("\n".join(["line"] * 5))
    qtbot.waitUntil(lambda: edit.height() > initial)
    assert edit.verticalScrollBar().maximum() == 0
    composer.set_text("\n".join(["line"] * 40))
    qtbot.waitUntil(lambda: edit.verticalScrollBar().maximum() > 0)
    qtbot.wait(30)
    assert not composer._height_timer.isActive()
    cap = edit.height()
    assert cap <= 8 * edit.fontMetrics().lineSpacing() + 20
    composer.set_text("\n".join(["line"] * 80))
    qtbot.wait(30)
    assert edit.height() == cap
    composer.clear()
    qtbot.waitUntil(lambda: edit.height() == initial and edit.verticalScrollBar().maximum() == 0)
    assert edit.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_composer_wrapping_font_theme_and_page_height_recompute(qtbot, qapp, repo):
    from app.ui.agent_workspace_page import AgentWorkspacePage
    from app.ui.design.theme_manager import ThemeManager

    task = repo.create(title="Resize", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(1000, 900)
    page.load_session({"id": 1}, [], task, None, True)
    page.show()
    edit = page.input_edit
    qtbot.wait(30)
    initial = edit.height()
    page.composer.set_text("wrapped draft " * 26)
    page.resize(440, 900)
    qtbot.waitUntil(lambda: edit.height() > initial)
    narrow_height = edit.height()
    page.resize(1000, 900)
    qtbot.waitUntil(lambda: edit.height() < narrow_height)
    draft = page.composer.text()
    page.composer.set_text("draft " * 800)
    qtbot.waitUntil(lambda: edit.verticalScrollBar().maximum() > 0)
    qtbot.wait(30)
    tall_cap = edit.height()
    page.resize(1000, 380)
    qtbot.waitUntil(lambda: edit.height() < tall_cap)
    assert edit.height() <= max(2 * edit.fontMetrics().lineSpacing() + 20, int(page.height() * .30))
    page.resize(1000, 900)
    page.composer.set_text(draft)
    for theme in ("dark", "light"):
        ThemeManager.instance().set_theme(theme)
        ThemeManager.instance().apply(qapp)
        qtbot.wait(30)
        assert page.composer.text() == draft
        assert edit.verticalScrollBar().maximum() == 0
    font = edit.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    edit.setFont(font)
    qtbot.waitUntil(lambda: edit.height() >= 2 * edit.fontMetrics().lineSpacing() + 12)
    page.composer.clear()
    qtbot.waitUntil(lambda: edit.verticalScrollBar().maximum() == 0)
