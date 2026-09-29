"""Task-scoped bounded Sandbox file and optional Docker tools."""

from __future__ import annotations

from .backend import DockerSandboxBackend, SandboxExecutionUnavailable
from .config import SandboxConfig
from .workspace import SandboxWorkspace, SandboxWorkspaceError
from ..tools.base import AgentTool, AgentToolContext, AgentToolSpec


class SandboxToolError(SandboxWorkspaceError):
    code = "sandbox_tool_failed"


class SandboxRunUnavailableError(SandboxToolError):
    code = "sandbox_execution_unavailable"

    def __init__(self):
        super().__init__("Sandbox execution is unavailable.")


class _WorkspaceTool(AgentTool):
    def __init__(self, workspace: SandboxWorkspace, spec: AgentToolSpec):
        self.workspace = workspace
        self._spec = spec

    @property
    def spec(self) -> AgentToolSpec:
        return self._spec


class SandboxListFilesTool(_WorkspaceTool):
    def __init__(self, workspace: SandboxWorkspace):
        super().__init__(workspace, AgentToolSpec(
            name="sandbox_list_files",
            description="List one level of files in the current Task's isolated Sandbox workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            read_only=True,
        ))

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context
        return self.workspace.list_files(arguments.get("path", "."))


class SandboxReadFileTool(_WorkspaceTool):
    def __init__(self, workspace: SandboxWorkspace):
        super().__init__(workspace, AgentToolSpec(
            name="sandbox_read_file",
            description="Read bounded UTF-8 text from the current Task Sandbox workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            read_only=True,
        ))

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context
        return self.workspace.read_text_file(arguments["path"])


class SandboxWriteFileTool(_WorkspaceTool):
    def __init__(self, workspace: SandboxWorkspace):
        super().__init__(workspace, AgentToolSpec(
            name="sandbox_write_file",
            description="Write UTF-8 text only inside the current Task Sandbox workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            read_only=False,
            mutation_scope="sandbox",
        ))

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context
        return self.workspace.write_text_file(
            arguments["path"],
            arguments["content"],
            overwrite=arguments.get("overwrite", False),
        )


class SandboxMakeDirectoryTool(_WorkspaceTool):
    def __init__(self, workspace: SandboxWorkspace):
        super().__init__(workspace, AgentToolSpec(
            name="sandbox_make_directory",
            description="Create a directory only inside the current Task Sandbox workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            read_only=False,
            mutation_scope="sandbox",
        ))

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context
        return self.workspace.make_directory(arguments["path"])


class SandboxRunTool(AgentTool):
    def __init__(self, workspace: SandboxWorkspace, backend: DockerSandboxBackend):
        self.workspace = workspace
        self.backend = backend
        self._spec = AgentToolSpec(
            name="sandbox_run",
            description=(
                "Run argv in the configured isolated Docker container, mounting only "
                "the current Task workspace with network disabled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "stdin": {"type": "string"},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            read_only=False,
            mutation_scope="sandbox",
        )

    @property
    def spec(self) -> AgentToolSpec:
        return self._spec

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context
        try:
            return self.backend.run(
                workspace=self.workspace,
                argv=arguments["argv"],
                cwd=arguments.get("cwd", "."),
                stdin=arguments.get("stdin", ""),
            )
        except SandboxExecutionUnavailable:
            raise SandboxRunUnavailableError() from None
        except SandboxWorkspaceError:
            raise
        except Exception as exc:  # noqa: BLE001 - no Docker/host details to model
            raise SandboxToolError("Sandbox execution failed.") from exc


def sandbox_tools_for_task(
    config: SandboxConfig,
    workspace: SandboxWorkspace,
    backend: DockerSandboxBackend | None,
    *,
    writable: bool,
) -> tuple[AgentTool, ...]:
    """Workspace binding grants files; advanced config only controls execution/limits."""
    tools: list[AgentTool] = [SandboxListFilesTool(workspace), SandboxReadFileTool(workspace)]
    if writable:
        tools.extend((SandboxWriteFileTool(workspace), SandboxMakeDirectoryTool(workspace)))
        if config.enabled and config.execution.enabled and backend is not None and backend.available:
            tools.append(SandboxRunTool(workspace, backend))
    return tuple(tools)
