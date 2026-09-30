"""Task-driven internal Agent Workspace (not a Sidebar / PageSpec page).

UX-2 information hierarchy::

    Global SAPageHeader   title=学习会话  subtitle=Task title
    Workspace
        Toolbar           [← 返回今日]  + capability chips (wrap)
        Task Context Card route / activity / duration tags + description
        Warning / error / model-unavailable surfaces (only when relevant)
        Empty hint        (only when no visible messages)
        Conversation      (UX-1 AgentMessageWidget, single outer scroll)
        Pending approvals (only when pending)
        Composer          (AgentComposer, independent surface)

The Task title is intentionally **not** rendered here: its single primary home
is the SAPageHeader subtitle, so the Workspace never duplicates it.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..agent.status import AgentCapabilityStatus
from ..services.learning_activity import activity_label
from .agent_composer import MAX_AGENT_INPUT_CHARS, AgentComposer
from .agent_message_widget import ASSISTANT_ROLE, USER_ROLE, AgentMessageWidget
from .agent_task_context_card import AgentTaskContextCard
from .agent_workspace_card import AgentWorkspaceCard
from .components.button import SAButton
from .components.empty_state import SAEmptyState
from .components.flow_layout import FlowWidget
from .components.info_banner import SAInfoBanner
from .components.tag import SATag
from .design import spacing
from .task_widget import format_minutes


STATUS_WARNINGS = {
    "mcp_config_invalid": "MCP 配置无效，本次仅使用内置能力。",
    "sandbox_config_invalid": "Sandbox 执行配置无效，文件工作区仍可使用。",
    "sandbox_execution_unavailable": "Sandbox 文件能力可用，但代码执行环境不可用。",
}

# Static, application-owned approval copy. Model output can never set these.
APPROVAL_ACTIONS = {
    "request_complete_current_task": {
        "title": "完成任务",
        "description": (
            "批准后按现有任务完成流程把当前任务标记为完成；"
            "不代表通过验收或提高 Mastery。"
        ),
        "approve": "批准",
        "reject": "拒绝",
    },
    "request_start_assessment": {
        "title": "开始正式验收",
        "description": (
            "批准后会生成或恢复验收题。你仍需亲自回答，"
            "只有提交并判题后才可能更新 Mastery。"
        ),
        "approve": "批准并开始验收",
        "reject": "拒绝",
    },
    "request_save_learning_note": {
        "title": "保存学习笔记",
        "description": "批准后保存这次的学习笔记。笔记不是 Mastery 或任务完成证据。",
        "approve": "批准保存",
        "reject": "拒绝",
    },
}


class AgentWorkspacePage(QWidget):
    """Task-bound conversation Workspace with a product-grade interaction shell."""

    send_requested = Signal(int, str)
    approval_approve_requested = Signal(int)
    approval_reject_requested = Signal(int)
    settings_requested = Signal()
    workspace_managed_requested = Signal(int)
    workspace_local_requested = Signal(int)
    workspace_open_requested = Signal(int)
    workspace_clear_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentWorkspacePage")
        self.current_session_id: int | None = None
        self.current_task_id: int | None = None
        self._model_configured = False
        self._busy = False
        self._approval_busy: set[int] = set()
        self._visible_messages: tuple = ()
        self._pending_scroll_handler = None
        self._scroll_epoch = 0
        self._programmatic_scroll = False
        self._rebuilding = False
        self._scrolled_away_during_busy = False
        self._unseen_reply = False
        self._build_ui()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XXL, spacing.LG, spacing.XXL, spacing.XL)
        root.setSpacing(spacing.LG)

        self.task_context_card = AgentTaskContextCard()
        self.workspace_card = AgentWorkspaceCard()
        self.workspace_card.managed_requested.connect(self.workspace_managed_requested.emit)
        self.workspace_card.local_requested.connect(self.workspace_local_requested.emit)
        self.workspace_card.open_requested.connect(self.workspace_open_requested.emit)
        self.workspace_card.clear_requested.connect(self.workspace_clear_requested.emit)
        header = QHBoxLayout()
        header.setSpacing(spacing.SM)
        header.addWidget(self.task_context_card, stretch=1)
        header.addWidget(self.workspace_card)
        root.addLayout(header)
        root.addLayout(self._build_toolbar())

        self.capability_warning_banner = SAInfoBanner(variant="warning")
        self.capability_warning_banner.hide()
        root.addWidget(self.capability_warning_banner)

        self.model_unavailable_banner = SAInfoBanner(
            title="AI 模型尚未配置",
            description="请先在设置中配置当前模型，然后继续当前学习任务。",
            variant="warning",
        )
        self.settings_button = SAButton("前往设置", variant="subtle", size="small")
        self.settings_button.setObjectName("AgentGoToSettings")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.model_unavailable_banner.set_action(self.settings_button)
        self.model_unavailable_banner.hide()
        root.addWidget(self.model_unavailable_banner)

        self.error_label = QLabel("")
        self.error_label.setObjectName("AgentErrorBanner")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        self.empty_hint = SAEmptyState(
            title="从当前任务开始学习",
            description=(
                "你可以让我解释核心概念、分析代码或实验结果，"
                "也可以在准备好后发起正式验收。"
            ),
        )
        self.empty_hint.setObjectName("AgentEmptyHint")

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setObjectName("AgentConversationScroll")
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conversation_body = QWidget()
        self.conversation_layout = QVBoxLayout(self.conversation_body)
        self.conversation_layout.setContentsMargins(0, 4, 0, 4)
        self.conversation_layout.setSpacing(spacing.LG)
        self.conversation_layout.addWidget(self.empty_hint)
        self.conversation_layout.addStretch()
        self.conversation_scroll.setWidget(self.conversation_body)
        root.addWidget(self.conversation_scroll, stretch=1)
        self.latest_button = SAButton("↓ 最新", variant="secondary", size="small",
                                      parent=self.conversation_scroll.viewport())
        self.latest_button.setObjectName("AgentLatestButton")
        self.latest_button.clicked.connect(self._on_latest_clicked)
        self.latest_button.hide()
        self.conversation_scroll.viewport().installEventFilter(self)
        bar = self.conversation_scroll.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll_value_changed)
        bar.sliderPressed.connect(self._clear_pending_scroll)
        bar.rangeChanged.connect(self._update_latest_button)

        root.addWidget(self._build_approvals())

        self.interaction_status_label = QLabel("")
        self.interaction_status_label.setObjectName("AgentInteractionStatus")
        self.interaction_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.interaction_status_label.setVisible(False)
        root.addWidget(self.interaction_status_label)

        self.composer = AgentComposer()
        self.composer.send_clicked.connect(self._send_current)
        self.composer.input_edit.textChanged.connect(self._update_send_enabled)
        root.addWidget(self.composer)

    def _build_toolbar(self) -> QVBoxLayout:
        toolbar = QVBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(spacing.SM)

        self.capability_chips_widget = FlowWidget()
        self.capability_chips: dict[str, SATag] = {}
        for key in ("ai", "approval", "mcp", "sandbox", "sandbox_exec"):
            tag = SATag("", "neutral")
            self.capability_chips_widget.add_widget(tag)
            self.capability_chips[key] = tag
        toolbar.addWidget(self.capability_chips_widget)
        return toolbar

    def _build_approvals(self) -> QWidget:
        self.approvals_container = QWidget()
        self.approvals_container.setObjectName("AgentPendingApprovals")
        container = QVBoxLayout(self.approvals_container)
        container.setContentsMargins(0, 0, 0, 0)
        container.setSpacing(spacing.SM)

        self.approval_section_title = QLabel("待确认操作")
        self.approval_section_title.setObjectName("AgentApprovalSectionTitle")
        self.approval_section_title.setTextFormat(Qt.TextFormat.PlainText)
        container.addWidget(self.approval_section_title)
        self.approval_section_caption = QLabel("这些操作只有在你确认后才会执行。")
        self.approval_section_caption.setObjectName("AgentApprovalSectionCaption")
        self.approval_section_caption.setTextFormat(Qt.TextFormat.PlainText)
        container.addWidget(self.approval_section_caption)

        self.approvals_layout = QVBoxLayout()
        self.approvals_layout.setContentsMargins(0, 0, 0, 0)
        self.approvals_layout.setSpacing(spacing.SM)
        container.addLayout(self.approvals_layout)
        self.approvals_container.hide()
        return self.approvals_container

    # ------------------------------------------------------------- state
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
        workspace_view=None,
        turn_busy: bool = False,
        approval_busy: tuple[int, ...] = (),
    ) -> None:
        """Replace Task/Session state without leaking content across Sessions.

        Same-Session reloads (status / approval / error refresh) preserve the
        user's unsent composer draft; a real Session switch clears it.
        """
        previous_session_id = self.current_session_id
        new_session_id = int(session["id"])
        session_changed = previous_session_id != new_session_id
        previous_scroll = self.conversation_scroll.verticalScrollBar().value()
        previous_messages = self._visible_messages
        reading_history = self._scrolled_away_during_busy or (
            self._busy and self._distance_from_bottom() > 48)
        self._clear_pending_scroll()
        self._rebuilding = True
        if session_changed:
            self._unseen_reply = False
            self._scrolled_away_during_busy = False

        self._bind_task_context(task, route_name)
        self.task_context_card.setVisible(True)
        self.current_task_id = int(task.id)
        self.workspace_card.set_view(workspace_view)
        self.workspace_card.setVisible(True)

        self._clear_conversation()
        visible_count = 0
        assistant_widgets: dict[int, AgentMessageWidget] = {}
        for message in messages:
            role = message.get("role")
            if role == "user":
                self._add_bubble("你", str(message.get("content") or ""), "user",
                                 message_id=message.get("id"))
                visible_count += 1
            elif role == "assistant" and not message.get("tool_calls_json"):
                text = str(message.get("content") or "")
                if text.strip():
                    widget = self._add_bubble("学习助手", text, "assistant",
                                              message_id=message.get("id"))
                    assistant_widgets[visible_count] = widget
                    visible_count += 1
        self.empty_hint.setVisible(visible_count == 0)

        self._approval_busy = set(approval_busy)
        self._render_approvals(approvals)
        status = capability_status or AgentCapabilityStatus(model_configured=bool(model_configured))
        self._model_configured = bool(status.model_configured)
        self._apply_capability_status(status)

        if session_changed:
            self.composer.clear()
        self.current_session_id = new_session_id
        self.set_error("")
        self.set_busy(turn_busy)
        if session_changed:
            self._scrolled_away_during_busy = False

        current = self._visible_message_signature(messages)
        new_assistant = None
        if (not session_changed and len(current) > len(previous_messages)
                and current[:len(previous_messages)] == previous_messages):
            for index in range(len(previous_messages), len(current)):
                if current[index][0] == "assistant":
                    new_assistant = assistant_widgets.get(index)
                    break
        self._visible_messages = current
        if session_changed:
            self._schedule_scroll(to_bottom=True)
        elif new_assistant is not None and reading_history:
            self._unseen_reply = True
            self._restore_scroll(previous_scroll)
        elif new_assistant is not None:
            self._unseen_reply = False
            self._schedule_scroll(widget=new_assistant)
        elif len(current) > len(previous_messages):
            self._schedule_scroll(to_bottom=True)
        else:
            self._restore_scroll(previous_scroll)
        self._rebuilding = False
        self._update_latest_button()

    def _bind_task_context(self, task, route_name: str | None) -> None:
        activity = getattr(task, "learning_activity_kind", None)
        if activity:
            activity_text = activity_label(activity)
        elif task.knowledge_point_id is not None or task.topic_id is not None:
            activity_text = "知识学习"
        else:
            activity_text = "学习活动"
        route_fallback = route_name is None
        route_text = route_name or ("未分类" if task.route_id is None else "学习路线")
        self.task_context_card.set_metadata(
            route_text,
            activity_text,
            format_minutes(task.estimated_minutes),
            route_is_fallback=route_fallback,
        )
        self.task_context_card.set_description(task.description)

    def _apply_capability_status(self, status: AgentCapabilityStatus) -> None:
        ai = self.capability_chips["ai"]
        ai.setText("AI 已配置" if self._model_configured else "AI 未配置")
        ai.set_variant("info" if self._model_configured else "warning")
        ai.setVisible(True)

        approval = self.capability_chips["approval"]
        approval.setText("写操作需确认")
        approval.set_variant("neutral")
        approval.setVisible(bool(status.approvals_enabled))

        mcp = self.capability_chips["mcp"]
        mcp.setText("MCP 已配置")
        mcp.set_variant("neutral")
        mcp.setVisible(bool(status.mcp_configured))

        sandbox = self.capability_chips["sandbox"]
        workspace_readable = self.workspace_card.available and self.workspace_card.workspace_kind != "none"
        sandbox.setText("Workspace 可读写" if self.workspace_card.workspace_kind == "managed"
                        else "Workspace 只读")
        sandbox.set_variant("neutral")
        sandbox.setVisible(workspace_readable)

        sandbox_exec = self.capability_chips["sandbox_exec"]
        sandbox_exec.setText("代码执行已配置")
        sandbox_exec.set_variant("neutral")
        sandbox_exec.setVisible(bool(status.sandbox_execution_configured))

        warnings = [STATUS_WARNINGS[code] for code in status.warnings if code in STATUS_WARNINGS]
        self.capability_warning_banner.set_description("\n".join(warnings))
        self.capability_warning_banner.setVisible(bool(warnings))
        self.model_unavailable_banner.setVisible(not self._model_configured)

    # --------------------------------------------------------- approvals
    def _render_approvals(self, approvals) -> None:
        while self.approvals_layout.count():
            item = self.approvals_layout.takeAt(0)
            if item.widget() is not None:
                stale = item.widget()
                stale.hide()
                stale.deleteLater()
                QCoreApplication.sendPostedEvents(stale, QEvent.Type.DeferredDelete)
        count = 0
        for row in sorted(approvals, key=lambda item: int(item["id"])):
            name = row.get("tool_name")
            action = APPROVAL_ACTIONS.get(name)
            if row.get("status") != "pending" or action is None:
                continue
            self.approvals_layout.addWidget(self._build_approval_card(row, action))
            count += 1
        self.approvals_container.setVisible(count > 0)

    def _build_approval_card(self, row, action: dict) -> QFrame:
        approval_id = int(row["id"])
        card = QFrame()
        card.setObjectName("AgentApprovalCard")
        card.setProperty("approval_id", approval_id)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(spacing.LG, spacing.MD, spacing.LG, spacing.MD)
        layout.setSpacing(spacing.SM)

        title = QLabel(action["title"])
        title.setObjectName("AgentApprovalActionTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)

        description = QLabel(action["description"])
        description.setObjectName("AgentApprovalDescription")
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        layout.addWidget(description)

        if row.get("tool_name") == "request_save_learning_note":
            layout.addLayout(self._build_note_preview(row))

        buttons = QHBoxLayout()
        buttons.setSpacing(spacing.SM)
        reject = SAButton(action["reject"], variant="subtle", size="small")
        reject.setObjectName("AgentApprovalReject")
        reject.setAutoDefault(False)
        reject.setDefault(False)
        reject.clicked.connect(
            lambda _checked=False, aid=approval_id: self.approval_reject_requested.emit(aid)
        )
        approve = SAButton(action["approve"], variant="primary", size="small")
        approve.setObjectName("AgentApprovalApprove")
        approve.setProperty("normal_text", action["approve"])
        approve.setAutoDefault(False)
        approve.setDefault(False)
        approve.clicked.connect(
            lambda _checked=False, aid=approval_id: self.approval_approve_requested.emit(aid)
        )
        buttons.addWidget(reject)
        buttons.addWidget(approve)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        for button in (reject, approve):
            button.setEnabled(not self._approval_busy)
        if self._approval_busy:
            approve.setText("正在执行…")
        return card

    def _build_note_preview(self, row) -> QVBoxLayout:
        preview = QVBoxLayout()
        preview.setContentsMargins(spacing.MD, spacing.SM, spacing.MD, spacing.SM)
        preview.setSpacing(spacing.XS)
        for caption_text, value_text, value_name in (
            ("笔记标题", str(row.get("note_title", "")), "AgentApprovalNoteValue"),
            ("内容预览", str(row.get("note_preview", "")), "AgentApprovalNotePreview"),
        ):
            caption = QLabel(caption_text)
            caption.setObjectName("AgentApprovalNoteCaption")
            caption.setTextFormat(Qt.TextFormat.PlainText)
            preview.addWidget(caption)
            value = QLabel(value_text)
            value.setObjectName(value_name)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            preview.addWidget(value)
        return preview

    # ------------------------------------------------------- interaction
    def _distance_from_bottom(self) -> int:
        bar = self.conversation_scroll.verticalScrollBar()
        return bar.maximum() - bar.value()

    def _on_scroll_value_changed(self, _value: int) -> None:
        if self._busy and not self._programmatic_scroll and not self._rebuilding:
            self._scrolled_away_during_busy = self._distance_from_bottom() > 48
        if self._distance_from_bottom() <= 48:
            self._unseen_reply = False
        self._update_latest_button()

    def _update_latest_button(self, *_args) -> None:
        away = self._distance_from_bottom() > 48
        self.latest_button.setText("新回复 ↓" if self._unseen_reply else "↓ 最新")
        self.latest_button.setVisible(away and self.current_session_id is not None)
        if away:
            viewport = self.conversation_scroll.viewport()
            self.latest_button.adjustSize()
            self.latest_button.move(max(0, viewport.width() - self.latest_button.width() - 12),
                                    max(0, viewport.height() - self.latest_button.height() - 12))
            self.latest_button.raise_()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched is self.conversation_scroll.viewport():
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self, self._update_latest_button)
            elif event.type() in (QEvent.Type.Wheel, QEvent.Type.TouchBegin,
                                  QEvent.Type.MouseButtonPress):
                self._clear_pending_scroll()
        return super().eventFilter(watched, event)

    def _on_latest_clicked(self) -> None:
        self._unseen_reply = False
        self._schedule_scroll(to_bottom=True)

    def _scroll_bottom(self) -> None:
        self._schedule_scroll(to_bottom=True)

    def _restore_scroll(self, value: int) -> None:
        self._schedule_scroll(value=max(0, int(value)))

    def closeEvent(self, event):  # noqa: N802 - Qt API
        # rangeChanged retains the Python handler and pending singleShots until
        # they fire. Disconnect before the widget/scrollbar is torn down.
        self._clear_pending_scroll()
        self.workspace_card.close()
        super().closeEvent(event)

    def _clear_pending_scroll(self) -> None:
        self._scroll_epoch += 1
        handler = self._pending_scroll_handler
        if handler is not None:
            try:
                self.conversation_scroll.verticalScrollBar().rangeChanged.disconnect(handler)
            except (RuntimeError, TypeError):
                pass
        self._pending_scroll_handler = None

    def _schedule_scroll(self, *, to_bottom: bool = False, value: int = 0,
                         widget: AgentMessageWidget | None = None) -> None:
        """Resolve targets only after layout; superseded Session targets are inert."""
        self._clear_pending_scroll()
        epoch = self._scroll_epoch
        session_id = self.current_session_id
        bar = self.conversation_scroll.verticalScrollBar()

        def _apply(*_args):
            if epoch != self._scroll_epoch or session_id != self.current_session_id:
                return
            self.conversation_body.layout().activate()
            if widget is not None:
                if widget.parent() is not self.conversation_body:
                    return
                top = widget.mapTo(self.conversation_body, QPoint(0, 0)).y()
                target = min(max(0, top - 16), bar.maximum())
            else:
                target = bar.maximum() if to_bottom else min(value, bar.maximum())
            self._programmatic_scroll = True
            try:
                bar.setValue(target)
            finally:
                self._programmatic_scroll = False
            self._update_latest_button()

        self._pending_scroll_handler = _apply
        # Range changes are the actual layout-completion signal. A fixed timer
        # can disconnect before a large conversation finishes relayout, leaving
        # a restore stuck at zero. The next turn/session switch or close clears
        # this handler; user scroll input clears it immediately below.
        bar.rangeChanged.connect(_apply)
        QTimer.singleShot(0, self, _apply)

    @staticmethod
    def _visible_message_signature(messages: list[dict]) -> tuple:
        """Only public user/assistant content participates in append detection."""
        visible = []
        for message in messages:
            role = message.get("role")
            if role == "user" or (role == "assistant" and not message.get("tool_calls_json")
                                   and str(message.get("content") or "").strip()):
                visible.append((role, message.get("id"), str(message.get("content") or "")))
        return tuple(visible)

    def clear_session(self, error: str = "") -> None:
        self.current_session_id = None
        self.current_task_id = None
        self.workspace_card.set_view(None)
        self.workspace_card.hide()
        self._approval_busy.clear()
        self._busy = False
        self._model_configured = False
        self._visible_messages = ()
        self._unseen_reply = False
        self._scrolled_away_during_busy = False
        self._clear_pending_scroll()
        self.latest_button.hide()
        self.task_context_card.set_metadata("", "", "")
        self.task_context_card.set_description("")
        self.task_context_card.setVisible(False)
        for key in self.capability_chips:
            self.capability_chips[key].setVisible(False)
        self.capability_warning_banner.set_description("")
        self.capability_warning_banner.hide()
        self.model_unavailable_banner.hide()
        self._clear_conversation()
        self._render_approvals(())
        self.composer.clear()
        self.composer.set_warning_visible(False)
        self._update_interaction_status()
        self.empty_hint.show()
        self.set_error(error)
        self._update_send_enabled()

    def set_approval_busy(self, approval_id: int, busy: bool) -> None:
        if busy:
            self._approval_busy.add(int(approval_id))
        else:
            self._approval_busy.discard(int(approval_id))
        self._update_approval_buttons()
        self.workspace_card.set_busy(self._busy or bool(self._approval_busy))
        self._update_interaction_status()
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
        if busy and not self._busy:
            self._scrolled_away_during_busy = self._distance_from_bottom() > 48
        self._busy = bool(busy)
        self.workspace_card.set_busy(self._busy or bool(self._approval_busy))
        self._update_approval_buttons()
        self._update_interaction_status()
        self._update_send_enabled()

    def _update_interaction_status(self) -> None:
        if self._approval_busy:
            text = "正在执行已确认操作…"
        elif self._busy:
            text = "学习助手正在思考…"
        else:
            text = ""
        self.interaction_status_label.setText(text)
        self.interaction_status_label.setVisible(bool(text))

    def set_error(self, message: str) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def _clear_conversation(self) -> None:
        while self.conversation_layout.count():
            item = self.conversation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Keep QObject parent ownership until DeferredDelete is delivered.
                # Detaching here creates an orphan top-level QWidget while scroll
                # callbacks/layout events may still be queued.
                if widget is self.empty_hint:
                    continue
                widget.hide()
                widget.deleteLater()
                # This UI rebuild must not expose stale children to findChild or
                # leave deferred top-level widgets alive until test teardown.
                QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
        self.conversation_layout.addWidget(self.empty_hint)
        self.conversation_layout.addStretch()

    def _add_bubble(self, speaker: str, text: str, role: str,
                    message_id: int | None = None) -> AgentMessageWidget | None:
        """Present one message. Rendering lives entirely in AgentMessageWidget."""
        if role not in (USER_ROLE, ASSISTANT_ROLE):
            return
        widget = AgentMessageWidget(role=role, text=text, speaker=speaker,
                                    message_id=message_id)
        # Insert before the trailing stretch.
        self.conversation_layout.insertWidget(
            max(0, self.conversation_layout.count() - 1), widget
        )
        return widget

    def _update_send_enabled(self) -> None:
        text = self.composer.text()
        too_long = len(text) > MAX_AGENT_INPUT_CHARS
        self.composer.set_warning_visible(too_long)
        self.composer.update_counter()
        enabled = (self.current_session_id is not None and self._model_configured
                   and not self._busy and not self._approval_busy)
        self.composer.set_input_enabled(enabled)
        self.composer.set_send_enabled(enabled and bool(text.strip()) and not too_long)

    def _send_current(self) -> None:
        if self.current_session_id is None:
            return
        text = self.composer.text()
        if (not text.strip() or len(text) > MAX_AGENT_INPUT_CHARS
                or self._busy or self._approval_busy or not self._model_configured):
            return
        self.composer.clear()
        self.send_requested.emit(self.current_session_id, text)

    # ----------------------------------------------------- compatibility
    @property
    def input_edit(self):
        return self.composer.input_edit

    @property
    def send_button(self):
        return self.composer.send_button

    @property
    def input_warning_label(self):
        return self.composer.warning_label
