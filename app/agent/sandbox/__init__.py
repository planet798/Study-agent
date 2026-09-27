"""Task-scoped Sandbox workspace and optional Docker execution tools."""

from .backend import DockerSandboxBackend, SandboxExecutionUnavailable
from .config import (
    SandboxConfig,
    SandboxConfigError,
    SandboxExecutionConfig,
    default_sandbox_config_path,
    load_sandbox_config,
    parse_sandbox_config,
)
from .provider import (
    SandboxProvider,
    SandboxTurnReport,
    SandboxTurnScope,
    default_sandbox_workspace_root,
)
from .workspace import (
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxWorkspace,
    SandboxWorkspaceError,
)

__all__ = [
    "DockerSandboxBackend",
    "SandboxConfig",
    "SandboxConfigError",
    "SandboxExecutionConfig",
    "SandboxExecutionUnavailable",
    "SandboxFileTooLargeError",
    "SandboxPathError",
    "SandboxProvider",
    "SandboxTurnReport",
    "SandboxTurnScope",
    "SandboxWorkspace",
    "SandboxWorkspaceError",
    "default_sandbox_config_path",
    "default_sandbox_workspace_root",
    "load_sandbox_config",
    "parse_sandbox_config",
]
