"""Sandbox Runtime composition, tool history, Task isolation, and app-state invariant."""

from __future__ import annotations

import json

from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.agent.skills import AgentSkillSelector, build_default_agent_skill_registry
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA
from app.agent.tools.registry import AgentToolRegistry
from app.ai.agent_protocol import ModelRequest, ModelResponse, ModelToolCall
from app.database.agent_repository import AgentRepository
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


class ContextSnapshot:
    def __init__(self, task_id, activity_kind="practice"):
        self.task_id = task_id
        self.activity_kind = activity_kind
        self.calls = []

    def build(self, context):
        self.calls.append(context)
        return {
            "task": {
                "title": "Practice task", "description": "Implement a small demo",
                "activity_kind": self.activity_kind, "status": "active",
                "deliverable": "demo.py", "acceptance_criteria": "prints result",
                "expected_artifact": "stdout result",
            },
            "route": None, "phase": None, "topic": None, "learning": None,
            "mastery": None, "capability": None,
        }


class ReadOnlyTool(AgentTool):
    def __init__(self, name):
        self._spec = AgentToolSpec(name, "approved read-only test tool", EMPTY_OBJECT_SCHEMA)

    @property
    def spec(self):
        return self._spec

    def execute(self, context, arguments):
        return {"task_id": context.task_id, "read_only": True}


class FakeSandboxBackend:
    instances = []

    def __init__(self, config, max_output_chars=20000):
        self.config = config
        self.max_output_chars = max_output_chars
        self.available = True
        self.runs = []
        self.__class__.instances.append(self)

    def run(self, workspace, argv, cwd=".", stdin=""):
        self.runs.append((workspace.task_id, list(argv), cwd, stdin))
        # The test backend is deterministic; it never executes host code.
        return {
            "exit_code": 0, "stdout": "sandbox result", "stderr": "",
            "timed_out": False, "stdout_truncated": False,
            "stderr_truncated": False,
        }


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _call(name, call_id, arguments):
    return ModelToolCall(
        id=call_id, name=name, arguments=json.dumps(arguments, ensure_ascii=False)
    )


def _business_state(conn, task_service, assessment_repo, task_id, kp_id):
    task = task_service.get_task(task_id)
    kp = assessment_repo.get_knowledge_point(kp_id)
    practice_tables = (
        "practice_projects", "practice_project_routes", "practice_project_skills",
        "practice_milestones", "practice_outputs", "practice_project_topics",
        "practice_topic_requirements", "practice_topic_evidence",
        "practice_topic_evidence_outputs",
    )
    return {
        "task": (task.id, task.status, task.title),
        "mastery": (kp["mastery_estimate"], kp["last_assessed_at"]),
        "assessment_count": conn.execute(
            "SELECT COUNT(*) FROM assessment_attempts"
        ).fetchone()[0],
        "capability_count": conn.execute(
            "SELECT COUNT(*) FROM capability_evidence"
        ).fetchone()[0],
        "practice": {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in practice_tables
        },
    }


