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


def test_assessment_approval_opens_existing_dialog_without_mastery_change(qtbot, conn, repo, task_service, date_service):
    import json
    from app.database.assessment_repository import AssessmentRepository
    from app.services.assessment_service import AssessmentService
    from app.ui.assessment_dialog import AssessmentDialog
    from tests.test_assessment_flow import FakeClient, QUESTIONS_CONTENT
    from app.ui.main_window import MainWindow

    assessment_repo = AssessmentRepository(conn)
    kp = assessment_repo.create_knowledge_point("Approval KP")
    task = repo.create(title="Assessment task", scheduled_date="2026-01-05",
                       source="generated", knowledge_point_id=kp["id"])
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    approvals = AgentApprovalService(AgentApprovalRepository(conn), task_service)
    class AssessmentModel(AgentModelClient):
        calls = 0
        def is_configured(self): return True
        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse("", (ModelToolCall("a1", "request_start_assessment", "{}"),))
            return ModelResponse("验收申请等待批准")
    model = AssessmentModel()
    def runtime_factory(fresh):
        tasks = TaskService(TaskRepository(fresh))
        return AgentRuntime(AgentSessionService(AgentRepository(fresh), tasks), model,
            approval_provider=AgentApprovalProvider(AgentApprovalService(
                AgentApprovalRepository(fresh), tasks)))
    clients = []
    worker_db_path = _db_path(conn)
    def approval_factory(fresh):
        executor = build_agent_approval_executor(fresh, db_path=worker_db_path)
        client = FakeClient(responses=[QUESTIONS_CONTENT])
        clients.append(client)
        executor.assessment_service = AssessmentService(client, AssessmentRepository(fresh))
        return executor
    window = MainWindow(task_service=task_service, date_service=date_service,
        today_provider=lambda: "2026-01-05", agent_session_service=sessions,
        agent_runtime_factory=runtime_factory, agent_approval_service=approvals,
        agent_approval_service_factory=approval_factory, db_path=_db_path(conn),
        ai_config_service=_ConfiguredAI(),
        assessment_service=AssessmentService(FakeClient(), assessment_repo),
        assessment_service_factory=lambda fresh: AssessmentService(FakeClient(), AssessmentRepository(fresh)))
    qtbot.addWidget(window)
    dialogs = []
    def show_dialog(attempt):
        dialog = AssessmentDialog(window.assessment_service, attempt, window.current_date,
                                  parent=window, service_factory=window.assessment_service_factory,
                                  db_path=window.db_path)
        dialogs.append(dialog)
        dialog.show()
    window._open_assessment_dialog = show_dialog
    page, sid = request(qtbot, window, sessions, task)
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
    assert dialogs, (window.statusBar().currentMessage(), approvals.repository.list_pending_for_session(sid),
                     [dict(row) for row in conn.execute("SELECT * FROM agent_approval_requests")])
    assert isinstance(dialogs[0], AssessmentDialog)
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 1
    assert assessment_repo.get_knowledge_point(kp["id"])["mastery_estimate"] == 0
    assert len(clients[0].calls) == 1
    assert model.calls == 2
    dialogs[0].close()
    window.close()


def test_note_approval_shows_safe_preview_then_creates_only_note_outcome(qtbot, conn, repo, task_service, date_service):
    import json
    from app.database.skill_repository import LearningOutcomeRepository
    task = repo.create(title="Note task", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    class NoteModel(AgentModelClient):
        calls = 0
        def is_configured(self): return True
        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse("", (ModelToolCall("n1", "request_save_learning_note",
                    json.dumps({"title":" <b>Note title</b> ",
                                "content":" <script>private note content</script> "})),))
            return ModelResponse("笔记申请等待批准")
    model = NoteModel()
    window, approvals = build_window(conn, task_service, date_service, sessions, model)
    qtbot.addWidget(window)
    page, sid = request(qtbot, window, sessions, task)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    preview = page.findChild(QLabel, "AgentApprovalNotePreview")
    assert preview.textFormat() == Qt.TextFormat.PlainText
    assert "<script>private note content</script>" in preview.text()
    assert conn.execute("SELECT COUNT(*) FROM learning_outcomes").fetchone()[0] == 0
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
    notes = [dict(row) for row in conn.execute("SELECT * FROM learning_outcomes")]
    assert len(notes) == 1
    assert notes[0]["kind"] == "note" and notes[0]["task_id"] is None
    assert notes[0]["git_commit"] == ""
    assert task_service.get_status(task.id) == "active"
    assert conn.execute("SELECT COUNT(*) FROM capability_evidence").fetchone()[0] == 0
    assert model.calls == 2
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
