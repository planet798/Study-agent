"""Application composition: basic files do not depend on advanced sandbox.json."""

from __future__ import annotations

import json

import pytest

from app.ai.agent_protocol import ModelResponse, ModelToolCall
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.agent.session import AgentSessionService
from app.services.task_service import TaskService


@pytest.mark.parametrize("invalid_config", [False, True])
def test_production_managed_file_write_without_sandbox_config(conn, tmp_path, monkeypatch, invalid_config):
    from app.agent.sandbox import provider as provider_module
    from app.services import workspace_service as service_module
    from app.main import build_agent_runtime

    root = tmp_path / "agent_workspaces"
    monkeypatch.setattr(provider_module, "default_sandbox_workspace_root", lambda: root)
    monkeypatch.setattr(service_module, "default_sandbox_workspace_root", lambda: root)
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task(title="Workspace production", scheduled_date="2026-10-01", source="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    config_path = tmp_path / "sandbox.json"
    if invalid_config:
        config_path.write_text('{"unknown":"unsafe"}', encoding="utf-8")
    runtime = build_agent_runtime(
        conn, db_path=db_path, mcp_config_path=tmp_path / "missing-mcp.json",
        sandbox_config_path=config_path,
    )
    runtime.workspace_service.use_managed(task.id)

    class Model:
        def __init__(self):
            self.requests = []

        def is_configured(self):
            return True

        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(content="", finish_reason="tool_calls", tool_calls=(
                    ModelToolCall("mkdir", "sandbox_make_directory", '{"path":"notes"}'),
                ))
            if len(self.requests) == 2:
                return ModelResponse(content="", finish_reason="tool_calls", tool_calls=(
                    ModelToolCall("write", "sandbox_write_file", json.dumps({
                        "path": "notes/loss_mask.md", "content": "Loss mask explanation",
                    })),
                ))
            return ModelResponse(content="Created `notes/loss_mask.md`", finish_reason="stop")

    model = Model()
    runtime.model_client = model
    runtime.send_message(session["id"], "Create notes/loss_mask.md")
    assert (root / f"task_{task.id}" / "notes" / "loss_mask.md").read_text() == "Loss mask explanation"
    exposed = {tool["function"]["name"] for tool in model.requests[0].tools}
    assert "sandbox_write_file" in exposed
    assert "sandbox_run" not in exposed
    assert str(root) not in repr(model.requests)
