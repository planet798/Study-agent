"""Offline, read-only Agent production status; never initializes GUI or providers."""
from __future__ import annotations

import sqlite3

from ..agent.status import build_agent_capability_status
from ..database.connection import get_raw_connection
from ..database.schema import SCHEMA_VERSION, get_schema_version
from .release_migration import FINGERPRINT_VERSION


def run_agent_diagnostic(db_path=None, *, ai_config_service=None,
                         mcp_config_path=None, sandbox_config_path=None) -> tuple[int, dict]:
    """Return (exit_code, bounded safe report); never create/migrate a database."""
    try:
        conn = get_raw_connection(db_path, read_only=True)
    except (OSError, sqlite3.Error):
        return 2, {"database": "unavailable", "result": "CORE_UNAVAILABLE"}
    try:
        version = get_schema_version(conn)
        if version != SCHEMA_VERSION:
            return 2, {"database": "migration_required", "schema": version,
                       "fingerprint": FINGERPRINT_VERSION, "result": "CORE_UNAVAILABLE"}
        if ai_config_service is None:
            from ..ai.config_service import AIConfigService
            ai_config_service = AIConfigService(conn=conn)
        status = build_agent_capability_status(
            ai_config_service, approvals_enabled=True,
            mcp_config_path=mcp_config_path, sandbox_config_path=sandbox_config_path,
        )
        from ..agent.mcp.config import MCPConfigError, load_mcp_config
        from ..agent.sandbox.config import SandboxConfigError, load_sandbox_config
        from ..agent.skills.learning import build_default_agent_skill_registry
        from ..agent.tools.learning import build_learning_tool_registry
        from ..agent.approval.provider import AgentApprovalProvider
        try:
            mcp = load_mcp_config(mcp_config_path)
            enabled_servers = [server for server in mcp.servers if server.enabled]
            allowed_count = sum(len(server.allowed_tools) for server in enabled_servers)
        except (MCPConfigError, OSError):
            enabled_servers, allowed_count = [], 0
        try:
            sandbox = load_sandbox_config(sandbox_config_path)
            file_tools = sandbox.enabled and sandbox.file_tools
        except (SandboxConfigError, OSError):
            file_tools = False
        from ..database.task_workspace_repository import TaskWorkspaceRepository
        workspace_counts = TaskWorkspaceRepository(conn).count_by_kind()
        report = {
            "database": "ok", "schema": version, "fingerprint": FINGERPRINT_VERSION,
            "ai": "configured" if status.model_configured else "unconfigured",
            "native_tools": len(build_learning_tool_registry(*([object()] * 6)).names()),
            "skills": len(build_default_agent_skill_registry().all()),
            "approval_actions": len(AgentApprovalProvider(object()).compose(None).names()),
            "mcp_enabled_servers": len(enabled_servers),
            "mcp_allowed_tools": allowed_count,
            "sandbox_file_tools": file_tools,
            "sandbox_execution_configured": status.sandbox_execution_configured,
            "managed_workspaces": workspace_counts["managed"],
            "local_workspaces": workspace_counts["local"],
            "sessions": conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0],
            "messages": conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0],
            "pending_approvals": conn.execute(
                "SELECT COUNT(*) FROM agent_approval_requests WHERE status='pending'"
            ).fetchone()[0],
            "warnings": list(status.warnings),
        }
        code = 2 if not status.model_configured else 1 if status.warnings else 0
        report["result"] = ("CORE_UNAVAILABLE" if code == 2 else
                            "DEGRADED" if code == 1 else "OK")
        return code, report
    except Exception:  # no filesystem paths / exception bodies in diagnostics
        return 2, {"database": "unavailable", "result": "CORE_UNAVAILABLE"}
    finally:
        conn.close()


def format_agent_diagnostic(data: dict) -> str:
    """Only constant labels and sanitized booleans/counts, never config values."""
    lines = ["Study-Agent Agent Diagnostic", "", "Database",
             f"  status: {data.get('database', 'unavailable')}"]
    if "schema" in data:
        lines += [f"  schema: {data['schema']}", f"  fingerprint: {data['fingerprint']}"]
    if "ai" in data:
        lines += ["AI", f"  active profile: {data['ai']}", "Agent",
                  f"  native tools: {data['native_tools']}",
                  f"  skills: {data['skills']}",
                  f"  approval actions: {data['approval_actions']}",
                  "MCP", f"  configured servers: {data['mcp_enabled_servers']}",
                  f"  allowlisted tools: {data['mcp_allowed_tools']}",
                  "Workspace", f"  managed bindings: {data['managed_workspaces']}",
                  f"  local bindings: {data['local_workspaces']}",
                  "Sandbox", f"  file tools: {'enabled' if data['sandbox_file_tools'] else 'disabled'}",
                  f"  execution configured: {'yes' if data['sandbox_execution_configured'] else 'no'}",
                  "Storage", f"  sessions: {data['sessions']}",
                  f"  messages: {data['messages']}",
                  f"  pending approvals: {data['pending_approvals']}"]
    for code in data.get("warnings", ()):
        lines.append(f"  warning: {code}")
    lines.append(f"Result: {data['result']}")
    return "\n".join(lines)
