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
