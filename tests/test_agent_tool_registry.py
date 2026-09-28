"""AgentToolRegistry strict protocol, schemas, error envelopes, and read-only guard."""

from __future__ import annotations

import json

import pytest

from app.agent.tools.base import (
    AgentTool,
    AgentToolContext,
    AgentToolSpec,
    EMPTY_OBJECT_SCHEMA,
)
from app.agent.tools.registry import AgentToolRegistry


class EchoTool(AgentTool):
    def __init__(self, *, name="echo", result=None, error=None, read_only=True):
        self._spec = AgentToolSpec(
            name, "read values", EMPTY_OBJECT_SCHEMA, read_only=read_only
        )
        self.result = {"message": "ok"} if result is None else result
        self.error = error
        self.calls = []

    @property
    def spec(self):
        return self._spec

    def execute(self, context, arguments):
        self.calls.append((context, arguments))
        if self.error:
            raise self.error
        return self.result


def test_registration_names_and_model_tool_schema():
    registry = AgentToolRegistry()
    tool = EchoTool()
    registry.register(tool)
    assert registry.names() == ("echo",)
    assert registry.get("echo") is tool
    assert registry.model_tools() == ({
        "type": "function",
        "function": {
            "name": "echo",
            "description": "read values",
            "parameters": {
                "type": "object", "properties": {},
                "additionalProperties": False,
            },
        },
    },)


def test_duplicate_registration_is_rejected_without_overwrite():
    registry = AgentToolRegistry()
    first, second = EchoTool(), EchoTool()
    registry.register(first)
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(second)
    assert registry.get("echo") is first


def test_registry_rejects_non_read_only_tool():
    registry = AgentToolRegistry()
    with pytest.raises(ValueError, match="mutation scope"):
        registry.register(EchoTool(read_only=False))


def test_approval_and_sandbox_scopes_are_explicit_and_isolated():
    class Mutation(EchoTool):
        def __init__(self, name, scope):
            super().__init__(name=name)
            self._spec = AgentToolSpec(name, "request", EMPTY_OBJECT_SCHEMA,
                                       read_only=False, mutation_scope=scope)

    approval = Mutation("request_complete_current_task", "approval")
    sandbox = Mutation("sandbox_write_file", "sandbox")
    for scopes, tool in (((), approval), (("sandbox",), approval),
                         (("approval",), sandbox)):
        with pytest.raises(ValueError, match="scope"):
            AgentToolRegistry(allowed_mutation_scopes=scopes).register(tool)
    only_approval = AgentToolRegistry(allowed_mutation_scopes=("approval",))
    only_approval.register(approval)
    combined = AgentToolRegistry(allowed_mutation_scopes=("approval", "sandbox"))
    combined.register(approval)
    combined.register(sandbox)
    assert combined.names() == ("request_complete_current_task", "sandbox_write_file")
    for scope in ("application", "database", "task", "mastery", "capability", "mcp_write"):
        with pytest.raises(ValueError, match="only sandbox and approval"):
            AgentToolRegistry(allowed_mutation_scopes=(scope,))


def test_unknown_tool_is_controlled_error_envelope():
    result = AgentToolRegistry().execute(
        "missing", AgentToolContext(session_id=1, task_id=3), {}
    )
    assert result == {
        "ok": False,
        "error": {"code": "tool_not_found", "message": "Tool is not available: missing."},
    }
    json.dumps(result)


@pytest.mark.parametrize("raw", ['[1]', '"text"', "123", "null", "bad json", ""])
def test_raw_arguments_must_be_json_object(raw):
    tool = EchoTool()
    registry = AgentToolRegistry()
    registry.register(tool)
    result = registry.execute_raw(
        "echo", AgentToolContext(session_id=1, task_id=2), raw
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert tool.calls == []


def test_empty_object_is_valid_and_extra_task_id_is_rejected():
    tool = EchoTool()
    registry = AgentToolRegistry()
    registry.register(tool)
    context = AgentToolContext(session_id=10, task_id=21)

    assert registry.execute_raw("echo", context, "{}") == {
        "ok": True, "data": {"message": "ok"}
    }
    assert tool.calls == [(context, {})]

    result = registry.execute_raw("echo", context, '{"task_id":999}')
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert tool.calls == [(context, {})]


def test_handler_exception_is_hidden_and_result_is_json_safe():
    tool = EchoTool(error=RuntimeError("secret SQL traceback detail"))
    registry = AgentToolRegistry()
    registry.register(tool)
    result = registry.execute("echo", AgentToolContext(2, 9), {})
    assert result == {
        "ok": False,
        "error": {
            "code": "tool_execution_failed",
            "message": "Tool execution failed.",
        },
    }
    assert "secret" not in json.dumps(result)


def test_non_json_safe_service_result_becomes_controlled_error():
    registry = AgentToolRegistry()
    registry.register(EchoTool(result=object()))
    result = registry.execute("echo", AgentToolContext(1, 1), {})
    assert result["ok"] is False
    assert result["error"]["code"] == "tool_execution_failed"
    json.dumps(result)
