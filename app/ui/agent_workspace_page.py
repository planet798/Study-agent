"""Task-driven internal Agent Workspace (not a Sidebar / PageSpec page)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.learning_activity import activity_label
from .components.button import SAButton
from .task_widget import format_minutes


class AgentWorkspacePage(QWidget):
    """Plain-text conversation view for one task-bound Agent Session."""

    back_requested = Signal()
    send_requested = Signal(int, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentWorkspacePage")
        self.current_session_id: int | None = None
        self._model_configured = False
        self._busy = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 20)
        root.setSpacing(12)

        self.back_button = SAButton("← 返回今日", variant="subtle", size="small")
        self.back_button.clicked.connect(self.back_requested.emit)
        root.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.task_title_label = QLabel("")
        self.task_title_label.setObjectName("AgentTaskTitle")
        self.task_title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.task_title_label.setWordWrap(True)
        root.addWidget(self.task_title_label)

        self.task_meta_label = QLabel("")
        self.task_meta_label.setObjectName("TaskMeta")
        self.task_meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self.task_meta_label.setWordWrap(True)
        root.addWidget(self.task_meta_label)

        self.task_description_label = QLabel("")
        self.task_description_label.setObjectName("AgentTaskDescription")
        self.task_description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.task_description_label.setWordWrap(True)
        self.task_description_label.setVisible(False)
        root.addWidget(self.task_description_label)

        self.model_unavailable_label = QLabel(
            "AI 模型尚未配置，请先在设置中配置当前模型。"
        )
        self.model_unavailable_label.setObjectName("AgentModelUnavailable")
        self.model_unavailable_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_unavailable_label.setWordWrap(True)
        self.model_unavailable_label.setVisible(False)
        root.addWidget(self.model_unavailable_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("AgentErrorBanner")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setObjectName("AgentConversationScroll")
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conversation_body = QWidget()
        self.conversation_layout = QVBoxLayout(self.conversation_body)
        self.conversation_layout.setContentsMargins(0, 4, 0, 4)
        self.conversation_layout.setSpacing(10)
        self.conversation_layout.addStretch()
        self.conversation_scroll.setWidget(self.conversation_body)
        root.addWidget(self.conversation_scroll, stretch=1)

        self.empty_hint = QLabel(
            "围绕这个任务开始学习。你可以让我讲解概念、分析代码、"
            "设计练习，或询问当前学习状态。"
        )
        self.empty_hint.setObjectName("AgentEmptyHint")
        self.empty_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.empty_hint.setWordWrap(True)
        root.addWidget(self.empty_hint)

        self.busy_label = QLabel("Agent 正在思考…")
        self.busy_label.setObjectName("AgentBusyLabel")
        self.busy_label.setTextFormat(Qt.TextFormat.PlainText)
        self.busy_label.setVisible(False)
        root.addWidget(self.busy_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName("AgentMessageInput")
        self.input_edit.setPlaceholderText("围绕当前学习任务提问…")
        self.input_edit.setFixedHeight(88)
        self.input_edit.textChanged.connect(self._update_send_enabled)
        input_row.addWidget(self.input_edit, stretch=1)
        self.send_button = SAButton("发送", variant="primary", size="medium")
        self.send_button.setObjectName("AgentSendButton")
        self.send_button.clicked.connect(self._send_current)
        input_row.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(input_row)

    def load_session(
        self,
        session: dict,
        messages: list[dict],
        task,
        route_name: str | None,
        model_configured: bool,
    ) -> None:
        """Replace all Task/Session state; never leaves previous Session content."""
        self.current_session_id = int(session["id"])
        self.task_title_label.setText(task.title or "")
        activity = getattr(task, "learning_activity_kind", None)
        if activity:
            activity_text = activity_label(activity)
        elif task.knowledge_point_id is not None or task.topic_id is not None:
            activity_text = "知识学习"
        else:
            activity_text = "学习活动"
        route_text = route_name or ("未分类" if task.route_id is None else "学习路线")
        self.task_meta_label.setText(
            f"{route_text} · {activity_text} · {format_minutes(task.estimated_minutes)}"
        )
        description = (task.description or "").strip()
        self.task_description_label.setText(description)
        self.task_description_label.setVisible(bool(description))

        self._clear_conversation()
        visible_count = 0
        for message in messages:
            role = message.get("role")
            if role == "user":
                self._add_bubble("你", str(message.get("content") or ""), "user")
                visible_count += 1
            elif role == "assistant" and not message.get("tool_calls_json"):
                text = str(message.get("content") or "")
                if text.strip():
                    self._add_bubble("学习助手", text, "assistant")
                    visible_count += 1
        self.empty_hint.setVisible(visible_count == 0)
        self._model_configured = bool(model_configured)
        self.model_unavailable_label.setVisible(not self._model_configured)
        self.input_edit.clear()
        self.set_error("")
        self.set_busy(False)
        self._update_send_enabled()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.busy_label.setVisible(self._busy)
        self.input_edit.setEnabled(not self._busy and self._model_configured)
        self._update_send_enabled()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def _clear_conversation(self) -> None:
        while self.conversation_layout.count():
            item = self.conversation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.conversation_layout.addStretch()

    def _add_bubble(self, speaker: str, text: str, role: str) -> None:
        bubble = QFrame()
        bubble.setObjectName("AgentMessageBubble")
        bubble.setProperty("role", role)
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        speaker_label = QLabel(speaker)
        speaker_label.setObjectName("AgentMessageSpeaker")
        speaker_label.setTextFormat(Qt.TextFormat.PlainText)
        body = QLabel(text)
        body.setObjectName("AgentMessageText")
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(speaker_label)
        layout.addWidget(body)
        # Insert before the trailing stretch.
        self.conversation_layout.insertWidget(
            max(0, self.conversation_layout.count() - 1), bubble
        )

    def _update_send_enabled(self) -> None:
        self.send_button.setEnabled(
            self._model_configured
            and not self._busy
            and bool(self.input_edit.toPlainText().strip())
        )
        if not self._model_configured:
            self.input_edit.setEnabled(False)
        elif not self._busy:
            self.input_edit.setEnabled(True)

    def _send_current(self) -> None:
        if self.current_session_id is None:
            return
        text = self.input_edit.toPlainText()
        if not text.strip() or self._busy or not self._model_configured:
            return
        self.input_edit.clear()
        self.send_requested.emit(self.current_session_id, text)
