"""User-selected, Task-scoped Workspace capabilities and safe display state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..agent.sandbox.config import DEFAULT_MAX_FILE_CHARS
from ..agent.sandbox.provider import default_sandbox_workspace_root
from ..agent.sandbox.workspace import SandboxWorkspace
from ..agent.workspace import AgentWorkspaceSpec
from ..database.task_workspace_repository import TaskWorkspaceRepository
from .task_service import TaskService

MAX_LOCAL_PATH_CHARS = 4096


@dataclass(frozen=True)
class TaskWorkspaceView:
    task_id: int
    kind: str
    label: str
    display_path: str
    available: bool
    readable: bool
    writable: bool


def is_filesystem_root(path: str | Path) -> bool:
    """Platform-independent root check, including Windows drives on POSIX hosts."""
    text = str(path)
    win = PureWindowsPath(text)
    if win.is_absolute() and len(win.parts) == 1:
        return True
    posix = Path(text)
    return posix.is_absolute() and posix == posix.parent


def canonical_local_directory(path: str | Path) -> Path:
    """Validate a path from a user directory picker, never from Agent tool input."""
    if not isinstance(path, (str, Path)):
        raise ValueError("Workspace directory is invalid.")
    raw = str(path)
    if not raw or "\x00" in raw or len(raw) > MAX_LOCAL_PATH_CHARS:
        raise ValueError("Workspace directory is invalid.")
    candidate = Path(raw)
    # Foreign-platform Windows paths must not become relative POSIX directory names.
    if not candidate.is_absolute() or is_filesystem_root(raw):
        raise ValueError("Workspace must be an existing non-root absolute directory.")
    try:
        canonical = candidate.resolve(strict=True)
        if not canonical.is_dir() or is_filesystem_root(canonical) or len(str(canonical)) > MAX_LOCAL_PATH_CHARS:
            raise ValueError("Workspace must be an existing non-root absolute directory.")
    except (OSError, RuntimeError):
        raise ValueError("Workspace must be an existing non-root absolute directory.") from None
    return canonical


class TaskWorkspaceService:
    def __init__(self, repo: TaskWorkspaceRepository, task_service: TaskService):
        self.repo = repo
        self.task_service = task_service

    def _validate_task(self, task_id: int) -> None:
        self.task_service.get_task(task_id)

    def get(self, task_id: int) -> dict | None:
        self._validate_task(task_id)
        return self.repo.get_by_task(task_id)

    def managed_path(self, task_id: int) -> Path:
        self._validate_task(task_id)
        return default_sandbox_workspace_root() / f"task_{int(task_id)}"

    def ensure_managed_directory(self, task_id: int) -> Path:
        """Only call for an explicit open-folder user action or a managed file tool."""
        self._validate_task(task_id)
        binding = self.repo.get_by_task(task_id)
        if binding is None or binding["kind"] != "managed":
            raise ValueError("No managed Workspace is bound to this Task.")
        return SandboxWorkspace(
            int(task_id), default_sandbox_workspace_root(), DEFAULT_MAX_FILE_CHARS
        ).ensure_workspace()

    def get_view(self, task_id: int) -> TaskWorkspaceView:
        self._validate_task(task_id)
        binding = self.repo.get_by_task(task_id)
        if binding is None:
            return TaskWorkspaceView(task_id, "none", "", "", False, False, False)
        if binding["kind"] == "managed":
            return TaskWorkspaceView(
                task_id, "managed", "Study-Agent 托管工作区",
                str(self.managed_path(task_id)), True, True, True,
            )
        path = Path(binding["local_path"])
        try:
            available = path.is_dir() and path.resolve(strict=True) == path
        except (OSError, RuntimeError):
            available = False
        return TaskWorkspaceView(
            task_id, "local", path.name, binding["local_path"],
            available, available, False,
        )

    def use_managed(self, task_id: int) -> TaskWorkspaceView:
        self._validate_task(task_id)
        self.repo.upsert(task_id, "managed", "")
        return self.get_view(task_id)

    def bind_local(self, task_id: int, path: str | Path) -> TaskWorkspaceView:
        self._validate_task(task_id)
        canonical = canonical_local_directory(path)
        self.repo.upsert(task_id, "local", str(canonical))
        return self.get_view(task_id)

    def clear(self, task_id: int) -> TaskWorkspaceView:
        self._validate_task(task_id)
        self.repo.delete_for_task(task_id)
        return self.get_view(task_id)

    def resolve_open_path(self, task_id: int) -> Path:
        view = self.get_view(task_id)
        if view.kind == "managed":
            return self.ensure_managed_directory(task_id)
        if view.kind == "local" and view.available:
            return Path(view.display_path)
        raise ValueError("Workspace directory is unavailable.")

    def runtime_spec(self, task_id: int) -> AgentWorkspaceSpec:
        view = self.get_view(task_id)
        if view.kind == "managed":
            return AgentWorkspaceSpec("managed", self.managed_path(task_id), True, True, True)
        if view.kind == "local":
            return AgentWorkspaceSpec(
                "local", Path(view.display_path) if view.available else None,
                view.available, False, False,
            )
        return AgentWorkspaceSpec("none", None, False, False, False)