def test_native_mcp_and_sandbox_compose_with_sandbox_scope_only(
    conn, repo, task_service, tmp_path
):
    from app.agent.sandbox.config import SandboxConfig, SandboxExecutionConfig
    from app.agent.sandbox.provider import SandboxProvider
    from app.database.assessment_repository import AssessmentRepository

    task_repo = TaskRepository(conn)
    # User-provided fixtures use the same repo/task service connection.
    task = repo.create(
        title="Practice task", description="Implement a small demo",
        scheduled_date="2026-09-15", source="generated", task_type="new",
    )
    assessment_repo = AssessmentRepository(conn)
    kp = assessment_repo.create_knowledge_point("sandbox runtime kp")
    task = task_repo.update(task.id, knowledge_point_id=kp["id"])
    task_service = TaskService(task_repo)
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)

    native_mcp_registry = AgentToolRegistry()
    native_mcp_registry.register(ReadOnlyTool("native_read"))
    native_mcp_registry.register(ReadOnlyTool("mcp_docs_search"))
    config = SandboxConfig(
        enabled=True, file_tools=True,
        execution=SandboxExecutionConfig(enabled=True),
    )
    provider = SandboxProvider(
        config,
        workspace_root=tmp_path / "agent_workspaces",
        docker_backend_factory=FakeSandboxBackend,
    )
    context_builder = ContextSnapshot(task.id)
    class CountingSelector:
        def __init__(self):
            self.delegate = AgentSkillSelector(build_default_agent_skill_registry())
            self.calls = []

        def select(self, snapshot):
            self.calls.append(snapshot)
            return self.delegate.select(snapshot)

    selector = CountingSelector()
    model = ScriptedModel([
        ModelResponse(content="", tool_calls=(_call(
            "sandbox_make_directory", "dir", {"path": "src"}
        ),), finish_reason="tool_calls"),
        ModelResponse(content="", tool_calls=(_call(
            "sandbox_write_file", "write", {
                "path": "src/main.py", "content": "print('ok')",
            }
        ),), finish_reason="tool_calls"),
        ModelResponse(content="", tool_calls=(_call(
            "sandbox_run", "run", {"argv": ["python", "src/main.py"]}
        ),), finish_reason="tool_calls"),
        ModelResponse(content="", tool_calls=(_call(
            "sandbox_read_file", "read", {"path": "src/main.py"}
        ),), finish_reason="tool_calls"),
        ModelResponse(content="Sandbox result reviewed", finish_reason="stop"),
    ])
    runtime = AgentRuntime(
        sessions, model, tool_registry=native_mcp_registry,
        context_builder=context_builder, skill_selector=selector,
        sandbox_provider=provider,
    )
    before_state = _business_state(
        conn, task_service, assessment_repo, task.id, kp["id"]
    )
    before = __import__("app.diagnostics.release_migration", fromlist=["inventory"]).inventory(conn)

    result = runtime.send_message(session["id"], "Build and run the demo")

    assert result.skill_key == "practice-coach"
    assert result.tool_rounds == 4
    assert len(context_builder.calls) == 1
    assert len(selector.calls) == 1
    assert len(model.requests) == 5
    exposed = [t["function"]["name"] for t in model.requests[0].tools]
    assert exposed == [
        "native_read", "mcp_docs_search", "sandbox_list_files",
        "sandbox_read_file", "sandbox_write_file",
        "sandbox_make_directory", "sandbox_run",
    ]
    assert all("sandbox_run" in [
        t["function"]["name"] for t in request.tools
    ] for request in model.requests)
    assert all(
        "sandbox" in request.messages[0].content.lower()
        for request in model.requests
    )
    assert len(FakeSandboxBackend.instances) >= 1
    backend = FakeSandboxBackend.instances[-1]
    assert backend.runs == [(
        task.id, ["python", "src/main.py"], ".", "",
    )]
    rows = sessions.messages(session["id"])
    assert [row["role"] for row in rows] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant", "tool",
        "assistant", "tool", "assistant",
    ]
    assert [row["tool_name"] for row in rows if row["role"] == "tool"] == [
        "sandbox_make_directory", "sandbox_write_file", "sandbox_run",
        "sandbox_read_file",
    ]
    assert json.loads(rows[6]["content"])["data"]["stdout"] == "sandbox result"
    assert _business_state(
        conn, task_service, assessment_repo, task.id, kp["id"]
    ) == before_state

    from app.diagnostics.release_migration import verify
    verified = verify(conn, before=before)
    assert verified["ok"] is True, verified
    assert verified["history_new_rows"]["agent_messages"] == 10


def test_sandbox_registry_rejects_application_mutation_even_when_enabled(tmp_path):
    from app.agent.sandbox.config import SandboxConfig
    from app.agent.sandbox.provider import SandboxProvider

    provider = SandboxProvider(
        SandboxConfig(enabled=True), workspace_root=tmp_path / "workspace"
    )
    with provider.open_turn(AgentToolContext(1, 5), AgentToolRegistry()) as scope:
        # The explicit scope is exactly "sandbox"; application scopes are unsupported.
        assert scope.registry.allowed_mutation_scopes == ("sandbox",)


