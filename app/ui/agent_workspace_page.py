"""Task-driven internal Agent Workspace (not a Sidebar / PageSpec page)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QEvent, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..agent.status import AgentCapabilityStatus
from ..services.learning_activity import activity_label
from .components.button import SAButton
from .task_widget import format_minutes


MAX_AGENT_INPUT_CHARS = 20_000

STATUS_WARNINGS = {
    "mcp_config_invalid": "MCP 配置无效，本次仅使用内置能力。",
    "sandbox_config_invalid": "Sandbox 配置无效，文件与执行能力已禁用。",
    "sandbox_execution_unavailable": "Sandbox 文件能力可用，但代码执行环境不可用。",
}


class AgentWorkspacePage(QWidget):
    """Plain-text conversation view for one task-bound Agent Session."""

    back_requested = Signal()
    send_requested = Signal(int, str)
    approval_approve_requested = Signal(int)
    approval_reject_requested = Signal(int)
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentWorkspacePage")
        self.current_session_id: int | None = None
        self._model_configured = False
        self._busy = False
        self._approval_busy: set[int] = set()
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

        self.status_label = QLabel("")
        self.status_label.setObjectName("AgentStatusRow")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.capability_warning_label = QLabel("")
        self.capability_warning_label.setObjectName("AgentCapabilityWarning")
        self.capability_warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.capability_warning_label.setWordWrap(True)
        self.capability_warning_label.hide()
        root.addWidget(self.capability_warning_label)

        self.model_unavailable_label = QLabel(
            "AI 模型尚未配置，请先在设置中配置当前模型。"
        )
        self.model_unavailable_label.setObjectName("AgentModelUnavailable")
        self.model_unavailable_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_unavailable_label.setWordWrap(True)
        self.model_unavailable_label.setVisible(False)
        root.addWidget(self.model_unavailable_label)
        self.settings_button = SAButton("前往设置", variant="subtle", size="small")
        self.settings_button.setObjectName("AgentGoToSettings")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.settings_button.hide()
        root.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignLeft)

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

        self.approvals_container = QWidget()
        self.approvals_container.setObjectName("AgentPendingApprovals")
        self.approvals_layout = QVBoxLayout(self.approvals_container)
        self.approvals_container.hide()
        root.addWidget(self.approvals_container)

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
        self.input_warning_label = QLabel("单条消息过长，请拆分后发送。")
        self.input_warning_label.setObjectName("AgentInputWarning")
        self.input_warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.input_warning_label.hide()
        root.addWidget(self.input_warning_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName("AgentMessageInput")
        self.input_edit.setPlaceholderText("围绕当前学习任务提问…")
        self.input_edit.setFixedHeight(88)
        self.input_edit.textChanged.connect(self._update_send_enabled)
        self.input_edit.installEventFilter(self)
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
        approvals=(),
        *,
        capability_status: AgentCapabilityStatus | None = None,
        turn_busy: bool = False,
        approval_busy: tuple[int, ...] = (),
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
        self._approval_busy = set(approval_busy)
        self._render_approvals(approvals)
        status = capability_status or AgentCapabilityStatus(model_configured=bool(model_configured))
        self._model_configured = bool(status.model_configured)
        parts = ["AI 已配置" if self._model_configured else "AI 未配置", "应用数据只读"]
        if status.approvals_enabled:
            parts.append("写操作需批准")
        if status.mcp_configured:
            parts.append("MCP 已配置")
        if status.sandbox_configured:
            parts.append("Sandbox 已启用")
        if status.sandbox_execution_configured:
            parts.append("Sandbox 执行已配置")
        self.status_label.setText(" · ".join(parts))
        warnings = [STATUS_WARNINGS[code] for code in status.warnings if code in STATUS_WARNINGS]
        self.capability_warning_label.setText("\n".join(warnings))
        self.capability_warning_label.setVisible(bool(warnings))
        self.model_unavailable_label.setVisible(not self._model_configured)
        self.settings_button.setVisible(not self._model_configured)
        self.input_edit.clear()
        self.set_error("")
        self.set_busy(turn_busy)
        QTimer.singleShot(0, self._scroll_bottom)

    def _render_approvals(self, approvals) -> None:
        while self.approvals_layout.count():
            item = self.approvals_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        count = 0
        descriptions = {
            "request_complete_current_task": (
                "待批准操作：Agent 请求完成当前学习任务。批准后执行现有任务完成流程；"
                "不代表通过验收或提高 Mastery。", "批准",
            ),
            "request_start_assessment": (
                "Agent 请求开始当前任务的正式学习验收。批准后系统会生成或恢复题目；"
                "你仍需亲自回答，只有提交并判题后才可能更新 Mastery。", "批准并开始验收",
            ),
            "request_save_learning_note": (
                "Agent 请求保存学习笔记。笔记不是 Mastery 或任务完成证据。", "批准保存",
            ),
        }
        for row in sorted(approvals, key=lambda item: int(item["id"])):
            name = row.get("tool_name")
            if row.get("status") != "pending" or name not in descriptions:
                continue
            approval_id = int(row["id"])
            card = QFrame()
            card.setObjectName("AgentApprovalCard")
            card.setProperty("approval_id", approval_id)
            layout = QVBoxLayout(card)
            text, approve_text = descriptions[name]
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
            if name == "request_save_learning_note":
                preview = QLabel(
                    f"标题：{row.get('note_title', '')}\n内容预览：{row.get('note_preview', '')}"
                )
                preview.setObjectName("AgentApprovalNotePreview")
                preview.setTextFormat(Qt.TextFormat.PlainText)
                preview.setWordWrap(True)
                layout.addWidget(preview)
            buttons = QHBoxLayout()
            reject = SAButton("拒绝", variant="subtle", size="small")
            reject.setObjectName("AgentApprovalReject")
            reject.clicked.connect(lambda _checked=False, aid=approval_id: self.approval_reject_requested.emit(aid))
            approve = SAButton(approve_text, variant="primary", size="small")
            approve.setObjectName("AgentApprovalApprove")
            approve.setProperty("normal_text", approve_text)
            approve.clicked.connect(lambda _checked=False, aid=approval_id: self.approval_approve_requested.emit(aid))
            buttons.addWidget(reject)
            buttons.addWidget(approve)
            layout.addLayout(buttons)
            for button in (reject, approve):
                button.setEnabled(not self._approval_busy)
            if self._approval_busy:
                approve.setText("正在执行…")
            self.approvals_layout.addWidget(card)
            count += 1
        self.approvals_container.setVisible(count > 0)

    def _scroll_bottom(self) -> None:
        bar = self.conversation_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_session(self, error: str = "") -> None:
        self.current_session_id = None
        self._approval_busy.clear()
        self._busy = False
        self._model_configured = False
        for label in (self.task_title_label, self.task_meta_label,
                      self.task_description_label, self.status_label,
                      self.capability_warning_label):
            label.clear()
        self.capability_warning_label.hide()
        self.task_description_label.hide()
        self.model_unavailable_label.hide()
        self.settings_button.hide()
        self._clear_conversation()
        self._render_approvals(())
        self.input_edit.clear()
        self.input_warning_label.hide()
        self.busy_label.hide()
        self.empty_hint.show()
        self.set_error(error)
        self._update_send_enabled()

    def set_approval_busy(self, approval_id: int, busy: bool) -> None:
        if busy:
            self._approval_busy.add(int(approval_id))
        else:
            self._approval_busy.discard(int(approval_id))
        self._update_approval_buttons()
        self._update_send_enabled()

    def _update_approval_buttons(self) -> None:
        locked = self._busy or bool(self._approval_busy)
        for card in self.approvals_container.findChildren(QFrame, "AgentApprovalCard"):
            for button in card.findChildren(SAButton):
                button.setEnabled(not locked)
                if button.objectName() == "AgentApprovalApprove":
                    button.setText("正在执行…" if self._approval_busy
                                   else button.property("normal_text"))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.busy_label.setText("Agent 正在思考…" if self._busy else "")
        self.busy_label.setVisible(self._busy)
        self._update_approval_buttons()
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
        text = self.input_edit.toPlainText()
        too_long = len(text) > MAX_AGENT_INPUT_CHARS
        self.input_warning_label.setVisible(too_long)
        enabled = (self.current_session_id is not None and self._model_configured
                   and not self._busy and not self._approval_busy)
        self.input_edit.setEnabled(enabled)
        self.send_button.setEnabled(enabled and bool(text.strip()) and not too_long)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if (watched is self.input_edit and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._send_current()
            return True
        return super().eventFilter(watched, event)

    def _send_current(self) -> None:
        if self.current_session_id is None:
            return
        text = self.input_edit.toPlainText()
        if (not text.strip() or len(text) > MAX_AGENT_INPUT_CHARS
                or self._busy or self._approval_busy or not self._model_configured):
            return
        self.input_edit.clear()
        self.send_requested.emit(self.current_session_id, text)
