"""MainWindow → internal Agent Workspace → worker/runtime integration tests."""

from __future__ import annotations

import json
import threading

import pytest
from PySide6.QtWidgets import QLabel

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


class _ConfiguredAI:
    def __init__(self, configured=True):
        self.configured = configured

    def get_runtime_config(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            is_configured=self.configured, profile_name="", source="none"
        )

    def list_profiles(self):
        return []

    def profile_count(self):
        return 0

    def get_active_profile(self):
        return None

    def is_legacy_configured(self):
        return False


def _db_path(conn):
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _make_window(conn, task_service, date_service, session_service,
                 runtime_factory, configured=True):
    from app.ui.main_window import MainWindow

    return MainWindow(
        task_service=task_service,
        date_service=date_service,
        today_provider=lambda: "2026-01-05",
        agent_session_service=session_service,
        agent_runtime_factory=runtime_factory,
        ai_config_service=_ConfiguredAI(configured),
        db_path=_db_path(conn),
    )


def _worker_factory_with_behavior(outcome, entered=None, release=None):
    """Factory creates a Service graph from the worker's fresh connection."""
    from app.database.repository import TaskRepository
    from app.services.task_service import TaskService

    def factory(fresh_conn):
        fresh_tasks = TaskService(TaskRepository(fresh_conn))
        fresh_sessions = AgentSessionService(
            AgentRepository(fresh_conn), fresh_tasks
        )

        class Runtime:
            def send_message(self, session_id, text):
                fresh_sessions.append_user_message(session_id, text)
                if entered is not None:
                    entered.set()
                if release is not None and not release.wait(5):
                    raise RuntimeError("test worker release timed out")
                if isinstance(outcome, Exception):
                    raise outcome
                fresh_sessions.append_assistant_message(session_id, outcome)
                return {"session_id": session_id}

        return Runtime()

    return factory


