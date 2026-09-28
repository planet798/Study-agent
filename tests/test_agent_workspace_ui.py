"""Workspace rendering, navigation semantics, input/busy and TaskWidget actions."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from app.database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_DONE, STATUS_NOT_DONE
from app.ui.agent_workspace_page import AgentWorkspacePage
from app.ui.app_shell import PAGE_SPECS, PageKey
from app.ui.task_widget import TaskWidget


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


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
    assert page.task_title_label.text() == "Learn attention"
    assert page.task_description_label.text() == "Understand attention"
    assert page.task_meta_label.text() == "R1 Route · 理论 · 35 分钟"
    bodies = page.findChildren(QLabel, "AgentMessageText")
    assert [label.text() for label in bodies] == [
        "Explain this", "<script>plain response</script>",
    ]
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in bodies)
    assert "internal" not in " ".join(label.text() for label in bodies)
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

    assert [label.text() for label in page.findChildren(QLabel, "AgentMessageText")] == [
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
    assert page.task_meta_label.text() == "未分类 · 学习活动 · 35 分钟"


def test_workspace_busy_and_model_unavailable_states(qtbot, repo):
    task = _task(repo)
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, model_configured=False)
    assert not page.model_unavailable_label.isHidden()
    assert not page.input_edit.isEnabled()
    assert not page.send_button.isEnabled()

    page.load_session({"id": 2}, [], task, None, model_configured=True)
    page.input_edit.setPlainText("hello")
    assert page.send_button.isEnabled()
    page.set_busy(True)
    assert not page.busy_label.isHidden()
    assert not page.input_edit.isEnabled()
    assert not page.send_button.isEnabled()
    page.set_busy(False)
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
    assert page.task_title_label.text() == ""
    assert page.findChildren(QLabel, "AgentMessageText") == []
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
    assert "MCP 已配置" in page.status_label.text()
    assert "写操作需批准" in page.status_label.text()
    assert "配置无效" in page.capability_warning_label.text()
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
