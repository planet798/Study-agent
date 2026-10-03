"""Task state is not the lifecycle or navigation key of an Agent Session."""
from __future__ import annotations

import pytest

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository
from app.services.past_task_service import PastTaskConfirmationService
from app.ui.main_window import MainWindow


def _window(qtbot, task_service, date_service, sessions, workspace=None):
    window = MainWindow(task_service, date_service, today_provider=lambda: "2026-01-06",
                        agent_session_service=sessions, agent_runtime_factory=lambda conn: None,
                        task_workspace_service=workspace)
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize("decision", ["not_done", "done", "cancelled"])
def test_origin_task_status_never_hides_history(
    qtbot, conn, repo, task_service, date_service, decision,
):
    task = repo.create(title="SFT history", scheduled_date="2026-01-05", source="manual",
                       task_type="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    sid = session["id"]
    sessions.append_user_message(sid, "Original question")
    sessions.append_assistant_message(sid, "Original answer")
    if decision == "cancelled":
        task_service.cancel_task(task.id)
    else:
        past = PastTaskConfirmationService(repo, task_service)
        assert task.id in [t.id for t in past.find_unresolved("2026-01-06")]
        past.apply_decisions({task.id: decision})
    assert task_service.get_task(task.id).status == decision
    assert sessions.get(sid)["status"] == "active"
    assert sessions.get(sid)["task_id"] == task.id
    assert [s["id"] for s in sessions.list_recent_active_sessions()] == [sid]
    window = _window(qtbot, task_service, date_service, sessions)
    assert window.sidebar.session_item(sid) is not None
    window.sidebar.session_item(sid).click()
    assert window.agent_workspace_page.current_session_id == sid
    assert window.sidebar.session_item(sid).isChecked()
    assert [m["content"] for m in sessions.messages(sid)] == ["Original question", "Original answer"]
    from app.ui.agent_message_widget import AgentMessageWidget
    bubbles = window.agent_workspace_page.conversation_body.findChildren(AgentMessageWidget)
    assert [b.raw_text for b in bubbles] == ["Original question", "Original answer"]
    sessions.append_user_message(sid, "Day 2 follow-up")
    window._reload_agent_session(sid)
    assert sessions.messages(sid)[-1]["content"] == "Day 2 follow-up"
    window.close()


def test_recent_order_selected_busy_and_bounded(qtbot, conn, repo, task_service, date_service):
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    ids = []
    for n in range(13):
        task = repo.create(title=("Long session title " * 15) + str(n),
                           scheduled_date="2026-01-05", source="manual")
        ids.append(sessions.start_or_resume(task.id)["id"])
    window = _window(qtbot, task_service, date_service, sessions)
    assert len(window.sidebar._session_items) == 10
    first = ids[-1]
    window._on_open_agent_session(first)
    assert window.sidebar.session_item(first).isChecked()
    assert len(window.sidebar.session_item(first).text()) < len(sessions.get(first)["title"])
    sessions.append_user_message(ids[0], "Latest")
    window._refresh_learning_sessions()
    assert sessions.list_recent_active_sessions(1)[0]["id"] == ids[0]
    assert next(iter(window.sidebar._session_items)) == ids[0]
    window._agent_inflight_sessions.add(first)
    window._refresh_learning_sessions()
    assert not window.sidebar.session_item(ids[0]).isEnabled()
    window._on_open_agent_session(ids[0])
    assert window.agent_workspace_page.current_session_id == first
    window._agent_inflight_sessions.clear()
    window._refresh_learning_sessions()
    assert window.sidebar.session_item(ids[0]).isEnabled()
    window._on_open_agent_session(ids[0])
    assert window.agent_workspace_page.current_session_id == ids[0]
    window.close()


@pytest.mark.parametrize("width", [900, 1100, 1400])
def test_narrow_workspace_control_never_claims_long_path(qtbot, repo, width):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from app.ui.agent_workspace_page import AgentWorkspacePage
    task = repo.create(title="DPI test", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.resize(width - 228, 620)
    font = page.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    page.setFont(font)
    path = "D:\\Projects\\" + "very-long-folder\\" * 50
    view = SimpleNamespace(task_id=task.id, kind="local", available=True,
                           label="Long project name " * 30, display_path=path)
    page.load_session({"id": 1}, [], task, None, True, workspace_view=view)
    page.show()
    QApplication.processEvents()
    control = page.workspace_card
    assert control.selector.toolTip() == path
    assert control.selector.width() < page.width() // 2
    assert control.badge.geometry().left() >= control.selector.geometry().right()
    assert page.conversation_scroll.height() > page.height() // 2
    page.close()


def test_conversation_header_sizing_does_not_leak_to_other_pages(
    qtbot, conn, repo, task_service, date_service,
):
    from app.ui.app_shell import PageKey
    task = repo.create(title="Conversation sizing", scheduled_date="2026-01-06", source="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    sid = sessions.start_or_resume(task.id)["id"]
    window = _window(qtbot, task_service, date_service, sessions)
    window._on_open_agent_session(sid)
    assert window.page_header._row.stretch(1) == 1
    window.sidebar.item(PageKey.TODAY).click()
    assert window.page_header._row.stretch(1) == 0
    assert window.page_header.subtitle() == "2026-01-06"
    window.sidebar.item(PageKey.SETTINGS).click()
    assert window.page_header._row.stretch(1) == 0
    window._on_open_agent_session(sid)
    assert window.page_header._row.stretch(1) == 1
    window.close()
