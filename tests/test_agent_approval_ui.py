"""Workspace approval card uses static application text and explicit signals."""
from PySide6.QtWidgets import QFrame, QLabel
from app.ui.agent_workspace_page import AgentWorkspacePage
from app.database.repository import TaskRepository


def test_pending_card_static_buttons_busy_and_chat(qtbot, conn):
    task = TaskRepository(conn).create(title="Task", scheduled_date="2026-09-15")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    approvals = [
        {"id": 7, "status": "pending", "tool_name": "request_complete_current_task",
         "description": "UNTRUSTED_MODEL_SENTINEL"},
        {"id": 8, "status": "pending", "tool_name": "untrusted_write"},
    ]
    page.load_session({"id": 1}, [], task, None, True, approvals)
    cards = page.findChildren(QFrame, "AgentApprovalCard")
    assert len(cards) == 1
    assert "UNTRUSTED_MODEL_SENTINEL" not in cards[0].findChildren(QLabel)[0].text()
    approves, rejects = [], []
    page.approval_approve_requested.connect(approves.append)
    page.approval_reject_requested.connect(rejects.append)
    page.input_edit.setPlainText("I can keep chatting")
    assert page.send_button.isEnabled()
    page.findChild(type(page.send_button), "AgentApprovalReject").click()
    assert rejects == [7]
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    assert approves == [7]
    page.set_approval_busy(7, True)
    button = page.findChild(type(page.send_button), "AgentApprovalApprove")
    assert not button.isEnabled() and button.text() == "正在执行…"
    assert page.send_button.isEnabled()
    page.load_session({"id": 1}, [], task, None, True, [{"id": 7, "status": "executed", "tool_name": "request_complete_current_task"}])
    assert not page.approvals_container.isVisible()
