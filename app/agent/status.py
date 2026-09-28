"""Static, sanitized capability snapshot; no external connections or mutation."""
from __future__ import annotations

from dataclasses import dataclass

from .mcp.config import MCPConfigError, load_mcp_config
from .sandbox.config import SandboxConfigError, load_sandbox_config

WARNINGS = frozenset({"mcp_config_invalid", "sandbox_config_invalid",
                      "sandbox_execution_unavailable"})


@dataclass(frozen=True)
class AgentCapabilityStatus:
    model_configured: bool
    native_tools: bool = True
    mcp_configured: bool = False
    sandbox_configured: bool = False
    sandbox_execution_configured: bool = False
    approvals_enabled: bool = False
    warnings: tuple[str, ...] = ()


def build_agent_capability_status(ai_config_service=None, *, approvals_enabled=False,
                                  mcp_config_path=None, sandbox_config_path=None) -> AgentCapabilityStatus:
    """Read only local configuration; never probe Docker, connect MCP or call a model."""
    try:
        model = bool(ai_config_service and ai_config_service.get_runtime_config().is_configured)
    except Exception:
        model = False
    warnings = []
    try:
        mcp = load_mcp_config(mcp_config_path)
        mcp_enabled = any(server.enabled for server in mcp.servers)
    except (MCPConfigError, OSError):
        mcp_enabled = False
        warnings.append("mcp_config_invalid")
    try:
        sandbox = load_sandbox_config(sandbox_config_path)
        sandbox_enabled = sandbox.enabled
        execution_enabled = sandbox_enabled and sandbox.execution.enabled
    except (SandboxConfigError, OSError):
        sandbox_enabled = execution_enabled = False
        warnings.append("sandbox_config_invalid")
    return AgentCapabilityStatus(
        model_configured=model, mcp_configured=mcp_enabled,
        sandbox_configured=sandbox_enabled,
        sandbox_execution_configured=execution_enabled,
        approvals_enabled=bool(approvals_enabled), warnings=tuple(warnings),
    )
