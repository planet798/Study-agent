"""Workspace rendering, navigation semantics, input/busy and TaskWidget actions."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel

from app.database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_DONE, STATUS_NOT_DONE
from app.ui.agent_message_widget import ASSISTANT_ROLE, USER_ROLE, AgentMessageWidget
from app.ui.agent_workspace_page import AgentWorkspacePage
from app.ui.app_shell import PAGE_SPECS, PageKey
from app.ui.task_widget import TaskWidget


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


def _message_widgets(page):
    return page.findChildren(AgentMessageWidget)


def _message_texts(page):
    return [widget.raw_text for widget in _message_widgets(page)]


def _task(repo, *, status=STATUS_ACTIVE, **kwargs):
    task = repo.create(
        title="Learn attention", description="Understand attention",
        scheduled_date="2026-01-05", estimated_minutes=35,
        source="generated", **kwargs,
    )
    if status != STATUS_ACTIVE:
        task = repo.update(task.id, status=status)
    return task


def test_workspace_is_internal_not_a_sidebar_page():
    assert [spec.key for spec in PAGE_SPECS] == [
        PageKey.TODAY, PageKey.ROUTES, PageKey.PRACTICE, PageKey.SETTINGS,
    ]
    assert not any(str(spec.key).lower().find("agent") >= 0 for spec in PAGE_SPECS)
    page = AgentWorkspacePage()
    assert page.objectName() == "AgentWorkspacePage"


def test_workspace_loads_task_history_and_hides_tool_protocol(qtbot, repo):
    task = _task(repo, learning_activity_kind="theory")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session(
        session={"id": 8, "task_id": task.id},
        messages=[
            {"role": "user", "content": "Explain this", "tool_calls_json": ""},
            {"role": "assistant", "content": "internal call", "tool_calls_json": '[{"id":"c"}]'},
            {"role": "tool", "content": '{"secret":"internal"}', "tool_name": "get_task_context"},
            {"role": "assistant", "content": "<script>plain response</script>", "tool_calls_json": ""},
        ],
        task=task,
        route_name="R1 Route",
        model_configured=True,
    )

    assert page.current_session_id == 8
    assert not hasattr(page, "task_title_label")  # no duplicate title in Workspace
    assert page.task_context_card.tag_texts() == ("R1 Route", "理论", "35 分钟")
    assert page.task_context_card.description_label.text() == "Understand attention"
    widgets = _message_widgets(page)
    assert [widget.raw_text for widget in widgets] == [
        "Explain this", "<script>plain response</script>",
    ]
    assert [widget.role for widget in widgets] == [USER_ROLE, ASSISTANT_ROLE]
    user_body = widgets[0].plain_view
    assert user_body.toPlainText() == "Explain this"
    assert widgets[1].markdown_view is not None
    assert "internal" not in " ".join(widget.raw_text for widget in widgets)
    assert page.empty_hint.isHidden()
    assert page.input_edit.isEnabled()


def test_workspace_still_renders_full_history_when_session_memory_exists(
    qtbot, conn, repo, task_service
):
    from app.agent.session import AgentSessionService
    from app.database.agent_memory_repository import AgentMemoryRepository
    from app.database.agent_repository import AgentRepository

    task = _task(repo)
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    first = sessions.append_user_message(session["id"], "early question")
    final = sessions.append_assistant_message(session["id"], "early answer")
    sessions.append_user_message(session["id"], "latest question")
    AgentMemoryRepository(conn).upsert(
        session["id"], final["id"], 2, "compressed early conversation"
    )

    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session(
        session=sessions.get(session["id"]),
        messages=sessions.messages(session["id"]),
        task=task, route_name=None, model_configured=True,
    )

    assert _message_texts(page) == [
        "early question", "early answer", "latest question",
    ]
    assert first["id"] < final["id"]


def test_workspace_empty_history_does_not_send_automatically(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    sent = []
    page.send_requested.connect(lambda *args: sent.append(args))
    page.load_session({"id": 1}, [], task, None, model_configured=True)
    assert not page.empty_hint.isHidden()
    assert sent == []
    assert page.task_context_card.tag_texts() == ("未分类", "学习活动", "35 分钟")


def test_workspace_busy_and_model_unavailable_states(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, model_configured=False)
    assert not page.model_unavailable_banner.isHidden()
    assert not page.input_edit.isEnabled()
    assert not page.send_button.isEnabled()

    page.load_session({"id": 2}, [], task, None, model_configured=True)
    page.input_edit.setPlainText("hello")
    assert page.send_button.isEnabled()
    page.set_busy(True)
    assert not page.interaction_status_label.isHidden()
    assert "学习助手正在思考" in page.interaction_status_label.text()
    assert not page.input_edit.isEnabled()
    assert not page.send_button.isEnabled()
    page.set_busy(False)
    assert page.interaction_status_label.isHidden()
    assert page.input_edit.isEnabled()
    assert page.send_button.isEnabled()


def test_workspace_send_emits_plain_text_and_rejects_blank(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 24}, [], task, None, model_configured=True)
    sent = []
    page.send_requested.connect(lambda *args: sent.append(args))

    page.input_edit.setPlainText("   \n")
    assert not page.send_button.isEnabled()
    page.input_edit.setPlainText("  Teach me  ")
    qtbot.mouseClick(page.send_button, Qt.MouseButton.LeftButton)
    assert sent == [(24, "  Teach me  ")]
    assert page.input_edit.toPlainText() == ""


def test_workspace_ctrl_enter_length_limit_and_clear(qtbot, repo):
    from app.ui.agent_workspace_page import MAX_AGENT_INPUT_CHARS
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 10}, [], task, None, model_configured=True)
    page.show()
    sent, settings = [], []
    page.send_requested.connect(lambda *args: sent.append(args))
    page.settings_requested.connect(lambda: settings.append(True))
    page.input_edit.setPlainText("one")
    qtbot.keyClick(page.input_edit, Qt.Key.Key_Return)
    assert page.input_edit.toPlainText().count("\n") == 1 and not sent
    page.input_edit.setPlainText("x" * (MAX_AGENT_INPUT_CHARS + 1))
    assert page.input_warning_label.isVisible() and not page.send_button.isEnabled()
    qtbot.keyClick(page.input_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent and len(page.input_edit.toPlainText()) == MAX_AGENT_INPUT_CHARS + 1
    page.input_edit.setPlainText("two")
    qtbot.keyClick(page.input_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [(10, "two")]
    page.load_session({"id": 11}, [], task, None, model_configured=False)
    assert page.settings_button.isVisible()
    page.settings_button.click()
    assert settings == [True]
    assert not page.send_button.isEnabled()
    page.clear_session("安全错误")
    assert page.current_session_id is None
    assert not page.task_context_card.description_label.isVisible()
    assert not page.composer.input_edit.toPlainText()
    assert _message_widgets(page) == []
    assert page.error_label.text() == "安全错误"


def test_workspace_status_row_scroll_and_busy_persistence(qtbot, repo):
    from app.agent.status import AgentCapabilityStatus
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(720, 480)
    page.show()
    messages = []
    for i in range(35):
        messages += [{"role": "user", "content": f"question {i}", "tool_calls_json": ""},
                     {"role": "assistant", "content": f"answer {i}", "tool_calls_json": ""}]
    status = AgentCapabilityStatus(True, mcp_configured=True, sandbox_configured=True,
        approvals_enabled=True, warnings=("mcp_config_invalid",))
    page.load_session({"id": 12}, messages, task, None, True,
                      capability_status=status, turn_busy=True,
                      approvals=[{"id": 7, "status": "pending", "tool_name": "request_complete_current_task"}])
    qtbot.waitUntil(lambda: page.conversation_scroll.verticalScrollBar().value() ==
                    page.conversation_scroll.verticalScrollBar().maximum(), timeout=2000)
    assert page.capability_chips["mcp"].isVisible()
    assert page.capability_chips["mcp"].text() == "MCP 已配置"
    assert page.capability_chips["approval"].isVisible()
    assert page.capability_chips["approval"].text() == "写操作需确认"
    assert "配置无效" in page.capability_warning_banner.description()
    assert not page.input_edit.isEnabled()
    assert not page.findChild(type(page.send_button), "AgentApprovalApprove").isEnabled()
    page.load_session({"id": 12}, messages, task, None, True,
                      capability_status=status, approval_busy=(7,))
    assert not page.input_edit.isEnabled()
    assert page.findChild(type(page.send_button), "AgentApprovalApprove").text() == "正在执行…"
    page.set_approval_busy(7, False)
    assert page.input_edit.isEnabled()


def test_task_widget_agent_action_priority_and_fallback(qtbot, repo):
    task = _task(repo, topic_id=1)
    fallback = TaskWidget(task)
    qtbot.addWidget(fallback)
    assert not hasattr(fallback, "start_study_btn")
    assert fallback.complete_btn.variant() == "primary"

    agent = TaskWidget(task, agent_enabled=True, agent_label="继续学习")
    qtbot.addWidget(agent)
    assert agent.start_study_btn.text() == "继续学习"
    assert agent.start_study_btn.variant() == "primary"
    assert agent.complete_btn.variant() == "secondary"
    assert agent.assessment_btn.variant() == "secondary"
    requested = []
    agent.start_study_requested.connect(requested.append)
    qtbot.mouseClick(agent.start_study_btn, Qt.MouseButton.LeftButton)
    assert requested == [task.id]


def test_done_cancelled_and_not_done_tasks_get_no_agent_entry(qtbot, repo):
    for status in (STATUS_DONE, STATUS_CANCELLED, STATUS_NOT_DONE):
        task = _task(repo, status=status)
        widget = TaskWidget(task, agent_enabled=True)
        qtbot.addWidget(widget)
        assert not hasattr(widget, "start_study_btn")


# ---------------------------------------------------------------- UX-2 ----


def test_workspace_does_not_duplicate_task_title(qtbot, repo):
    from PySide6.QtWidgets import QLabel
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, "R1 Route", True)
    assert not hasattr(page, "task_title_label")
    assert all(label.text() != task.title for label in page.findChildren(QLabel))


def test_task_context_card_tags_and_optional_description(qtbot, repo):
    task = _task(repo, learning_activity_kind="theory")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, "R2 LLM Post-Training", True)
    assert page.task_context_card.tag_texts() == ("R2 LLM Post-Training", "理论", "35 分钟")
    assert page.task_context_card.description_label.text() == "Understand attention"
    assert not page.task_context_card.description_label.isHidden()

    empty = repo.create(
        title="No description", scheduled_date="2026-01-05", source="generated",
        estimated_minutes=20,
    )
    page.load_session({"id": 2}, [], empty, None, True)
    assert page.task_context_card.tag_texts() == ("未分类", "学习活动", "20 分钟")
    assert page.task_context_card.description_label.isHidden()
    # Card itself is still present (metadata is always useful).
    assert not page.task_context_card.isHidden()


def test_capability_chips_are_truthful_and_drop_architecture_jargon(qtbot, repo):
    from app.agent.status import AgentCapabilityStatus
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    status = AgentCapabilityStatus(
        True, mcp_configured=True, sandbox_configured=True,
        sandbox_execution_configured=True, approvals_enabled=True,
    )
    page.load_session({"id": 1}, [], task, None, True, capability_status=status)
    chips = page.capability_chips
    assert chips["ai"].text() == "AI 已配置"
    assert chips["approval"].text() == "写操作需确认"
    assert chips["mcp"].text() == "MCP 已配置"
    assert chips["sandbox"].text() == "Sandbox 已启用"
    assert chips["sandbox_exec"].text() == "Sandbox 执行已配置"
    assert all(not chip.isHidden() for chip in chips.values())
    joined = " ".join(chip.text() for chip in chips.values())
    assert "在线" not in joined
    assert "应用数据只读" not in joined


def test_capability_chips_hide_unconfigured_optional_features(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True)
    assert page.capability_chips["ai"].isHidden() is False
    assert page.capability_chips["mcp"].isHidden() is True
    assert page.capability_chips["sandbox"].isHidden() is True
    assert page.capability_chips["approval"].isHidden() is True


def test_same_session_reload_preserves_unsent_draft(qtbot, repo):
    from app.agent.status import AgentCapabilityStatus
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True)
    page.input_edit.setPlainText("draft that must survive a status refresh")
    page.load_session(
        {"id": 1}, [], task, None, True,
        capability_status=AgentCapabilityStatus(True, mcp_configured=True),
        approvals=[{"id": 5, "status": "pending", "tool_name": "request_complete_current_task"}],
    )
    assert page.input_edit.toPlainText() == "draft that must survive a status refresh"
    assert page.capability_chips["mcp"].isHidden() is False


def test_session_switch_clears_unsent_draft(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True)
    page.input_edit.setPlainText("session A draft")
    page.load_session({"id": 2}, [], task, None, True)
    assert page.input_edit.toPlainText() == ""
    page.input_edit.setPlainText("session B draft")
    page.load_session({"id": 1}, [], task, None, True)
    assert page.input_edit.toPlainText() == ""


def test_approval_section_hidden_until_pending(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True)
    assert page.approvals_container.isHidden()
    assert page.approvals_layout.count() == 0

    page.load_session(
        {"id": 1}, [], task, None, True,
        approvals=[{"id": 7, "status": "pending", "tool_name": "request_complete_current_task"}],
    )
    assert not page.approvals_container.isHidden()
    assert page.approval_section_title.text() == "待确认操作"
    assert "确认后才会执行" in page.approval_section_caption.text()
    card = page.findChild(QFrame, "AgentApprovalCard")
    assert card is not None
    assert card.findChild(QLabel, "AgentApprovalActionTitle").text() == "完成任务"
    assert card.findChild(type(page.send_button), "AgentApprovalApprove").text() == "批准"


def test_unified_busy_status_for_turn_and_approval(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True)
    assert page.interaction_status_label.isHidden()
    page.set_busy(True)
    assert page.interaction_status_label.text() == "学习助手正在思考…"
    page.set_busy(False)
    page.set_approval_busy(9, True)
    assert page.interaction_status_label.text() == "正在执行已确认操作…"
    page.set_approval_busy(9, False)
    assert page.interaction_status_label.isHidden()


def test_narrow_viewport_has_no_horizontal_overflow(qtbot, repo):
    from app.agent.status import AgentCapabilityStatus
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(380, 640)
    page.show()
    status = AgentCapabilityStatus(
        True, mcp_configured=True, sandbox_configured=True,
        sandbox_execution_configured=True, approvals_enabled=True,
    )
    page.load_session(
        {"id": 1},
        [{"role": "user", "content": "question " + "x" * 200, "tool_calls_json": ""},
         {"role": "assistant", "content": "## Answer\\n\\n" + "y" * 400, "tool_calls_json": ""}],
        task,
        "R2 LLM Post-Training / a very long route name that must wrap",
        True,
        capability_status=status,
        approvals=[{"id": 7, "status": "pending", "tool_name": "request_save_learning_note",
                    "note_title": "n" * 200, "note_preview": "p" * 400}],
    )
    qtbot.waitExposed(page)
    qtbot.waitUntil(lambda: page.conversation_scroll.viewport().width() > 0)
    assert not page.conversation_scroll.horizontalScrollBar().isVisible()
    assert page.composer.send_button.isVisible()
    assert page.composer.width() <= page.width()
    assert page.capability_chips_widget.width() <= page.width()
    card = page.findChild(QFrame, "AgentApprovalCard")
    assert card.width() <= page.conversation_scroll.viewport().width() + 40


def test_same_session_message_refresh_keeps_scroll_position(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    messages = []
    for index in range(40):
        messages.append({"role": "user", "content": f"question {index}", "tool_calls_json": ""})
        messages.append({"role": "assistant", "content": f"answer {index}", "tool_calls_json": ""})
    page.resize(720, 480)
    page.show()
    page.load_session({"id": 1}, messages, task, None, True)
    bar = page.conversation_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    target = bar.maximum() // 2
    bar.setValue(target)

    page.load_session(
        {"id": 1}, messages, task, None, True,
        approvals=[{"id": 1, "status": "pending", "tool_name": "request_complete_current_task"}],
    )
    qtbot.waitUntil(lambda: abs(bar.value() - target) <= 2)
    assert abs(bar.value() - target) <= 2
    # A genuinely new message still scrolls to the bottom.
    page.load_session(
        {"id": 1},
        messages + [{"role": "user", "content": "newest", "tool_calls_json": ""}],
        task, None, True,
    )
    qtbot.waitUntil(lambda: bar.value() == bar.maximum())
    assert bar.value() == bar.maximum()
