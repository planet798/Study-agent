"""The request tool is a metadata-only approval capability."""
import json
from app.agent.approval.service import AgentApprovalService
from app.agent.approval.tools import RequestCompleteCurrentTaskTool
from app.agent.tools.base import AgentToolContext
from app.agent.tools.registry import AgentToolRegistry
from app.database.agent_approval_repository import AgentApprovalRepository
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


def test_approval_provider_preserves_sandbox_scope_without_enabling_mcp_write(conn):
    from app.agent.approval.provider import AgentApprovalProvider
    from app.agent.tools.base import AgentTool, AgentToolSpec, EMPTY_OBJECT_SCHEMA

    class SandboxTool(AgentTool):
        @property
        def spec(self):
            return AgentToolSpec("sandbox_write_file", "sandbox only", EMPTY_OBJECT_SCHEMA,
                                 read_only=False, mutation_scope="sandbox")
        def execute(self, context, arguments):
            return {}

    base = AgentToolRegistry(allowed_mutation_scopes=("sandbox",))
    base.register(SandboxTool())
    provider = AgentApprovalProvider(AgentApprovalService(
        AgentApprovalRepository(conn), TaskService(TaskRepository(conn))))
    registry = provider.compose(base)
    assert registry.allowed_mutation_scopes == ("approval", "sandbox")
    assert registry.get("sandbox_write_file").spec.mutation_scope == "sandbox"
    assert registry.get("request_complete_current_task").spec.mutation_scope == "approval"
    assert base.allowed_mutation_scopes == ("sandbox",)


def test_three_request_tools_have_fixed_scopes_and_schemas(conn):
    from app.agent.approval.tools import (RequestStartAssessmentTool,
        RequestSaveLearningNoteTool)
    service = AgentApprovalService(AgentApprovalRepository(conn), TaskService(TaskRepository(conn)))
    assessment = RequestStartAssessmentTool(service).spec
    note = RequestSaveLearningNoteTool(service).spec
    for spec in (assessment, note):
        assert not spec.read_only and spec.mutation_scope == "approval"
    assert assessment.parameters == {"type": "object", "properties": {}, "additionalProperties": False}
    assert note.parameters["required"] == ["title", "content"]
    assert note.parameters["additionalProperties"] is False
    assert set(note.parameters["properties"]) == {"title", "content"}
    from app.agent.approval.provider import AgentApprovalProvider
    provider = AgentApprovalProvider(service)
    assert provider.compose(None).names() == (
        "request_complete_current_task", "request_start_assessment", "request_save_learning_note"
    )


def test_approval_tool_declares_empty_schema_and_never_completes_task(conn):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("approval tool", scheduled_date="2026-09-15")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    assistant = agent.add_message(session["id"], "assistant", "", tool_calls_json=(
        '[{"id":"call-1","name":"request_complete_current_task","arguments":"{}"}]'))
    service = AgentApprovalService(AgentApprovalRepository(conn), task_service)
    tool = RequestCompleteCurrentTaskTool(service)
    spec = tool.spec
    assert not spec.read_only and spec.mutation_scope == "approval"
    assert spec.parameters == {"type":"object", "properties":{}, "additionalProperties":False}
    assert "does NOT complete" in spec.description
    registry = AgentToolRegistry(allowed_mutation_scopes=("approval",))
    registry.register(tool)
    failed = registry.execute_raw(spec.name, AgentToolContext(session["id"], task.id), "{}")
    assert failed["ok"] is False
    result = registry.execute_raw(spec.name,
        AgentToolContext(session["id"], task.id, assistant["id"], "call-1"), "{}")
    assert result["ok"] and result["data"]["approval_required"] is True
    assert task_service.get_status(task.id) == "active"
    assert registry.execute_raw(spec.name, AgentToolContext(session["id"], task.id), '{"unexpected":1}')["ok"] is False
