"""AgentRuntime trace lifecycle, integration hooks, and fail-open regressions."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.agent.memory.compactor import AgentMemoryCompactor, AgentMemoryPolicy
from app.agent.runtime import AgentRuntime, AgentRuntimeError
from app.agent.session import AgentSessionService
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolError, AgentToolSpec
from app.agent.tools.base import EMPTY_OBJECT_SCHEMA
from app.agent.tools.registry import AgentToolRegistry
from app.agent.trace.service import AgentTraceService
from app.agent.eval.evaluator import AgentTurnEvaluator
from app.ai.agent_protocol import AgentModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.ai.interface import AIServiceError
from app.database.agent_evaluation_repository import AgentEvaluationRepository
from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.agent_trace_repository import AgentTraceRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


class FakeModel(AgentModelClient):
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []
        self.calls = 0

    def is_configured(self):
        return True

    def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected model request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ResultTool(AgentTool):
    def __init__(self, name, result=None, error=False):
        self.name = name
        self.result = result if result is not None else {"ok": "tool result"}
        self.error = error

    @property
    def spec(self):
        return AgentToolSpec(self.name, "safe test description", {
            "type": "object", "properties": {"content": {"type": "string"}},
            "required": [], "additionalProperties": False,
        }, True)

    def execute(self, context: AgentToolContext, arguments: dict):
        if self.error:
            raise AgentToolError("private tool error body")
        return self.result


class FakeMCPProvider:
    def __init__(self, registry):
        self.registry = registry

    @contextmanager
    def open_turn(self, native_registry=None):
        report = SimpleNamespace(
            available_servers=("docs",), unavailable_servers=("offline",),
            exposed_tools=("mcp_docs_search",),
        )
        yield SimpleNamespace(registry=self.registry, report=report)


class FakeSandboxProvider:
    config = SimpleNamespace(file_tools=True)

    def __init__(self, registry):
        self.registry = registry

    @contextmanager
    def open_turn(self, context, base_registry=None):
        report = SimpleNamespace(
            enabled=True, execution_available=False,
            exposed_tools=("sandbox_list_files",),
        )
        yield SimpleNamespace(registry=self.registry, report=report)


def _session(conn, title="Trace Runtime"):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task(
        title=title, scheduled_date="2026-10-01", source="manual"
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    trace_service = AgentTraceService(
        AgentTraceRepository(conn), AgentEvaluationRepository(conn), AgentTurnEvaluator()
    )
    return task_service, sessions, task, session, trace_service


def _response(content="answer", *, calls=(), usage=None):
    return ModelResponse(
        content=content, tool_calls=tuple(calls), finish_reason="tool_calls" if calls else "stop",
        model="fake-model", usage=(usage if usage is not None else {
            "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
        }),
    )


def _trace_for(conn, trace_id):
    return AgentTraceRepository(conn).get(trace_id)


def test_successful_no_tool_turn_persists_trace_and_pass_evaluation(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    model = FakeModel([_response("normal answer")])
    runtime = AgentRuntime(sessions, model, trace_service=trace_service)

    result = runtime.send_message(session["id"], "hello")

    assert result.assistant_message["content"] == "normal answer"
    assert result.trace_id > 0 and result.evaluation_status == "pass"
    trace = _trace_for(conn, result.trace_id)
    assert trace["status"] == "succeeded"
    assert trace["model_call_count"] == 1
    assert trace["tool_call_count"] == trace["tool_rounds"] == 0
    assert trace["usage_complete"] == 1
    assert json.loads(AgentEvaluationRepository(conn).get_for_trace(
        result.trace_id, 2
    )["checks_json"])["turn_succeeded"] is True


def test_multi_tool_call_round_is_counted_as_one_round_and_three_calls(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    registry = AgentToolRegistry()
    for index in range(3):
        registry.register(ResultTool(f"echo_{index}"))
    calls = tuple(ModelToolCall(f"c{index}", f"echo_{index}", "{}") for index in range(3))
    model = FakeModel([_response("", calls=calls), _response("final")])
    runtime = AgentRuntime(
        sessions, model, tool_registry=registry, trace_service=trace_service
    )

    result = runtime.send_message(session["id"], "use three tools")
    trace = _trace_for(conn, result.trace_id)

    assert (trace["model_call_count"], trace["tool_call_count"], trace["tool_rounds"]) == (
        2, 3, 1,
    )
    assert trace["tool_error_count"] == 0
    assert result.evaluation_status == "pass"


def test_controlled_tool_error_warns_but_turn_succeeds(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    registry = AgentToolRegistry()
    registry.register(ResultTool("native_error", error=True))
    model = FakeModel([
        _response("", calls=(ModelToolCall("c1", "native_error", "{}"),)),
        _response("handled"),
    ])
    runtime = AgentRuntime(
        sessions, model, tool_registry=registry, trace_service=trace_service
    )

    result = runtime.send_message(session["id"], "try a failing tool")
    trace = _trace_for(conn, result.trace_id)
    event = AgentTraceRepository(conn).list_events(result.trace_id)[0]

    assert trace["status"] == "succeeded"
    assert trace["tool_call_count"] == trace["tool_error_count"] == 1
    assert result.evaluation_status == "warn"
    assert "private tool error body" not in event["details_json"]


def test_blank_or_closed_session_does_not_create_trace(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    runtime = AgentRuntime(
        sessions, FakeModel([_response("not used")]), trace_service=trace_service
    )
    with pytest.raises(Exception):
        runtime.send_message(session["id"], "   ")
    sessions.close(session["id"])
    with pytest.raises(Exception):
        runtime.send_message(session["id"], "closed turn")
    assert AgentTraceRepository(conn).list_for_session(session["id"]) == []
    assert sessions.messages(session["id"]) == []


def test_ai_failure_persists_failed_trace_and_reraises_original_error(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    error = AIServiceError("private endpoint and API key")
    runtime = AgentRuntime(sessions, FakeModel([error]), trace_service=trace_service)

    with pytest.raises(AIServiceError) as caught:
        runtime.send_message(session["id"], "will fail")

    assert caught.value is error
    rows = sessions.messages(session["id"])
    assert [row["role"] for row in rows] == ["user"]
    trace = AgentTraceRepository(conn).list_for_session(session["id"])[0]
    event = next(
        event for event in AgentTraceRepository(conn).list_events(trace["id"])
        if event["kind"] == "model"
    )
    assert trace["status"] == "failed"
    assert trace["assistant_message_id"] is None
    assert trace["error_code"] == "ai_service_error"
    assert event["status"] == "error"
    assert "private endpoint" not in event["details_json"]
    assert AgentEvaluationRepository(conn).get_for_trace(trace["id"], 2)["status"] == "fail"


def test_context_too_large_leaves_user_and_failed_trace(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    policy = AgentMemoryPolicy(
        history_budget_chars=300, compaction_trigger_chars=200,
        target_tail_chars=100, keep_recent_turns=1,
        summary_input_max_chars=1000, summary_max_chars=200,
        per_message_summary_chars=128, max_compaction_passes=1,
        summary_max_tokens=20,
    )
    compactor = AgentMemoryCompactor(
        sessions, AgentMemoryRepository(conn), FakeModel([]), policy
    )
    model = FakeModel([_response("must not be called")])
    runtime = AgentRuntime(
        sessions, model, memory_compactor=compactor, trace_service=trace_service
    )

    with pytest.raises(Exception) as caught:
        runtime.send_message(session["id"], "x" * 500)

    assert type(caught.value).__name__ == "AgentContextTooLargeError"
    assert model.calls == 0
    assert [row["role"] for row in sessions.messages(session["id"])] == ["user"]
    trace = AgentTraceRepository(conn).list_for_session(session["id"])[0]
    assert trace["status"] == "failed" and trace["error_code"] == "context_too_large"
    memory_event = next(event for event in AgentTraceRepository(conn).list_events(trace["id"])
                        if event["kind"] == "memory")
    assert json.loads(memory_event["details_json"])["error_code"] == "context_too_large"


def test_memory_summary_calls_are_included_and_fail_soft_does_not_fail_turn(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    for index in range(5):
        sessions.append_user_message(session["id"], f"older question {index} " + "q" * 220)
        sessions.append_assistant_message(session["id"], f"older answer {index} " + "a" * 220)
    policy = AgentMemoryPolicy(
        history_budget_chars=7000, compaction_trigger_chars=1200,
        target_tail_chars=600, keep_recent_turns=1,
        summary_input_max_chars=4000, summary_max_chars=500,
        per_message_summary_chars=500, max_compaction_passes=3,
        summary_max_tokens=100,
    )
    model = FakeModel([
        AIServiceError("summary endpoint secret"), _response("normal response"),
    ])
    compactor = AgentMemoryCompactor(
        sessions, AgentMemoryRepository(conn), model, policy
    )
    runtime = AgentRuntime(
        sessions, model, memory_compactor=compactor, trace_service=trace_service
    )

    result = runtime.send_message(session["id"], "current question")
    trace = _trace_for(conn, result.trace_id)
    events = AgentTraceRepository(conn).list_events(result.trace_id)
    model_events = [event for event in events if event["kind"] == "model"]

    assert result.assistant_message["content"] == "normal response"
    assert trace["status"] == "succeeded"
    assert trace["model_call_count"] == 2
    assert trace["memory_model_call_count"] == 1
    assert trace["usage_complete"] == 0
    assert trace["memory_compacted"] == 0
    assert result.evaluation_status == "warn"
    assert json.loads(model_events[0]["details_json"])["purpose"] == "memory_summary"
    assert json.loads(model_events[0]["details_json"])["error_code"] == "ai_service_error"
    memory_failure = next(event for event in events
                          if event["kind"] == "memory" and event["name"] == "summary")
    assert memory_failure["status"] == "error"
    assert "summary endpoint secret" not in json.dumps(events)


def test_successful_memory_compaction_records_boundary_and_model_cost(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    for index in range(5):
        sessions.append_user_message(session["id"], f"history-{index} " + "q" * 250)
        sessions.append_assistant_message(session["id"], f"answer-{index} " + "a" * 250)
    policy = AgentMemoryPolicy(
        history_budget_chars=7000, compaction_trigger_chars=1200,
        target_tail_chars=600, keep_recent_turns=1,
        summary_input_max_chars=4000, summary_max_chars=500,
        per_message_summary_chars=500, max_compaction_passes=3,
        summary_max_tokens=100,
    )
    memory_model = FakeModel([_response("stored rolling summary")])
    compactor = AgentMemoryCompactor(
        sessions, AgentMemoryRepository(conn), memory_model, policy
    )
    normal_model = FakeModel([_response("answer")])
    runtime = AgentRuntime(
        sessions, normal_model, memory_compactor=compactor, trace_service=trace_service
    )

    result = runtime.send_message(session["id"], "new question")
    trace = _trace_for(conn, result.trace_id)
    events = AgentTraceRepository(conn).list_events(result.trace_id)
    model_events = [event for event in events if event["kind"] == "model"]

    assert trace["memory_compacted"] == 1
    assert trace["memory_through_message_id"] > 0
    assert trace["model_call_count"] == 2
    assert trace["memory_model_call_count"] == 1
    assert trace["total_tokens"] == 28
    assert [json.loads(event["details_json"])["purpose"] for event in model_events] == [
        "memory_summary", "agent",
    ]


def test_mcp_and_sandbox_scope_events_store_only_safe_metadata(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)
    registry = AgentToolRegistry()
    registry.register(ResultTool("native_read"))
    registry.register(ResultTool("mcp_docs_search"))
    registry.register(ResultTool("sandbox_list_files"))
    model = FakeModel([_response("answer")])
    runtime = AgentRuntime(
        sessions, model, tool_registry=registry,
        mcp_provider=FakeMCPProvider(registry),
        sandbox_provider=FakeSandboxProvider(registry),
        trace_service=trace_service,
    )

    result = runtime.send_message(session["id"], "hello")
    events = AgentTraceRepository(conn).list_events(result.trace_id)
    by_name = {event["name"]: json.loads(event["details_json"]) for event in events}

    assert by_name["discovery"]["available_server_keys"] == ["docs"]
    assert by_name["discovery"]["unavailable_server_keys"] == ["offline"]
    assert by_name["scope"] == {
        "file_tools": True, "execution_available": False, "tool_count": 1,
    }
    assert "url" not in json.dumps(events).lower()
    assert "workspace" not in json.dumps(events).lower()


def test_trace_service_failure_is_fail_open_for_success_and_original_failure(conn):
    _task_service, sessions, _task, session, trace_service = _session(conn)

    class FailingTraceService:
        def new_collector(self, *args):
            return trace_service.new_collector(*args)

        def safe_record_success(self, *args):
            raise RuntimeError("trace database secret")

        def safe_record_failure(self, *args):
            raise RuntimeError("trace database secret")

    success = AgentRuntime(
        sessions, FakeModel([_response("still succeeds")]),
        trace_service=FailingTraceService(),
    ).send_message(session["id"], "success")
    assert success.assistant_message["content"] == "still succeeds"
    assert success.trace_id == 0 and success.evaluation_status == ""

    error = AIServiceError("original model failure")
    with pytest.raises(AIServiceError) as caught:
        AgentRuntime(
            sessions, FakeModel([error]), trace_service=FailingTraceService()
        ).send_message(session["id"], "fails")
    assert caught.value is error
    assert sessions.messages(session["id"])[-1]["role"] == "user"


def test_evaluation_failure_keeps_trace_and_successful_result(conn):
    _task_service, sessions, _task, session, _trace_service = _session(conn)

    class FailingEvaluator:
        def evaluate(self, trace, events):
            raise RuntimeError("evaluation internal secret")

    service = AgentTraceService(
        AgentTraceRepository(conn), AgentEvaluationRepository(conn), FailingEvaluator()
    )
    result = AgentRuntime(
        sessions, FakeModel([_response("complete")]), trace_service=service
    ).send_message(session["id"], "hello")

    assert result.assistant_message["content"] == "complete"
    assert result.trace_id > 0 and result.evaluation_status == ""
    assert AgentTraceRepository(conn).get(result.trace_id) is not None
    assert AgentEvaluationRepository(conn).list_for_trace(result.trace_id) == []
