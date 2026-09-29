"""Compose task-scoped Sandbox tools with an existing Native/MCP Registry."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..tools.registry import AgentToolRegistry
from ..workspace import AgentWorkspaceSpec
from .backend import DockerSandboxBackend
from .config import SandboxConfig
from .tools import sandbox_tools_for_task
from .workspace import LocalProjectWorkspace, SandboxWorkspace


@dataclass(frozen=True)
class SandboxTurnReport:
    enabled: bool
    execution_available: bool
    exposed_tools: tuple[str, ...]


@dataclass(frozen=True)
class SandboxTurnScope:
    registry: AgentToolRegistry
    report: SandboxTurnReport


def default_sandbox_workspace_root() -> Path:
    """Fixed application data root; never derived from model/task text."""
    repository_root = Path(__file__).resolve().parents[3]
    return repository_root / "data" / "agent_workspaces"


class SandboxProvider:
    """Operator-configured task file tools and optional Docker-only execution."""

    def __init__(
        self,
        config: SandboxConfig,
        workspace_root: str | Path | None = None,
        docker_backend_factory=DockerSandboxBackend,
    ):
        self.config = config
        self.workspace_root = Path(
            workspace_root if workspace_root is not None
            else default_sandbox_workspace_root()
        ).expanduser().absolute()
        self.backend = None
        if config.enabled and config.execution.enabled:
            try:
                self.backend = docker_backend_factory(
                    config.execution, max_output_chars=config.max_output_chars
                )
            except Exception:  # noqa: BLE001 - execution is optional
                self.backend = None

    @contextmanager
    def open_turn(
        self,
        context,
        base_registry: AgentToolRegistry | None = None,
        workspace_spec: AgentWorkspaceSpec | None = None,
    ) -> Iterator[SandboxTurnScope]:
        """Build a fresh effective Registry without touching workspace files."""
        effective = AgentToolRegistry(allowed_mutation_scopes=("sandbox",))
        if base_registry is not None:
            for tool in base_registry.registered_tools():
                if not tool.spec.read_only or tool.spec.mutation_scope:
                    raise ValueError(
                        "Sandbox base registry may contain only read-only native/MCP tools"
                    )
                effective.register(tool)

        workspace = None
        sandbox_tools = ()
        if workspace_spec is not None and workspace_spec.readable and workspace_spec.root is not None:
            if workspace_spec.kind == "managed" and workspace_spec.writable:
                # Managed physical root is fixed by the service, not a model argument.
                workspace = SandboxWorkspace(
                    task_id=context.task_id,
                    base_root=workspace_spec.root.parent,
                    max_file_chars=self.config.max_file_chars,
                )
                if workspace.task_root != workspace_spec.root:
                    raise ValueError("Managed workspace identity is invalid.")
            elif workspace_spec.kind == "local" and not workspace_spec.writable:
                workspace = LocalProjectWorkspace(
                    workspace_spec.root, max_file_chars=self.config.max_file_chars,
                )
            if workspace is not None:
                backend = self.backend if (workspace_spec.kind == "managed"
                    and workspace_spec.execution_allowed) else None
                if self.config.enabled and self.config.execution.enabled and backend is not None:
                    probe = getattr(backend, "probe", None)
                    if callable(probe):
                        try:
                            probe()
                        except Exception:  # noqa: BLE001 - optional Docker execution
                            setattr(backend, "available", False)
                sandbox_tools = sandbox_tools_for_task(
                    self.config, workspace, backend,
                    writable=workspace_spec.kind == "managed",
                )

        exposed: list[str] = []
        for tool in sandbox_tools:
            try:
                effective.register(tool)
            except (TypeError, ValueError):
                # Tool-name collision fails closed; Native/MCP registration wins.
                continue
            exposed.append(tool.spec.name)

        report = SandboxTurnReport(
            enabled=bool(sandbox_tools),
            execution_available="sandbox_run" in exposed,
            exposed_tools=tuple(exposed),
        )
        yield SandboxTurnScope(effective, report)
