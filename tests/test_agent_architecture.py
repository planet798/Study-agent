"""Agent-1 架构边界测试。

验证稳定事实（避免脆弱的全文快照）：
- AgentRuntime 不持有 SQLite connection；
- AgentRuntime 依赖 AgentSessionService + AgentModelClient；
- AgentSessionService 通过 TaskService 校验 task（不直接用 TaskRepository）；
- legacy AIClient 接口仍存在且未改签名；
- AgentModelClient 是独立 interface；
- Sidebar 仍无 Agent page。
"""

from __future__ import annotations

import inspect
import sqlite3

from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.ai.agent_protocol import AgentModelClient, ModelRequest, ModelResponse
from app.ai.client import AdaptiveAIClient, DeepSeekClient
from app.ai.interface import AIClient
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService
from app.ui.app_shell import PAGE_SPECS, PageKey


def test_agent_runtime_has_no_sqlite_connection():
    runtime = AgentRuntime.__init__
    params = inspect.signature(runtime).parameters
    assert set(params) >= {"self", "session_service", "model_client"}
    for param in params.values():
        default = param.default
        assert not isinstance(default, sqlite3.Connection)
    # 类属性 / 实例依赖不含 connection
    source = inspect.getsource(AgentRuntime)
    assert "sqlite3" not in source
    assert "AgentRepository" not in source
    assert "TaskRepository" not in source


def test_agent_session_service_depends_on_task_service_not_task_repository():
    params = set(inspect.signature(AgentSessionService.__init__).parameters)
    assert params == {"self", "agent_repo", "task_service"}
    source = inspect.getsource(AgentSessionService)
    assert "TaskRepository" not in source
    # 通过 TaskService 校验 task 存在性
    assert "task_service.get_task" in source or "get_task(" in source


def test_legacy_ai_client_interface_is_unchanged():
    assert issubclass(AIClient, object)
    chat = inspect.signature(AIClient.chat)
    assert list(chat.parameters)[:3] == ["self", "system_prompt", "user_prompt"]
    assert issubclass(DeepSeekClient, AIClient)
    assert issubclass(AdaptiveAIClient, AIClient)
    for client in (DeepSeekClient, AdaptiveAIClient):
        assert "chat" in vars(client)


def test_agent_model_client_is_a_separate_interface():
    assert issubclass(AgentModelClient, object)
    assert not issubclass(AgentModelClient, AIClient)
    assert hasattr(AgentModelClient, "complete")
    assert not hasattr(AgentModelClient, "chat")
    assert ModelRequest(messages=()).tools == ()
    assert ModelResponse(content="x").tool_calls == ()


def test_agent_repository_is_persistence_only():
    source = inspect.getsource(AgentRepository)
    for forbidden in ("AIServiceError", "mastery", "capability_evidence",
                      "complete_task", "prompt"):
        assert forbidden not in source.lower().replace("不操作 mastery", "")


def test_sidebar_has_no_agent_page():
    keys = [spec.key for spec in PAGE_SPECS]
    assert keys == [PageKey.TODAY, PageKey.ROUTES, PageKey.PRACTICE, PageKey.SETTINGS]
    assert not any("agent" in str(k).lower() for k in keys)

    # Workspace exists as an internal UI component, never as navigation metadata.
    from app.ui.agent_workspace_page import AgentWorkspacePage
    assert AgentWorkspacePage is not None
    assert not any("workspace" in str(k).lower() for k in keys)


def test_agent_tables_are_protected_history_not_growth():
    """v21 Agent 表是正式历史数据，参与 verifier fingerprint。"""
    from app.diagnostics.release_migration import (
        FINGERPRINT_COLUMNS,
        FINGERPRINT_VERSION,
        GROWTH_TABLES,
        HISTORY_TABLES,
    )

    for table in ("agent_sessions", "agent_messages"):
        assert table in HISTORY_TABLES
        assert table not in GROWTH_TABLES
        assert table in FINGERPRINT_COLUMNS
    assert FINGERPRINT_VERSION == 5


