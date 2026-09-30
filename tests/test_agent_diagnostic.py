"""Offline Agent diagnostics: no migrations, external IO, or secret disclosure."""
import json

import pytest
from types import SimpleNamespace
from app.agent.status import build_agent_capability_status
from app.diagnostics.agent_diagnostic import run_agent_diagnostic, format_agent_diagnostic


class AI:
    def __init__(self, configured=True): self.configured = configured
    def get_runtime_config(self): return SimpleNamespace(is_configured=self.configured)


def test_status_reads_local_configuration_without_connecting_or_probing(tmp_path, monkeypatch):
    import app.agent.mcp.client as mcp_client
    from app.agent.sandbox.backend import DockerSandboxBackend
    monkeypatch.setattr(mcp_client.MCPClientBridge, "__init__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MCP connected")))
    monkeypatch.setattr(DockerSandboxBackend, "probe", lambda *a: (_ for _ in ()).throw(AssertionError("Docker probed")))
    missing_mcp, missing_sandbox = tmp_path / "missing-mcp", tmp_path / "missing-sandbox"
    status = build_agent_capability_status(AI(), approvals_enabled=True,
        mcp_config_path=missing_mcp, sandbox_config_path=missing_sandbox)
    assert status.model_configured and status.native_tools and status.approvals_enabled
    assert not status.mcp_configured and not status.sandbox_configured and status.warnings == ()
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"version":1,"servers":[]}', encoding="utf-8")
    sandbox = tmp_path / "sandbox.json"
    sandbox.write_text('{"version":1,"enabled":true,"file_tools":true,"execution":{"enabled":true}}', encoding="utf-8")
    status = build_agent_capability_status(AI(), mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert status.sandbox_execution_configured and status.sandbox_configured
    mcp.write_text('bad private url https://user:pass@example.com', encoding="utf-8")
    sandbox.write_text('bad PRIVATE_DOCKER_PATH', encoding="utf-8")
    status = build_agent_capability_status(AI(), mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert status.warnings == ("mcp_config_invalid", "sandbox_config_invalid")
    assert "user:pass" not in str(status) and "PRIVATE_DOCKER_PATH" not in str(status)


def test_diagnostic_minimal_read_only_and_optional_degradation(conn, tmp_path):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    before = conn.total_changes
    mcp, sandbox = tmp_path / "mcp.json", tmp_path / "sandbox.json"
    code, report = run_agent_diagnostic(path, ai_config_service=AI(),
        mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert code == 0 and report["result"] == "OK"
    assert (report["schema"], report["fingerprint"]) == (25, 7)
    assert (report["native_tools"], report["skills"], report["approval_actions"]) == (6, 6, 3)
    assert report["sessions"] == report["messages"] == report["pending_approvals"] == 0
    assert conn.total_changes == before
    mcp.write_text('BAD MCP KEY: SECRET_API_KEY', encoding="utf-8")
    sandbox.write_text('BAD SANDBOX PATH: /private/secret', encoding="utf-8")
    code, degraded = run_agent_diagnostic(path, ai_config_service=AI(),
        mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert code == 1 and degraded["result"] == "DEGRADED"
    assert degraded["warnings"] == ["mcp_config_invalid", "sandbox_config_invalid"]
    output = format_agent_diagnostic(degraded)
    for secret in ("SECRET_API_KEY", "/private/secret", str(path)):
        assert secret not in output and secret not in json.dumps(degraded)
    code, unavailable = run_agent_diagnostic(path, ai_config_service=AI(False),
        mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert code == 2 and unavailable["ai"] == "unconfigured"
    assert conn.total_changes == before


@pytest.mark.migration  # creates v23 and verifies migration gate, not all diagnostics
def test_diagnostic_rejects_old_or_missing_database_without_migration(tmp_path):
    import sqlite3
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise
    missing = tmp_path / "missing.db"
    code, _ = run_agent_diagnostic(missing)
    assert code == 2 and not missing.exists()
    path = tmp_path / "legacy.db"
    conn = get_raw_connection(path)
    migrate_stepwise(conn, target=23)
    conn.close()
    code, report = run_agent_diagnostic(path, ai_config_service=AI())
    assert code == 2 and report["database"] == "migration_required"
    ro = sqlite3.connect(path)
    assert ro.execute("PRAGMA user_version").fetchone()[0] == 23
    ro.close()


def test_cli_runs_before_gui_and_prints_only_static_counts(conn, capsys, monkeypatch):
    import sys
    from app.main import main
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(sys, "argv", ["app.main", "agent-diagnostic", "--db", path, "--json"])
    code = main()
    payload = json.loads(capsys.readouterr().out)
    assert code in (0, 2)  # CI may not configure an AI Profile
    assert payload["database"] == "ok"
    assert payload["schema"] == 25
