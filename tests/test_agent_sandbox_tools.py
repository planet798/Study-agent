"""Five fixed Sandbox tools, task scoping, JSON-safe results and failure envelopes."""

from __future__ import annotations

import json
from app.agent.sandbox.config import SandboxConfig
from app.agent.sandbox.provider import SandboxProvider
from app.agent.sandbox.workspace import SandboxWorkspace
from app.agent.workspace import AgentWorkspaceSpec


def _managed(root, task_id):
    return AgentWorkspaceSpec("managed", root / f"task_{task_id}", True, True, True)
from app.agent.tools.base import AgentToolContext


def test_file_tools_are_task_scoped_and_json_safe(tmp_path):
    provider = SandboxProvider(
        SandboxConfig(enabled=True, file_tools=True),
        workspace_root=tmp_path / "agent_workspaces",
    )
    context_a = AgentToolContext(session_id=1, task_id=42)
    with provider.open_turn(context_a, workspace_spec=_managed(tmp_path / "agent_workspaces", 42)) as scope:
        assert scope.registry.names() == (
            "sandbox_list_files", "sandbox_read_file", "sandbox_write_file",
            "sandbox_make_directory",
        )
        assert scope.registry.execute_raw(
            "sandbox_make_directory", context_a, '{"path":"src"}'
        ) == {"ok": True, "data": {"path": "src", "created": True}}
        created = scope.registry.execute_raw(
            "sandbox_write_file",
            context_a,
            json.dumps({"path": "src/main.py", "content": "print('hello')"}),
        )
        assert created["ok"] is True
        read = scope.registry.execute_raw(
            "sandbox_read_file", context_a, '{"path":"src/main.py"}'
        )
        assert read["data"]["content"] == "print('hello')"
        listing = scope.registry.execute_raw(
            "sandbox_list_files", context_a, '{"path":"src"}'
        )
        assert listing["data"]["entries"] == [
            {"name": "main.py", "type": "file", "size": 14}
        ]
        for name in scope.registry.names():
            json.dumps(scope.registry.execute_raw(name, context_a, "{}"), allow_nan=False)

    context_b = AgentToolContext(session_id=2, task_id=43)
    with provider.open_turn(context_b, workspace_spec=_managed(tmp_path / "agent_workspaces", 43)) as scope_b:
        listing_b = scope_b.registry.execute_raw(
            "sandbox_list_files", context_b, '{"path":"."}'
        )
        assert listing_b["data"]["entries"] == []
    assert (tmp_path / "agent_workspaces" / "task_42" / "src" / "main.py").exists()
    assert not (tmp_path / "agent_workspaces" / "task_43" / "src").exists()


def test_overwrite_is_explicit_and_no_delete_tool_exists(tmp_path):
    provider = SandboxProvider(
        SandboxConfig(enabled=True), workspace_root=tmp_path / "ws"
    )
    ctx = AgentToolContext(1, 10)
    with provider.open_turn(ctx, workspace_spec=_managed(tmp_path / "ws", ctx.task_id)) as scope:
        scope.registry.execute_raw(
            "sandbox_write_file", ctx, '{"path":"a.txt","content":"one"}'
        )
        denied = scope.registry.execute_raw(
            "sandbox_write_file", ctx, '{"path":"a.txt","content":"two"}'
        )
        assert denied["ok"] is False
        assert denied["error"]["code"] == "sandbox_workspace_error"
        replaced = scope.registry.execute_raw(
            "sandbox_write_file",
            ctx,
            '{"path":"a.txt","content":"two","overwrite":true}',
        )
        assert replaced["ok"] is True
        assert replaced["data"]["overwritten"] is True
        assert "sandbox_delete" not in scope.registry.names()


def test_model_cannot_select_task_id_or_workspace_root(tmp_path):
    provider = SandboxProvider(
        SandboxConfig(enabled=True), workspace_root=tmp_path / "ws"
    )
    ctx = AgentToolContext(1, 12)
    with provider.open_turn(ctx, workspace_spec=_managed(tmp_path / "ws", ctx.task_id)) as scope:
        result = scope.registry.execute_raw(
            "sandbox_write_file",
            ctx,
            '{"path":"../task_99/pwn.txt","content":"x","task_id":99}',
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_arguments"
        assert not (tmp_path / "ws" / "task_99").exists()


def test_path_escape_is_controlled_tool_result(tmp_path):
    provider = SandboxProvider(
        SandboxConfig(enabled=True), workspace_root=tmp_path / "ws"
    )
    ctx = AgentToolContext(1, 12)
    with provider.open_turn(ctx, workspace_spec=_managed(tmp_path / "ws", ctx.task_id)) as scope:
        result = scope.registry.execute_raw(
            "sandbox_read_file", ctx, '{"path":"../../etc/passwd"}'
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "sandbox_path_invalid"
        assert str(tmp_path) not in result["error"]["message"]


def test_file_tool_limits_are_enforced_and_output_stays_bounded(tmp_path):
    config = SandboxConfig(enabled=True, max_file_chars=8)
    provider = SandboxProvider(config, workspace_root=tmp_path / "ws")
    ctx = AgentToolContext(1, 12)
    with provider.open_turn(ctx, workspace_spec=_managed(tmp_path / "ws", ctx.task_id)) as scope:
        too_large = scope.registry.execute_raw(
            "sandbox_write_file", ctx,
            json.dumps({"path": "large.txt", "content": "x" * 9}),
        )
        assert too_large["ok"] is False
        assert too_large["error"]["code"] == "sandbox_file_too_large"
        reader = scope.registry.get("sandbox_read_file")
        (reader.workspace.ensure_workspace() / "read.txt").write_text(
            "x" * 30, encoding="utf-8"
        )  # simulate an oversized pre-existing workspace file
        read = scope.registry.execute_raw(
            "sandbox_read_file", ctx, '{"path":"read.txt"}'
        )
        assert read["data"]["content"] == "x" * 8
        assert read["data"]["truncated"] is True
        assert len(json.dumps(read)) < 500


def test_file_tools_are_application_state_independent():
    from pathlib import Path
    import app.agent.sandbox.tools as tools_module

    source = Path(tools_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "TaskService", "AssessmentService", "CapabilityService", "Repository",
        "sqlite3", "UPDATE knowledge_points", "capability_evidence",
    ):
        assert forbidden not in source
