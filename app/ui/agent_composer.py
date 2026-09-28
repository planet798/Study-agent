"""AgentComposer：Workspace 输入区（纯 UI）。

Interactions (unchanged from Agent-11):
- ``Enter`` inserts a newline
- ``Ctrl+Enter`` sends

The composer only owns presentation and the local interaction contract; the
WorkspacePage owns Session/turn state and decides whether sending is allowed.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components.button import SAButton
from .design import spacing

MAX_AGENT_INPUT_CHARS = 20_000
COUNTER_VISIBLE_THRESHOLD = 16_000


class AgentComposer(QFrame):
    send_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentComposer")

        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.MD, spacing.SM, spacing.MD, spacing.SM)
        root.setSpacing(spacing.XS)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName("AgentMessageInput")
        self.input_edit.setPlaceholderText("围绕当前任务提问…")
        self.input_edit.setFixedHeight(92)
        self.input_edit.installEventFilter(self)
        root.addWidget(self.input_edit)

        self.warning_label = QLabel("单条消息过长，请拆分后发送。")
        self.warning_label.setObjectName("AgentInputWarning")
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        root.addWidget(self.warning_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(spacing.SM)
        self.hint_label = QLabel("Ctrl+Enter 发送 · Enter 换行")
        self.hint_label.setObjectName("AgentComposerHint")
        self.hint_label.setTextFormat(Qt.TextFormat.PlainText)
        bottom.addWidget(self.hint_label)
        self.counter_label = QLabel("")
        self.counter_label.setObjectName("AgentComposerCounter")
        self.counter_label.setTextFormat(Qt.TextFormat.PlainText)
        self.counter_label.setVisible(False)
        bottom.addWidget(self.counter_label)
        bottom.addStretch(1)
        self.send_button = SAButton("发送", variant="primary", size="medium")
        self.send_button.setObjectName("AgentSendButton")
        self.send_button.setAutoDefault(False)
        self.send_button.setDefault(False)
        self.send_button.clicked.connect(self.send_clicked.emit)
        bottom.addWidget(self.send_button)
        root.addLayout(bottom)

    # ---------- public API ----------
    def text(self) -> str:
        return self.input_edit.toPlainText()

    def set_text(self, text: str) -> None:
        self.input_edit.setPlainText(text)

    def clear(self) -> None:
        self.input_edit.clear()

    def set_placeholder(self, text: str) -> None:
        self.input_edit.setPlaceholderText(text)

    def set_input_enabled(self, enabled: bool) -> None:
        self.input_edit.setEnabled(bool(enabled))

    def set_send_enabled(self, enabled: bool) -> None:
        self.send_button.setEnabled(bool(enabled))

    def set_warning_visible(self, visible: bool) -> None:
        self.warning_label.setVisible(bool(visible))

    def update_counter(self) -> None:
        length = len(self.text())
        visible = length >= COUNTER_VISIBLE_THRESHOLD
        self.counter_label.setText(f"{length} / {MAX_AGENT_INPUT_CHARS}" if visible else "")
        self.counter_label.setVisible(visible)

    # ---------- interaction ----------
    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if (watched is self.input_edit and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.send_clicked.emit()
            return True
        return super().eventFilter(watched, event)