def test_agent_skills_and_mcp_are_separate_from_career_skills_and_future_modules_absent():
    from importlib.util import find_spec
    from pathlib import Path

    from app.agent.skills import AgentSkill, AgentSkillRegistry, AgentSkillSelector
    from app.services.skill_service import SkillService
    import app.agent.mcp as mcp_package
    import app.agent.memory as memory_package
    import app.agent.skills as skills_package

    assert find_spec("app.agent.skills") is not None
    assert find_spec("app.agent.mcp") is not None
    assert AgentSkillRegistry is not SkillService
    assert AgentSkill is not SkillService
    assert AgentSkillSelector is not SkillService
    assert find_spec("app.agent.sandbox") is not None
    assert find_spec("app.agent.memory") is not None
    assert find_spec("app.agent.trace") is None

    package_path = Path(skills_package.__file__).parent
    sources = "\\n".join(p.read_text(encoding="utf-8") for p in package_path.glob("*.py"))
    for forbidden in (
        "SkillService", "SkillRepository", "skill_repository", "sqlite3",
        "AgentToolRegistry",
    ):
        assert forbidden not in sources

    memory_path = Path(memory_package.__file__).parent
    memory_sources = "\\n".join(
        p.read_text(encoding="utf-8") for p in memory_path.glob("*.py")
    )
    for forbidden in (
        "TaskRepository", "AssessmentRepository", "CapabilityRepository",
        "SkillService", "MCP Client", "Sandbox Backend", "PySide6", "QtWidgets",
    ):
        assert forbidden.lower() not in memory_sources.lower()

    mcp_path = Path(mcp_package.__file__).parent
    mcp_sources = "\\n".join(p.read_text(encoding="utf-8") for p in mcp_path.glob("*.py"))
    for forbidden in ("SkillService", "SkillRepository", "sqlite3", "SELECT ", "UPDATE "):
        assert forbidden.lower() not in mcp_sources.lower()

    import app.agent.sandbox as sandbox_package
    sandbox_path = Path(sandbox_package.__file__).parent
    sandbox_sources = "\\n".join(
        p.read_text(encoding="utf-8") for p in sandbox_path.glob("*.py")
    )
    for forbidden in (
        "TaskRepository", "AssessmentRepository", "CapabilityEvidenceRepository",
        "TaskService", "AssessmentService", "CapabilityService", "sqlite3",
    ):
        assert forbidden.lower() not in sandbox_sources.lower()


def test_native_registry_contains_only_read_only_tools():
    from app.agent.tools.learning import build_learning_tool_registry

    registry = build_learning_tool_registry(*([object()] * 6))
    assert registry.names() == (
        "get_task_context", "get_route_context", "get_topic_context",
        "get_learning_components", "get_mastery", "get_capability",
    )
    assert all(registry.get(name).spec.read_only for name in registry.names())
    assert all(
        spec["function"]["parameters"] == {
            "type": "object", "properties": {}, "additionalProperties": False,
        }
        for spec in registry.model_tools()
    )


def test_learning_tool_handlers_depend_on_services_not_database():
    from app.agent.tools.learning import (
        GetCapabilityTool, GetLearningComponentsTool, GetMasteryTool,
        GetRouteContextTool, GetTaskContextTool, GetTopicContextTool,
    )

    for tool_type in (
        GetTaskContextTool, GetRouteContextTool, GetTopicContextTool,
        GetLearningComponentsTool, GetMasteryTool, GetCapabilityTool,
    ):
        source = inspect.getsource(tool_type)
        assert "sqlite3" not in source
        assert "SELECT " not in source.upper()
        assert "Repository" not in source
        assert "conn" not in inspect.signature(tool_type.__init__).parameters


def test_agent7_schema_is_v22_and_fingerprint_remains_v5():
    from app.database.schema import SCHEMA_VERSION
    from app.diagnostics.release_migration import FINGERPRINT_VERSION

    assert SCHEMA_VERSION == 22
    assert FINGERPRINT_VERSION == 5


def test_vertical_slice_task_to_runtime(conn, repo):
    """TaskService → AgentSessionService → AgentRuntime → FakeAgentModelClient。"""
    from app.agent.session import AgentSessionService
    from app.ai.agent_protocol import AgentModelClient, ModelResponse

    class _Fake(AgentModelClient):
        def is_configured(self) -> bool:
            return True

        def complete(self, request):
            return ModelResponse(content="继续加油", finish_reason="stop")

    task_service = TaskService(TaskRepository(conn))
    session_service = AgentSessionService(AgentRepository(conn), task_service)
    runtime = AgentRuntime(session_service, _Fake())

    task = task_service.create_task(title="切片任务", scheduled_date="2026-09-15")
    session = session_service.start_or_resume(task.id)
    result = runtime.send_message(session["id"], "我卡在 attention")

    assert result.assistant_message["content"] == "继续加油"
    assert [m["role"] for m in session_service.messages(session["id"])] == [
        "user", "assistant"
    ]
    assert task_service.get_task(task.id).status == "active"
