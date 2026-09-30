"""Workspace approval card uses static application text and explicit signals."""
from types import SimpleNamespace

import pytest

from PySide6.QtWidgets import QFrame, QLabel
from app.ui.agent_workspace_page import AgentWorkspacePage
from app.database.repository import TaskRepository


def test_main_window_approval_lookup_uses_only_public_service_api():
    from app.ui.main_window import MainWindow

    class ServiceOnly:
        def __init__(self):
            self.calls = []
        def get_pending_for_session(self, approval_id, session_id):
            self.calls.append((approval_id, session_id))
            return {"id": approval_id, "session_id": session_id, "status": "pending"}

    service = ServiceOnly()  # No .repository attribute: direct access would fail.
    window = SimpleNamespace(agent_workspace_page=SimpleNamespace(current_session_id=23),
                             agent_approval_service=service)
    assert MainWindow._approval_for_current_session(window, 17)["id"] == 17
    assert service.calls == [(17, 23)]


@pytest.mark.threaded
@pytest.mark.integration
def test_failed_worker_execution_never_reports_success(qtbot, conn, repo, task_service, date_service):
    from app.agent.session import AgentSessionService
    from app.database.agent_repository import AgentRepository
    from tests.test_agent_approval_integration import CompletionModel, build_window, request

    task = repo.create(title="approval fails", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window, approvals = build_window(conn, task_service, date_service, sessions, CompletionModel())
    qtbot.addWidget(window)
    page, session_id = request(qtbot, window, sessions, task)
    approval_id = approvals.list_pending_for_session(session_id)[0]["id"]
    task_service.mark_not_done(task.id, "not ready")
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
    assert approvals.repository.get(approval_id)["status"] == "failed"
    assert "任务已按你的批准标记为完成" not in window.statusBar().currentMessage()
    assert "任务状态已变化或执行未成功" in window.statusBar().currentMessage()
    assert task_service.get_status(task.id) == "not_done"
    window.close()


def test_three_pending_card_types_plain_text_preview_and_independent_labels(qtbot, conn):
    task = TaskRepository(conn).create(title="Task", scheduled_date="2026-09-15")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    rows = [
        {"id": 3, "status": "pending", "tool_name": "request_save_learning_note",
         "note_title": "<b>NOTE_PRIVATE_TITLE</b>",
         "note_preview": "<script>NOTE_PRIVATE_CONTENT</script>"},
        {"id": 2, "status": "pending", "tool_name": "request_start_assessment"},
        {"id": 1, "status": "pending", "tool_name": "request_complete_current_task"},
    ]
    page.load_session({"id": 1}, [], task, None, True, rows)
    cards = page.findChildren(QFrame, "AgentApprovalCard")
    assert [card.property("approval_id") for card in cards] == [1, 2, 3]
    buttons = [card.findChild(type(page.send_button), "AgentApprovalApprove").text()
               for card in cards]
    assert buttons == ["批准", "批准并开始验收", "批准保存"]
    assessment_text = " ".join(label.text() for label in cards[1].findChildren(QLabel))
    assert "亲自回答" in assessment_text and "Mastery" in assessment_text
    preview = cards[2].findChild(QLabel, "AgentApprovalNotePreview")
    from PySide6.QtCore import Qt
    assert preview.textFormat() == Qt.TextFormat.PlainText
    assert "<script>NOTE_PRIVATE_CONTENT</script>" in preview.text()
    page.set_approval_busy(2, True)
    assert all(not card.findChild(type(page.send_button), "AgentApprovalApprove").isEnabled()
               for card in cards)
    page.set_approval_busy(2, False)
    assert [card.findChild(type(page.send_button), "AgentApprovalApprove").text()
            for card in cards] == buttons


@pytest.mark.threaded
@pytest.mark.integration
def test_reenter_during_approval_disables_chat_and_other_decisions(
    qtbot, conn, repo, task_service, date_service
):
    import threading
    from app.agent.session import AgentSessionService
    from app.database.agent_repository import AgentRepository
    from app.main import build_agent_approval_executor
    from tests.test_agent_approval_integration import CompletionModel, build_window, request

    task = repo.create(title="In-flight approval", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window, approvals = build_window(conn, task_service, date_service, sessions, CompletionModel())
    qtbot.addWidget(window)
    page, sid = request(qtbot, window, sessions, task)
    entered, release = threading.Event(), threading.Event()
    def factory(fresh):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test worker timeout")
        return build_agent_approval_executor(fresh)
    window.agent_approval_service_factory = factory
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    assert entered.wait(3)
    window._on_agent_back()
    window._on_start_study(task.id)
    assert page.current_session_id == sid
    assert not page.input_edit.isEnabled()
    assert not page.findChild(type(page.send_button), "AgentApprovalReject").isEnabled()
    window._on_agent_send(sid, "forbidden during approval")
    assert len(sessions.messages(sid)) == 4
    release.set()
    qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
    assert approvals.list_pending_for_session(sid) == []
    window.close()


def test_stale_workspace_does_not_open_assessment_dialog(qtbot, conn, repo, task_service, date_service):
    from app.agent.session import AgentSessionService
    from app.database.agent_repository import AgentRepository
    from tests.test_agent_approval_integration import CompletionModel, build_window
    task = repo.create(title="Task", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window, _approval = build_window(conn, task_service, date_service, sessions, CompletionModel())
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    sid = window.agent_workspace_page.current_session_id
    window._on_agent_back()
    opened = []
    window._open_assessment_dialog = opened.append
    window._on_agent_approval_finished(123, sid,
        {"status":"executed", "action":"start_assessment", "result":{"attempt":{"id": 1}}}, None)
    assert opened == []
    assert "可从今日继续" in window.statusBar().currentMessage()
    window.close()


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
    assert not page.send_button.isEnabled()  # serialize approval execution and Agent turn
    page.load_session({"id": 1}, [], task, None, True, [{"id": 7, "status": "executed", "tool_name": "request_complete_current_task"}])
    assert not page.approvals_container.isVisible()
