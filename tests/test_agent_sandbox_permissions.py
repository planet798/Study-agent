"""Sandbox-only mutation scope remains opt-in and isolated from Native/MCP tools."""

from __future__ import annotations

from app.agent.sandbox.config import SandboxConfig, SandboxExecutionConfig
from app.agent.sandbox.provider import SandboxProvider
from app.agent.sandbox.workspace import SandboxWorkspace
from app.agent.workspace import AgentWorkspaceSpec
from app.agent.sandbox.tools import sandbox_tools_for_task
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA
from app.agent.tools.registry import AgentToolRegistry


class SandboxMutationTool(AgentTool):
    @property
    def spec(self):
        return AgentToolSpec(
            "sandbox_mutation", "write only the task sandbox", EMPTY_OBJECT_SCHEMA,
            read_only=False, mutation_scope="sandbox",
        )

    def execute(self, context, arguments):
        return {"ok": True}


class ApplicationMutationTool(AgentTool):
    @property
    def spec(self):
        return AgentToolSpec(
            "application_mutation", "write app state", EMPTY_OBJECT_SCHEMA,
            read_only=False, mutation_scope="application",
        )

    def execute(self, context, arguments):
        return {"ok": True}


class ReadOnlyTool(AgentTool):
    @property
    def spec(self):
        return AgentToolSpec("native_read", "read", EMPTY_OBJECT_SCHEMA, True)

    def execute(self, context, arguments):
        return {"value": 1}


class Backend:
    def __init__(self, _config, available):
        self.available = available


def test_default_registry_still_rejects_sandbox_mutation_tools():
    registry = AgentToolRegistry()
    registry.register(ReadOnlyTool())
    try:
        registry.register(SandboxMutationTool())
    except ValueError as exc:
        assert "mutation scope" in str(exc)
    else:
        raise AssertionError("default Registry must reject sandbox mutation")
    assert registry.names() == ("native_read",)


def test_only_explicit_sandbox_scope_allows_sandbox_mutation():
    registry = AgentToolRegistry(allowed_mutation_scopes=("sandbox",))
    registry.register(SandboxMutationTool())
    assert registry.names() == ("sandbox_mutation",)
    assert registry.get("sandbox_mutation").spec.mutation_scope == "sandbox"


def test_application_scope_cannot_be_enabled_or_registered():
    try:
        AgentToolRegistry(allowed_mutation_scopes=("application",))
    except ValueError:
        pass
    else:
        raise AssertionError("application mutation scope must remain impossible")
    registry = AgentToolRegistry(allowed_mutation_scopes=("sandbox",))
    try:
        registry.register(ApplicationMutationTool())
    except ValueError:
        pass
    else:
        raise AssertionError("application mutation must remain rejected")


def test_sandbox_tools_are_exact_and_scoped_by_operation(tmp_path):
    config = SandboxConfig(enabled=True, file_tools=True)
    workspace = SandboxWorkspace(17, tmp_path / "agent_workspaces", 1000)
    tools = sandbox_tools_for_task(config, workspace, None, writable=True)
    by_name = {tool.spec.name: tool.spec for tool in tools}
    assert tuple(by_name) == (
        "sandbox_list_files", "sandbox_read_file", "sandbox_write_file",
        "sandbox_make_directory",
    )
    assert by_name["sandbox_list_files"].read_only is True
    assert by_name["sandbox_read_file"].read_only is True
    assert by_name["sandbox_write_file"].read_only is False
    assert by_name["sandbox_write_file"].mutation_scope == "sandbox"
    assert by_name["sandbox_make_directory"].read_only is False
    assert by_name["sandbox_make_directory"].mutation_scope == "sandbox"
    assert "sandbox_run" not in by_name


def test_run_tool_only_appears_when_execution_is_enabled_and_backend_available(tmp_path):
    config = SandboxConfig(
        enabled=True, file_tools=True,
        execution=SandboxExecutionConfig(enabled=True),
    )
    workspace = SandboxWorkspace(1, tmp_path / "ws", 1000)
    unavailable = Backend(config.execution, available=False)
    tools = sandbox_tools_for_task(config, workspace, unavailable, writable=True)
    assert "sandbox_run" not in {tool.spec.name for tool in tools}

    available = Backend(config.execution, available=True)
    tools = sandbox_tools_for_task(config, workspace, available, writable=True)
    run_tool = next(t for t in tools if t.spec.name == "sandbox_run")
    assert run_tool.spec.read_only is False
    assert run_tool.spec.mutation_scope == "sandbox"


def test_provider_composes_native_then_sandbox_and_does_not_create_workspace_on_open(
    tmp_path,
):
    config = SandboxConfig(enabled=True, file_tools=True)
    provider = SandboxProvider(config, workspace_root=tmp_path / "workspaces")
    native = AgentToolRegistry()
    native.register(ReadOnlyTool())
    context = AgentToolContext(session_id=1, task_id=77)

    with provider.open_turn(context, base_registry=native, workspace_spec=AgentWorkspaceSpec("managed", tmp_path / "workspaces" / "task_77", True, True, True)) as scope:
        assert scope.registry.names() == (
            "native_read", "sandbox_list_files", "sandbox_read_file",
            "sandbox_write_file", "sandbox_make_directory",
        )
        assert scope.registry.get("native_read") is native.get("native_read")
        assert not (tmp_path / "workspaces" / "task_77").exists()
    assert not (tmp_path / "workspaces").exists()


def test_disabled_sandbox_returns_only_base_registry(tmp_path):
    provider = SandboxProvider(SandboxConfig(), workspace_root=tmp_path / "workspaces")
    native = AgentToolRegistry()
    native.register(ReadOnlyTool())
    with provider.open_turn(AgentToolContext(1, 8), native) as scope:
        assert scope.registry.names() == ("native_read",)
    assert not (tmp_path / "workspaces").exists()
