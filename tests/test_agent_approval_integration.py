"""Today → Workspace → model request → explicit user click → canonical completion."""
from PySide6.QtWidgets import QFrame
from app.agent.approval.provider import AgentApprovalProvider
from app.agent.approval.service import AgentApprovalService
from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.ai.agent_protocol import AgentModelClient, ModelResponse, ModelToolCall
from app.database.agent_repository import AgentRepository
from app.database.agent_approval_repository import AgentApprovalRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService
from app.main import build_agent_approval_executor
from tests.test_agent_workspace_integration import _ConfiguredAI, _db_path

class CompletionModel(AgentModelClient):
    calls = 0
    def is_configured(self): return True
    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse("", (ModelToolCall("call1", "request_complete_current_task", "{}"),))
        return ModelResponse("我已发起请求，需要你在下方批准。")


def build_window(conn, task_service, date_service, sessions, model):
    from app.ui.main_window import MainWindow
    approvals = AgentApprovalService(AgentApprovalRepository(conn), task_service)
    def runtime_factory(fresh):
        tasks = TaskService(TaskRepository(fresh))
        return AgentRuntime(AgentSessionService(AgentRepository(fresh), tasks), model,
            approval_provider=AgentApprovalProvider(AgentApprovalService(
                AgentApprovalRepository(fresh), tasks)))
    window = MainWindow(task_service=task_service, date_service=date_service,
        today_provider=lambda: "2026-01-05", agent_session_service=sessions,
        agent_runtime_factory=runtime_factory, agent_approval_service=approvals,
        agent_approval_service_factory=build_agent_approval_executor,
        db_path=_db_path(conn), ai_config_service=_ConfiguredAI())
    return window, approvals


def request(qtbot, window, sessions, task):
    next(w for w in window._task_widgets if w.task().id == task.id).start_study_btn.click()
    page = window.agent_workspace_page
    session_id = page.current_session_id
    page.input_edit.setPlainText("请求完成任务")
    page.send_button.click()
    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=7000)
    qtbot.waitUntil(lambda: page.findChild(QFrame, "AgentApprovalCard") is not None, timeout=7000)
    return page, session_id


def test_approve_runs_only_after_user_click_and_refreshes_today(qtbot, conn, repo, task_service, date_service):
    task = repo.create(title="approval integration", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    model = CompletionModel()
    window, approvals = build_window(conn, task_service, date_service, sessions, model)
    qtbot.addWidget(window)
    page, session_id = request(qtbot, window, sessions, task)
    assert task_service.get_status(task.id) == "active"
    pending = approvals.list_pending_for_session(session_id)
    assert len(pending) == 1
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    assert not page.findChild(type(page.send_button), "AgentApprovalApprove").isEnabled()
    qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
    assert task_service.get_status(task.id) == "done"
    assert approvals.repository.get(pending[0]["id"])["status"] == "executed"
    assert approvals.list_pending_for_session(session_id) == []
    assert model.calls == 2
    assert sessions.get(session_id)["status"] == "active"
    assert len(sessions.messages(session_id)) == 4
    assert page.current_session_id == session_id
    window.close()


def test_reject_requires_no_model_call_or_task_mutation(qtbot, conn, repo, task_service, date_service):
    task = repo.create(title="rejection integration", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    model = CompletionModel()
    window, approvals = build_window(conn, task_service, date_service, sessions, model)
    qtbot.addWidget(window)
    page, sid = request(qtbot, window, sessions, task)
    pending = approvals.list_pending_for_session(sid)[0]
    page.findChild(type(page.send_button), "AgentApprovalReject").click()
    assert task_service.get_status(task.id) == "active"
    assert approvals.repository.get(pending["id"])["status"] == "rejected"
    assert approvals.list_pending_for_session(sid) == []
    assert model.calls == 2
    window.close()
