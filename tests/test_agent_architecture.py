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


def test_agent_tables_are_growth_not_history():
    """v21 新增表属于 growth（新正式数据模型），不是 pre-v20 历史表。"""
    from app.diagnostics.release_migration import (
        FINGERPRINT_COLUMNS,
        FINGERPRINT_VERSION,
        GROWTH_TABLES,
        HISTORY_TABLES,
    )

    for table in ("agent_sessions", "agent_messages"):
        assert table in GROWTH_TABLES
        assert table not in HISTORY_TABLES
        assert table not in FINGERPRINT_COLUMNS
    # 未修改历史指纹定义
    assert FINGERPRINT_VERSION == 4


def test_agent_package_has_no_forbidden_modules():
    from importlib.util import find_spec
    for module in (
        "app.agent.tools", "app.agent.skills", "app.agent.mcp",
        "app.agent.sandbox", "app.agent.memory", "app.agent.trace",
    ):
        assert find_spec(module) is None, module


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