def test_actual_mcp_provider_and_sandbox_scope_compose_in_one_runtime_turn(
    conn, repo, task_service, tmp_path
):
    from types import SimpleNamespace
    from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

    from app.agent.mcp.config import MCPConfig, MCPServerConfig
    from app.agent.mcp.provider import MCPToolProvider
    from app.agent.sandbox.config import SandboxConfig
    from app.agent.sandbox.provider import SandboxProvider

    task = repo.create(
        title="Coexist task", scheduled_date="2026-09-15", source="generated"
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    native = AgentToolRegistry()
    native.register(ReadOnlyTool("native_read"))

    class FakeBridge:
        def __init__(self, configs):
            self.configs = tuple(configs)
            self.available_servers = [c.key for c in self.configs]
            self.unavailable_servers = []
            self.closed = False
            self.calls = []

        def __enter__(self):
            return self

        def list_tools(self, server_key):
            return (Tool(
                name="search", description="read docs",
                inputSchema={"type": "object", "properties": {},
                             "additionalProperties": False},
                annotations=ToolAnnotations(readOnlyHint=True),
            ),)

        def call_tool(self, server_key, name, arguments):
            self.calls.append((server_key, name, arguments))
            return CallToolResult(content=[TextContent(text="external read result")])

        def close(self):
            self.closed = True

    bridges = []

    def bridge_factory(configs):
        bridge = FakeBridge(configs)
        bridges.append(bridge)
        return bridge

    mcp_provider = MCPToolProvider(MCPConfig(1, (MCPServerConfig(
        key="docs", transport="stdio", enabled=True, allowed_tools=("search",),
        command="fake", args=(),
    ),)), bridge_factory=bridge_factory)
    sandbox_provider = SandboxProvider(
        SandboxConfig(enabled=True, file_tools=True),
        workspace_root=tmp_path / "sandbox",
    )
    context = ContextSnapshot(task.id)

    class CountingSelector:
        def __init__(self):
            self.delegate = AgentSkillSelector(build_default_agent_skill_registry())
            self.calls = []

        def select(self, snapshot):
            self.calls.append(snapshot)
            return self.delegate.select(snapshot)

    selector = CountingSelector()
    model = ScriptedModel([
        ModelResponse(content="", tool_calls=(_call(
            "mcp_docs_search", "mcp", {}
        ),), finish_reason="tool_calls"),
        ModelResponse(content="", tool_calls=(_call(
            "sandbox_write_file", "sandbox", {"path": "note.txt", "content": "ok"}
        ),), finish_reason="tool_calls"),
        ModelResponse(content="finished", finish_reason="stop"),
    ])
    runtime = AgentRuntime(
        sessions, model, tool_registry=native, context_builder=context,
        skill_selector=selector, mcp_provider=mcp_provider,
        sandbox_provider=sandbox_provider,
    )

    result = runtime.send_message(session["id"], "Use approved external and task tools")
    names = [tool["function"]["name"] for tool in model.requests[0].tools]
    assert names == [
        "native_read", "mcp_docs_search", "sandbox_list_files",
        "sandbox_read_file", "sandbox_write_file", "sandbox_make_directory",
    ]
    assert result.tool_rounds == 2 and result.skill_key == "practice-coach"
    assert len(context.calls) == len(selector.calls) == 1
    assert bridges[0].calls == [("docs", "search", {})]
    assert bridges[0].closed is True
    assert [row["tool_name"] for row in sessions.messages(session["id"])
            if row["role"] == "tool"] == ["mcp_docs_search", "sandbox_write_file"]
    sandbox_root = tmp_path / "sandbox" / f"task_{task.id}"
    assert (sandbox_root / "note.txt").read_text(encoding="utf-8") == "ok"
