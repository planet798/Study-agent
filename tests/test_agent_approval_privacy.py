"""Approval audit records no prompt, user text, Task description or model arguments."""
import json
from app.agent.approval.service import AgentApprovalService
from app.database.agent_approval_repository import AgentApprovalRepository
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


def test_approval_tables_store_only_identifiers_and_finite_codes(conn):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("PRIVATE_TASK_SENTINEL", scheduled_date="2026-09-15",
                                    description="PRIVATE_DESCRIPTION_SENTINEL")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    agent.add_message(session["id"], "user", "PRIVATE_USER_SENTINEL")
    message = agent.add_message(session["id"], "assistant", "PRIVATE_MODEL_EXPLANATION_SENTINEL",
        tool_calls_json='[{"id":"call-1","name":"request_complete_current_task","arguments":"{}"}]')
    approval = AgentApprovalService(AgentApprovalRepository(conn), task_service)
    pending = approval.request_complete_current_task(session["id"], task.id,
                                                       message["id"], "call-1")
    approval.reject(pending["approval_id"])
    dump = json.dumps({table:[dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        for table in ("agent_approval_requests", "agent_approval_events")})
    for marker in ("PRIVATE_TASK_SENTINEL", "PRIVATE_DESCRIPTION_SENTINEL",
                   "PRIVATE_USER_SENTINEL", "PRIVATE_MODEL_EXPLANATION_SENTINEL"):
        assert marker not in dump


def test_assessment_questions_never_enter_approval_audit_tables(conn):
    from app.database.assessment_repository import AssessmentRepository
    from app.services.assessment_service import AssessmentService
    from tests.test_assessment_flow import FakeClient, QUESTIONS_CONTENT
    repo = AssessmentRepository(conn)
    kp = repo.create_knowledge_point("Private KP")
    tasks = TaskService(TaskRepository(conn))
    task = TaskRepository(conn).create("Assess", scheduled_date="2026-09-15",
                                      knowledge_point_id=kp["id"])
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    assistant = agent.add_message(session["id"], "assistant", "",
        tool_calls_json='[{"id":"a1","name":"request_start_assessment","arguments":"{}"}]')
    response = QUESTIONS_CONTENT.replace("解释 requires_grad=True 的作用", "ASSESSMENT_QUESTION_SECRET")
    service = AgentApprovalService(AgentApprovalRepository(conn), tasks,
        assessment_service=AssessmentService(FakeClient(responses=[response]), repo))
    pending = service.request_start_assessment(session["id"], task.id, assistant["id"], "a1")
    result = service.approve_and_execute(pending["approval_id"])
    assert result["status"] == "executed"
    assert "ASSESSMENT_QUESTION_SECRET" in result["result"]["attempt"]["questions_json"]
    dump = json.dumps({table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                       for table in ("agent_approval_requests", "agent_approval_events",
                                     "agent_turn_traces", "agent_trace_events",
                                     "agent_turn_evaluations")})
    assert "ASSESSMENT_QUESTION_SECRET" not in dump


def test_note_payload_is_not_in_approval_trace_or_evaluation(conn):
    from app.agent.approval.provider import AgentApprovalProvider
    from app.agent.runtime import AgentRuntime
    from app.agent.session import AgentSessionService
    from app.agent.trace.service import AgentTraceService
    from app.database.agent_trace_repository import AgentTraceRepository
    from app.database.agent_evaluation_repository import AgentEvaluationRepository
    from app.ai.agent_protocol import AgentModelClient, ModelResponse, ModelToolCall
    class NoteModel(AgentModelClient):
        calls = 0
        def is_configured(self): return True
        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse("", (ModelToolCall("note-call", "request_save_learning_note",
                    json.dumps({"title": "NOTE_PRIVATE_TITLE_SENTINEL",
                                "content": "NOTE_PRIVATE_CONTENT_SENTINEL"})),))
            return ModelResponse("等待用户批准")
    tasks = TaskService(TaskRepository(conn))
    task = tasks.create_task("Study", scheduled_date="2026-09-15")
    sessions = AgentSessionService(AgentRepository(conn), tasks)
    session = sessions.start_or_resume(task.id)
    approval = AgentApprovalService(AgentApprovalRepository(conn), tasks)
    runtime = AgentRuntime(sessions, NoteModel(),
        approval_provider=AgentApprovalProvider(approval),
        trace_service=AgentTraceService(AgentTraceRepository(conn), AgentEvaluationRepository(conn)))
    result = runtime.send_message(session["id"], "Draft a note")
    assert result.trace_id > 0
    assert approval.list_pending_views_for_session(session["id"])[0]["note_title"] == "NOTE_PRIVATE_TITLE_SENTINEL"
    tables = ("agent_approval_requests", "agent_approval_events", "agent_turn_traces",
              "agent_trace_events", "agent_turn_evaluations")
    dump = json.dumps({table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                       for table in tables})
    for secret in ("NOTE_PRIVATE_TITLE_SENTINEL", "NOTE_PRIVATE_CONTENT_SENTINEL"):
        assert secret not in dump
    assert "NOTE_PRIVATE_CONTENT_SENTINEL" in sessions.messages(session["id"])[1]["tool_calls_json"]


def test_approval_trace_and_evaluation_do_not_copy_tool_result_or_user_decision(conn):
    from app.agent.approval.provider import AgentApprovalProvider
    from app.agent.runtime import AgentRuntime
    from app.agent.session import AgentSessionService
    from app.agent.trace.service import AgentTraceService
    from app.database.agent_trace_repository import AgentTraceRepository
    from app.database.agent_evaluation_repository import AgentEvaluationRepository
    from app.ai.agent_protocol import AgentModelClient, ModelResponse, ModelToolCall
    class Model(AgentModelClient):
        calls = 0
        def is_configured(self): return True
        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse("", (ModelToolCall("call-private", "request_complete_current_task", "{}"),))
            return ModelResponse("PRIVATE_MODEL_FINAL_SENTINEL")
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("PRIVATE_TASK_TITLE_SENTINEL", scheduled_date="2026-09-15")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    repository = AgentApprovalRepository(conn)
    runtime = AgentRuntime(sessions, Model(), system_prompt="PRIVATE_SYSTEM_SENTINEL",
        approval_provider=AgentApprovalProvider(AgentApprovalService(repository, task_service)),
        trace_service=AgentTraceService(AgentTraceRepository(conn), AgentEvaluationRepository(conn)))
    runtime.send_message(session["id"], "PRIVATE_USER_SENTINEL")
    approval = repository.list_pending_for_session(session["id"])[0]
    AgentApprovalService(repository, task_service).reject(approval["id"])
    tables = ("agent_approval_requests", "agent_approval_events", "agent_turn_traces",
              "agent_trace_events", "agent_turn_evaluations")
    dump = json.dumps({table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                       for table in tables})
    for secret in ("PRIVATE_TASK_TITLE_SENTINEL", "PRIVATE_MODEL_FINAL_SENTINEL",
                   "PRIVATE_SYSTEM_SENTINEL", "PRIVATE_USER_SENTINEL"):
        assert secret not in dump
