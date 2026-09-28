"""Model request writes only approval metadata; canonical execution awaits UI."""
import json
from app.agent.approval.provider import AgentApprovalProvider
from app.agent.approval.service import AgentApprovalService
from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.agent.trace.service import AgentTraceService
from app.database.agent_repository import AgentRepository
from app.database.agent_approval_repository import AgentApprovalRepository
from app.database.agent_trace_repository import AgentTraceRepository
from app.database.agent_evaluation_repository import AgentEvaluationRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService
from app.ai.agent_protocol import AgentModelClient, ModelResponse, ModelToolCall

class Model(AgentModelClient):
    def __init__(self):
        self.calls = 0
    def is_configured(self): return True
    def complete(self, request):
        self.calls += 1
        if self.calls % 2:
            return ModelResponse("", (ModelToolCall(f"call{self.calls}", "request_complete_current_task", "{}"),),
                                 usage={"prompt_tokens":2,"completion_tokens":1,"total_tokens":3})
        return ModelResponse("等待用户批准", usage={"prompt_tokens":2,"completion_tokens":1,"total_tokens":3})

def test_pending_tool_protocol_trace_and_duplicate_pending(conn):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("Learn", scheduled_date="2026-09-15")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    approvals = AgentApprovalService(AgentApprovalRepository(conn), task_service)
    trace = AgentTraceRepository(conn)
    model = Model()
    runtime = AgentRuntime(sessions, model, approval_provider=AgentApprovalProvider(approvals),
        trace_service=AgentTraceService(trace, AgentEvaluationRepository(conn)))
    untouched_tables = ("knowledge_points", "capability_evidence", "learning_outcomes",
                        "assessment_attempts", "practice_projects")
    before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in untouched_tables}
    first = runtime.send_message(session["id"], "Please request completion")
    second = runtime.send_message(session["id"], "Request it again")
    assert before == {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in untouched_tables}
    assert task_service.get_status(task.id) == "active"
    pending = approvals.list_pending_for_session(session["id"])
    assert len(pending) == 1
    rows = sessions.messages(session["id"])
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"] * 2
    assert json.loads(rows[2]["content"])["data"]["approval_required"] is True
    assert json.loads(rows[6]["content"])["data"]["reused"] is True
    assert "不会直接完成任务" in model_prompt(runtime, session)
    events = trace.list_events(first.trace_id)
    tool = next(e for e in events if e["kind"] == "tool")
    assert json.loads(tool["details_json"])["tool_kind"] == "approval"
    assert first.evaluation_status == second.evaluation_status == "pass"
    assert json.loads(AgentEvaluationRepository(conn).get_for_trace(first.trace_id, 2)["metrics_json"])["approval_tool_calls"] == 1

def test_model_cannot_call_approval_executor(conn):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("Study", scheduled_date="2026-09-15")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    provider = AgentApprovalProvider(AgentApprovalService(AgentApprovalRepository(conn), task_service))
    class BadModel(AgentModelClient):
        calls = 0
        def is_configured(self): return True
        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                assert [tool["function"]["name"] for tool in request.tools] == ["request_complete_current_task"]
                return ModelResponse("", (ModelToolCall("bad", "approve_and_execute", "{}"),))
            return ModelResponse("Could not approve")
    runtime = AgentRuntime(sessions, BadModel(), approval_provider=provider)
    result = runtime.send_message(session["id"], "approve yourself")
    assert task_service.get_status(task.id) == "active"
    assert AgentApprovalRepository(conn).list_pending_for_session(session["id"]) == []
    assert json.loads(result.tool_messages[0]["content"])["error"]["code"] == "tool_not_found"


def model_prompt(runtime, session):
    return runtime.build_system_message(session, tool_registry=runtime._with_approval(None)).content