def test_today_task_opens_internal_workspace_and_resumes_same_session(
    qtbot, conn, repo, task_service, date_service
):
    from app.ui.app_shell import PAGE_SPECS, PageKey

    task_a = repo.create(
        title="Task A", description="A description", scheduled_date="2026-01-05",
        source="manual", task_type="manual",
    )
    task_b = repo.create(
        title="Task B", scheduled_date="2026-01-05", source="generated",
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    factory_calls = []
    window = _make_window(
        conn, task_service, date_service, sessions,
        lambda fresh: factory_calls.append(fresh), configured=False,
    )
    qtbot.addWidget(window)

    assert [spec.key for spec in PAGE_SPECS] == [
        PageKey.TODAY, PageKey.ROUTES, PageKey.PRACTICE, PageKey.SETTINGS,
    ]
    assert window.agent_workspace_page_index == window.stack.count() - 1
    assert window.stack.widget(window.agent_workspace_page_index) is window.agent_workspace_page
    assert window.sidebar.current_key() == PageKey.TODAY.value
    assert factory_calls == []  # opening a Workspace does not call the model/runtime

    widget_a = next(w for w in window._task_widgets if w.task().id == task_a.id)
    assert widget_a.start_study_btn.text() == "开始学习"
    widget_a.start_study_btn.click()
    session_a = sessions.get_active_for_task(task_a.id)
    assert session_a is not None
    assert window.stack.currentWidget() is window.agent_workspace_page
    assert window.page_header.title() == "学习会话"
    assert window.page_header.subtitle() == "Task A"
    assert window.sidebar.current_key() == PageKey.TODAY.value
    assert window.agent_workspace_page.task_title_label.text() == "Task A"
    assert not window.agent_workspace_page.input_edit.isEnabled()
    window._on_start_study(task_a.id)
    assert sessions.get_active_for_task(task_a.id)["id"] == session_a["id"]

    # Switching Task clears previous Session content but doesn't close it.
    sessions.append_user_message(session_a["id"], "A history")
    widget_b = next(w for w in window._task_widgets if w.task().id == task_b.id)
    widget_b.start_study_btn.click()
    session_b = sessions.get_active_for_task(task_b.id)
    assert session_b["id"] != session_a["id"]
    assert window.agent_workspace_page.current_session_id == session_b["id"]
    assert window.agent_workspace_page.task_title_label.text() == "Task B"
    assert window.agent_workspace_page.findChildren(QLabel, "AgentMessageText") == []

    window.agent_workspace_page.back_button.click()
    assert window.stack.currentWidget() is window.today_page
    assert sessions.get(session_a["id"])["status"] == "active"
    window.close()


def test_existing_history_shows_continue_and_manual_learning_is_eligible(
    qtbot, conn, repo, task_service, date_service
):
    manual = repo.create(
        title="One-off study", scheduled_date="2026-01-05", source="manual",
        task_type="manual",
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    existing = sessions.start_or_resume(manual.id)
    sessions.append_user_message(existing["id"], "already started")
    window = _make_window(conn, task_service, date_service, sessions, lambda _c: None)
    qtbot.addWidget(window)

    widget = next(w for w in window._task_widgets if w.task().id == manual.id)
    assert widget.start_study_btn.text() == "继续学习"
    widget.start_study_btn.click()
    assert window.agent_workspace_page.current_session_id == existing["id"]
    body = window.agent_workspace_page.findChildren(QLabel, "AgentMessageText")
    assert [label.text() for label in body] == ["already started"]
    window.close()


def test_worker_failure_reloads_persisted_user_message_and_shows_error(
    qtbot, conn, repo, task_service, date_service
):
    task = repo.create(
        title="Network task", scheduled_date="2026-01-05", source="generated",
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    factory = _worker_factory_with_behavior(RuntimeError("secret AI error"))
    window = _make_window(conn, task_service, date_service, sessions, factory)
    qtbot.addWidget(window)
    next(w for w in window._task_widgets if w.task().id == task.id).start_study_btn.click()
    page = window.agent_workspace_page
    page.input_edit.setPlainText("Keep this on failure")
    page.send_button.click()

    qtbot.waitUntil(
        lambda: not window._agent_inflight_sessions
        and sessions.count_messages(page.current_session_id) == 1,
        timeout=5000,
    )
    bubbles = page.findChildren(QLabel, "AgentMessageText")
    assert [bubble.text() for bubble in bubbles] == ["Keep this on failure"]
    assert not page.error_label.isHidden()
    assert "secret" not in page.error_label.text()
    assert page.busy_label.isHidden()
    window.close()


def test_stale_worker_completion_never_renders_into_another_session(
    qtbot, conn, repo, task_service, date_service
):
    task_a = repo.create(title="A", scheduled_date="2026-01-05", source="generated")
    task_b = repo.create(title="B", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    entered, release = threading.Event(), threading.Event()
    factory = _worker_factory_with_behavior(
        "A answer", entered=entered, release=release
    )
    window = _make_window(conn, task_service, date_service, sessions, factory)
    qtbot.addWidget(window)

    next(w for w in window._task_widgets if w.task().id == task_a.id).start_study_btn.click()
    page = window.agent_workspace_page
    page.input_edit.setPlainText("A question")
    page.send_button.click()
    assert entered.wait(3)
    next(w for w in window._task_widgets if w.task().id == task_b.id).start_study_btn.click()
    session_b = page.current_session_id
    assert page.task_title_label.text() == "B"
    release.set()

    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=5000)
    assert page.current_session_id == session_b
    assert page.task_title_label.text() == "B"
    assert page.findChildren(QLabel, "AgentMessageText") == []
    window.close()


def test_production_runtime_factory_builds_sqlite_services_from_fresh_conn(conn, tmp_path):
    from app.agent.context import AgentTaskContextBuilder
    from app.ai.agent_client import AdaptiveAgentModelClient
    from app.main import build_agent_runtime

    config_path = tmp_path / "mcp-disabled.json"
    config_path.write_text('{"version":1,"servers":[]}', encoding="utf-8")
    sandbox_path = tmp_path / "sandbox-disabled.json"
    sandbox_path.write_text('{"version":1,"enabled":false}', encoding="utf-8")
    runtime = build_agent_runtime(
        conn, db_path=_db_path(conn), mcp_config_path=config_path,
        sandbox_config_path=sandbox_path,
    )
    from app.agent.skills import AgentSkillSelector
    from app.agent.memory.compactor import AgentMemoryCompactor

    assert isinstance(runtime.context_builder, AgentTaskContextBuilder)
    assert isinstance(runtime.memory_compactor, AgentMemoryCompactor)
    assert runtime.memory_compactor.session_service is runtime.session_service
    assert runtime.memory_compactor.memory_repository.conn is conn
    assert runtime.memory_compactor.model_client is runtime.model_client
    assert isinstance(runtime.model_client, AdaptiveAgentModelClient)
    assert isinstance(runtime.skill_selector, AgentSkillSelector)
    assert runtime.mcp_provider is None  # explicit empty config keeps Agent-4 behavior
    assert runtime.sandbox_provider is None
    assert runtime.session_service.repo.conn is conn
    registry = runtime.tool_registry
    assert registry.get("get_task_context").task_service.repo.conn is conn
    assert registry.get("get_route_context").learning_route_service.route_repo.conn is conn
    assert registry.get("get_topic_context").study_plan_service.plan_repo.conn is conn
    assert registry.get("get_learning_components").topic_learning_service.conn is conn
    assert registry.get("get_mastery").assessment_service.assessment_repo.conn is conn
    assert registry.get("get_capability").capability_service.conn is conn


def test_full_workspace_worker_context_tool_loop_and_verifier_growth(
    qtbot, conn, repo, task_service, date_service, tmp_path
):
    from app.ai.agent_protocol import ModelResponse, ModelToolCall
    from app.diagnostics import release_migration as rm
    from app.main import build_agent_runtime

    task = repo.create(
        title="Integrated task", description="Read-only integration",
        scheduled_date="2026-01-05", source="generated",
    )
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    model_requests = []
    factory_errors = []
    factory_db_path = _db_path(conn)
    mcp_config_path = tmp_path / "mcp-disabled.json"
    mcp_config_path.write_text('{"version":1,"servers":[]}', encoding="utf-8")
    sandbox_config_path = tmp_path / "sandbox-disabled.json"
    sandbox_config_path.write_text('{"version":1,"enabled":false}', encoding="utf-8")

    def runtime_factory(fresh_conn):
        try:
            runtime = build_agent_runtime(
                fresh_conn, db_path=factory_db_path,
                mcp_config_path=mcp_config_path,
                sandbox_config_path=sandbox_config_path,
            )
        except Exception as exc:
            factory_errors.append(repr(exc))
            raise

        class ScriptedModel:
            def __init__(self):
                self.responses = [
                    ModelResponse(content="", tool_calls=(ModelToolCall(
                        id="workspace-call", name="get_task_context", arguments="{}"
                    ),), finish_reason="tool_calls"),
                    ModelResponse(content="Task context loaded", finish_reason="stop"),
                ]

            def complete(self, request):
                model_requests.append(request)
                return self.responses.pop(0)

            def is_configured(self):
                return True

        runtime.model_client = ScriptedModel()
        return runtime

    window = _make_window(conn, task_service, date_service, sessions, runtime_factory)
    qtbot.addWidget(window)
    next(w for w in window._task_widgets if w.task().id == task.id).start_study_btn.click()
    page = window.agent_workspace_page
    session_id = page.current_session_id
    before = rm.inventory(conn)

    page.input_edit.setPlainText("What is this task?")
    page.send_button.click()
    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=7000)
    assert sessions.count_messages(session_id) == 4, (page.error_label.text(), factory_errors)
    rows = sessions.messages(session_id)
    assert [row["role"] for row in rows] == ["user", "assistant", "tool", "assistant"]
    assert [label.text() for label in page.findChildren(QLabel, "AgentMessageText")] == [
        "What is this task?", "Task context loaded",
    ]
    assert "BEGIN_TASK_CONTEXT_JSON" in model_requests[0].messages[0].content
    assert [m.role for m in model_requests[1].messages] == [
        "system", "user", "assistant", "tool",
    ]
    assert json.loads(model_requests[1].messages[-1].content)["data"]["title"] == task.title

    verified = rm.verify(conn, before=before)
    assert verified["ok"] is True, verified
    assert verified["history_new_rows"]["agent_messages"] == 4
    window.close()
