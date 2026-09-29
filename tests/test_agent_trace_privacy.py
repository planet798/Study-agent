"""Regression boundary: Trace/Eval rows contain telemetry, never conversation payloads."""

from __future__ import annotations

import json

from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolSpec
from app.agent.tools.registry import AgentToolRegistry
from app.agent.trace.service import AgentTraceService
from app.ai.agent_protocol import AgentModelClient, ModelResponse, ModelToolCall
from app.database.agent_evaluation_repository import AgentEvaluationRepository
from app.database.agent_repository import AgentRepository
from app.database.agent_trace_repository import AgentTraceRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


class _Model(AgentModelClient):
    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="", finish_reason="tool_calls", model="fake-model",
                tool_calls=(
                    ModelToolCall(
                        "sandbox-call", "sandbox_write_file",
                        '{"path":"notes.txt","content":"TOOL_ARGUMENT_SECRET"}',
                    ),
                    ModelToolCall(
                        "mcp-call", "mcp_docs_search",
                        '{"query":"MCP_ARGUMENT_SECRET"}',
                    ),
                    ModelToolCall("unknown-call", "USER_PRIVATE_SENTINEL", "{}"),
                ),
                usage={"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            )
        return ModelResponse(
            content="private final response", finish_reason="stop",
            model="https://BASE_URL_CREDENTIAL_SENTINEL",
            usage={"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55},
        )


class _SecretTool(AgentTool):
    def __init__(self, name, result):
        self.name = name
        self.result = result

    @property
    def spec(self):
        return AgentToolSpec(self.name, "safe description", {
            "type": "object", "properties": {"content": {"type": "string"}},
            "additionalProperties": True,
        }, True)

    def execute(self, context: AgentToolContext, arguments: dict):
        return self.result


def test_private_prompt_user_tool_arguments_and_external_outputs_stay_out_of_telemetry(conn):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task(
        title="Privacy Task", scheduled_date="2026-10-01", source="manual"
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    registry = AgentToolRegistry()
    registry.register(_SecretTool(
        "sandbox_write_file", {"written": True, "output": "SANDBOX_OUTPUT_SECRET"}
    ))
    registry.register(_SecretTool(
        "mcp_docs_search", {"text": "MCP_OUTPUT_SECRET"}
    ))
    traces = AgentTraceRepository(conn)
    evaluations = AgentEvaluationRepository(conn)
    service = AgentTraceService(traces, evaluations)
    runtime = AgentRuntime(
        sessions,
        _Model(),
        system_prompt="SYSTEM_PRIVATE_SENTINEL",
        tool_registry=registry,
        trace_service=service,
    )

    result = runtime.send_message(session["id"], "USER_PRIVATE_SENTINEL")
    trace_rows = conn.execute("SELECT * FROM agent_turn_traces").fetchall()
    event_rows = conn.execute("SELECT * FROM agent_trace_events").fetchall()
    evaluation_rows = conn.execute("SELECT * FROM agent_turn_evaluations").fetchall()
    telemetry = json.dumps({
        "traces": [dict(row) for row in trace_rows],
        "events": [dict(row) for row in event_rows],
        "evaluations": [dict(row) for row in evaluation_rows],
    }, ensure_ascii=False)

    for sentinel in (
        "USER_PRIVATE_SENTINEL", "SYSTEM_PRIVATE_SENTINEL", "TOOL_ARGUMENT_SECRET",
        "MCP_ARGUMENT_SECRET", "SANDBOX_OUTPUT_SECRET", "MCP_OUTPUT_SECRET",
        "BASE_URL_CREDENTIAL_SENTINEL", "private final response",
    ):
        assert sentinel not in telemetry
    assert result.trace_id > 0 and result.evaluation_status == "warn"

    raw_history = json.dumps(sessions.messages(session["id"]), ensure_ascii=False)
    assert "USER_PRIVATE_SENTINEL" in raw_history
    assert "TOOL_ARGUMENT_SECRET" in raw_history
    assert "SANDBOX_OUTPUT_SECRET" in raw_history
    assert "MCP_OUTPUT_SECRET" in raw_history


def test_local_workspace_host_path_never_enters_request_messages_or_telemetry(conn, tmp_path):
    from app.agent.sandbox.config import SandboxConfig
    from app.agent.sandbox.provider import SandboxProvider
    from app.agent.workspace import AgentWorkspaceSpec

    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task(title="Workspace privacy", scheduled_date="2026-10-01", source="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    project = tmp_path / "SECRET_PROJECT_PATH_SENTINEL"
    project.mkdir()
    (project / "README.md").write_text("public contents", encoding="utf-8")

    class Workspace:
        def runtime_spec(self, task_id):
            assert task_id == task.id
            return AgentWorkspaceSpec("local", project, True, False, False)

    class ReadModel(AgentModelClient):
        def __init__(self):
            self.requests = []

        def is_configured(self):
            return True

        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(content="", finish_reason="tool_calls", tool_calls=(
                    ModelToolCall("read-1", "sandbox_read_file", '{"path":"README.md"}'),
                ))
            return ModelResponse(content="Read complete", finish_reason="stop")

    model = ReadModel()
    runtime = AgentRuntime(
        sessions, model,
        sandbox_provider=SandboxProvider(SandboxConfig()),
        workspace_service=Workspace(),
        trace_service=AgentTraceService(AgentTraceRepository(conn), AgentEvaluationRepository(conn)),
    )
    runtime.send_message(session["id"], "Read README")
    assert len(model.requests) == 2
    assert "read-only mode" in model.requests[0].messages[0].content
    for request in model.requests:
        assert str(project) not in repr(request)
        assert "SECRET_PROJECT_PATH_SENTINEL" not in repr(request)
    for table in ("agent_turn_traces", "agent_trace_events", "agent_turn_evaluations", "agent_messages"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        serialized = json.dumps([dict(row) for row in rows])
        assert str(project) not in serialized
        assert "SECRET_PROJECT_PATH_SENTINEL" not in serialized
