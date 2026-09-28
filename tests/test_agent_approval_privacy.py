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
