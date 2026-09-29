"""MainWindow → internal Agent Workspace → worker/runtime integration tests."""

from __future__ import annotations

import json
import threading

import pytest

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository
from app.ui.agent_message_widget import AgentMessageWidget


def _message_widgets(page):
    return page.findChildren(AgentMessageWidget)


def _message_texts(page):
    return [widget.raw_text for widget in _message_widgets(page)]


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
    assert not hasattr(window.agent_workspace_page, "task_title_label")
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
    assert window.page_header.subtitle() == "Task B"
    assert _message_widgets(window.agent_workspace_page) == []

    window.agent_workspace_page.back_button.click()
    assert window.stack.currentWidget() is window.today_page
    # Returning restores the Today header contract (no stale Workspace title).
    assert window.page_header.title() == "今日"
    assert window.page_header.subtitle() == "2026-01-05"
    assert sessions.get(session_a["id"])["status"] == "active"
    window.close()


def test_workspace_switch_clears_stale_approvals_errors_and_input(
    qtbot, conn, repo, task_service, date_service
):
    task_a = repo.create(title="A", scheduled_date="2026-01-05", source="generated")
    task_b = repo.create(title="B", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window = _make_window(conn, task_service, date_service, sessions, lambda _c: None)
    qtbot.addWidget(window)
    window._on_start_study(task_a.id)
    a = window.agent_workspace_page.current_session_id
    class ApprovalViews:
        def list_pending_views_for_session(self, sid):
            return ([{"id": 7, "session_id": a, "status": "pending",
                      "tool_name": "request_complete_current_task"}] if sid == a else [])
    window.agent_approval_service = ApprovalViews()
    window._reload_agent_session(a)
    page = window.agent_workspace_page
    assert page.approvals_layout.count() == 1
    page.set_error("old error")
    page.input_edit.setPlainText("old unsent input")
    window._on_start_study(task_b.id)
    assert window.page_header.subtitle() == "B"
    assert page.approvals_layout.count() == 0
    assert not page.error_label.text() and not page.input_edit.toPlainText()
    window._on_start_study(task_a.id)
    assert page.current_session_id == a
    assert not page.error_label.text()
    window.close()


def test_missing_session_reload_clears_previous_task_content(
    qtbot, conn, repo, task_service, date_service, monkeypatch
):
    task = repo.create(title="Old task", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window = _make_window(conn, task_service, date_service, sessions, lambda _c: None)
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    page = window.agent_workspace_page
    sid = page.current_session_id
    monkeypatch.setattr(sessions, "get", lambda _sid: (_ for _ in ()).throw(RuntimeError("db error")))
    window._reload_agent_session(sid)
    assert page.current_session_id is None
    assert page.current_session_id is None
    assert not page.approvals_container.isVisible()
    assert "无法重新加载" in page.error_label.text()
    window.close()


def test_workspace_settings_shortcut_navigates_to_existing_settings(
    qtbot, conn, repo, task_service, date_service
):
    from app.ui.app_shell import PageKey
    task = repo.create(title="Unconfigured model", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window = _make_window(conn, task_service, date_service, sessions,
                          lambda _conn: None, configured=False)
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    page = window.agent_workspace_page
    assert not page.settings_button.isHidden()
    page.settings_button.click()
    assert window.stack.currentWidget() is window.ai_settings_page
    assert window.sidebar.current_key() == PageKey.SETTINGS.value
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
    body = _message_widgets(window.agent_workspace_page)
    assert [widget.raw_text for widget in body] == ["already started"]
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
    assert _message_texts(page) == ["Keep this on failure"]
    assert not page.error_label.isHidden()
    assert "secret" not in page.error_label.text()
    assert page.interaction_status_label.isHidden()
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
    assert window.page_header.subtitle() == "B"
    release.set()

    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=5000)
    assert page.current_session_id == session_b
    assert window.page_header.subtitle() == "B"
    assert _message_widgets(page) == []
    window.close()


def test_reenter_inflight_session_keeps_busy_until_worker_finishes(
    qtbot, conn, repo, task_service, date_service
):
    task = repo.create(title="In-flight", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    entered, release = threading.Event(), threading.Event()
    window = _make_window(conn, task_service, date_service, sessions,
                          _worker_factory_with_behavior("answer", entered=entered, release=release))
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    page = window.agent_workspace_page
    page.input_edit.setPlainText("hello")
    page.send_button.click()
    assert entered.wait(3)
    sid = page.current_session_id
    window._on_agent_back()
    window._on_start_study(task.id)
    assert page.current_session_id == sid
    assert not page.send_button.isEnabled() and not page.input_edit.isEnabled()
    assert not page.interaction_status_label.isHidden()
    window._on_agent_send(sid, "must not start a second turn")
    assert len(window._agent_inflight_sessions) == 1
    release.set()
    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=5000)
    assert page.input_edit.isEnabled()
    assert [r["role"] for r in sessions.messages(sid)] == ["user", "assistant"]
    window.close()


def test_workspace_status_and_optional_config_degradation_are_nonblocking(
    qtbot, conn, repo, task_service, date_service, tmp_path, monkeypatch
):
    task = repo.create(title="Optional config", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    mcp, sandbox = tmp_path / "mcp.json", tmp_path / "sandbox.json"
    mcp.write_text('invalid SECRET_MCP_URL', encoding="utf-8")
    sandbox.write_text('invalid PRIVATE_SANDBOX_PATH', encoding="utf-8")
    monkeypatch.setenv("STUDY_AGENT_MCP_CONFIG", str(mcp))
    monkeypatch.setenv("STUDY_AGENT_SANDBOX_CONFIG", str(sandbox))
    window = _make_window(conn, task_service, date_service, sessions,
                          _worker_factory_with_behavior("native answer"))
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    page = window.agent_workspace_page
    assert "MCP 配置无效" in page.capability_warning_banner.description()
    assert "Sandbox 执行配置无效，文件工作区仍可使用" in page.capability_warning_banner.description()
    assert "SECRET_MCP_URL" not in page.capability_warning_banner.description()
    page.input_edit.setPlainText("native question")
    page.send_button.click()
    qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=5000)
    assert "native answer" in _message_texts(page)
    window.close()


def test_shutdown_waits_for_running_worker_and_clears_references(
    qtbot, conn, repo, task_service, date_service
):
    from PySide6.QtCore import QThread
    task = repo.create(title="Shutdown", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window = _make_window(conn, task_service, date_service, sessions, lambda _c: None)
    qtbot.addWidget(window)
    class CooperativeWorker(QThread):
        def run(self):
            while not self.isInterruptionRequested():
                self.msleep(5)
    worker = CooperativeWorker(window)
    window._ai_workers.append(worker)
    worker.start()
    qtbot.waitUntil(worker.isRunning, timeout=2000)
    window._stop_ai_workers()
    assert not worker.isRunning()
    assert not window._ai_workers
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
    from app.agent.trace.service import AgentTraceService

    assert isinstance(runtime.context_builder, AgentTaskContextBuilder)
    assert isinstance(runtime.memory_compactor, AgentMemoryCompactor)
    assert isinstance(runtime.trace_service, AgentTraceService)
    assert runtime.trace_service.trace_repository.conn is conn
    assert runtime.trace_service.evaluation_repository.conn is conn
    assert runtime.memory_compactor.session_service is runtime.session_service
    assert runtime.memory_compactor.memory_repository.conn is conn
    assert runtime.memory_compactor.model_client is runtime.model_client
    assert isinstance(runtime.model_client, AdaptiveAgentModelClient)
    assert isinstance(runtime.skill_selector, AgentSkillSelector)
    assert runtime.mcp_provider is None  # explicit empty config keeps Agent-4 behavior
    assert runtime.sandbox_provider is not None  # file Workspace needs no advanced config
    assert runtime.workspace_service is not None
    assert runtime.approval_provider.service.repository.conn is conn
    assert runtime.approval_provider.service.task_service.repo.conn is conn
    assert runtime.session_service.repo.conn is conn
    registry = runtime.tool_registry
    assert registry.get("get_task_context").task_service.repo.conn is conn
    assert registry.get("get_route_context").learning_route_service.route_repo.conn is conn
    assert registry.get("get_topic_context").study_plan_service.plan_repo.conn is conn
    assert registry.get("get_learning_components").topic_learning_service.conn is conn
    assert registry.get("get_mastery").assessment_service.assessment_repo.conn is conn
    assert registry.get("get_capability").capability_service.conn is conn


def test_production_invalid_optional_configs_keep_native_agent_turn(
    conn, repo, task_service, tmp_path
):
    from app.ai.agent_protocol import ModelResponse
    from app.main import build_agent_runtime

    mcp = tmp_path / "invalid-mcp.json"
    sandbox = tmp_path / "invalid-sandbox.json"
    mcp.write_text('{"private":"SECRET_ENDPOINT"}', encoding="utf-8")
    sandbox.write_text('{"private":"SECRET_DOCKER_PATH"}', encoding="utf-8")
    task = repo.create(title="Native fallback", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    runtime = build_agent_runtime(conn, db_path=_db_path(conn),
                                  mcp_config_path=mcp, sandbox_config_path=sandbox)
    assert runtime.mcp_provider is None and runtime.sandbox_provider is not None
    class FakeModel:
        def is_configured(self): return True
        def complete(self, request):
            assert any(tool["function"]["name"] == "get_task_context" for tool in request.tools)
            return ModelResponse("Native tools remain available")
    runtime.model_client = FakeModel()
    result = runtime.send_message(session["id"], "continue learning")
    assert result.assistant_message["content"] == "Native tools remain available"
    assert "SECRET_ENDPOINT" not in result.assistant_message["content"]


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
    assert _message_texts(page) == [
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


def test_reload_preserves_unsent_draft_and_switch_clears_it(
    qtbot, conn, repo, task_service, date_service
):
    task_a = repo.create(title="Draft A", scheduled_date="2026-01-05", source="generated")
    task_b = repo.create(title="Draft B", scheduled_date="2026-01-05", source="generated")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    window = _make_window(conn, task_service, date_service, sessions, lambda _c: None)
    qtbot.addWidget(window)

    window._on_start_study(task_a.id)
    page = window.agent_workspace_page
    session_a = page.current_session_id
    page.input_edit.setPlainText("unsent draft for A")

    # A status / approval-style reload of the same Session must not drop the draft.
    window._reload_agent_session(session_a)
    assert page.current_session_id == session_a
    assert page.input_edit.toPlainText() == "unsent draft for A"

    # A real Session switch clears the draft so it never leaks into B.
    window._on_start_study(task_b.id)
    assert page.input_edit.toPlainText() == ""
    window.close()
