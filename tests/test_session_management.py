"""Session metadata domain and Sidebar projection (no UI)."""

import pytest

from app.agent.session import (
    AgentSessionError, AgentSessionService, SessionClosedError, SessionNotFoundError,
)
from app.database.agent_repository import AgentRepository


@pytest.fixture
def service(conn, task_service):
    return AgentSessionService(AgentRepository(conn), task_service)


@pytest.fixture
def session(service, repo):
    task = repo.create(title="Original", scheduled_date="2026-01-01")
    return service.start_or_resume(task.id)


def test_rename_reset_and_fallback(service, session, conn):
    sid = session["id"]
    renamed = service.rename(sid, "  用户标题  ")
    assert renamed["display_title"] == "用户标题"
    assert service.effective_title(renamed) == "用户标题"
    for field in ("title", "task_id", "created_at", "updated_at", "status", "closed_at"):
        assert renamed[field] == session[field]
    reset = service.reset_title(sid)
    assert reset["display_title"] == ""
    assert service.effective_title(reset) == "Original"
    assert service.effective_title({"title": "", "display_title": "  "}) == "学习会话"
    conn.execute("UPDATE tasks SET title='Changed task' WHERE id=?", (session["task_id"],))
    conn.commit()
    assert service.start_or_resume(session["task_id"])["title"] == "Original"


@pytest.mark.parametrize("title", ["", "   ", "a" * 121, "a\x00b", "a\nb", "\ttitle", "a\x7fb", "a\u200bb", None])
def test_invalid_rename(service, session, title):
    with pytest.raises(AgentSessionError):
        service.rename(session["id"], title)
    assert service.get(session["id"]) == session


def test_title_length_boundaries(service, session):
    for title in ("字", "字" * 120):
        assert service.rename(session["id"], title)["display_title"] == title


def test_pin_archive_restore_preserve_conversation(service, session):
    sid = session["id"]
    service.append_user_message(sid, "History")
    before = service.get(sid)
    messages = service.messages(sid)
    pinned = service.pin(sid)
    assert pinned["pinned_at"]
    assert service.pin(sid) == pinned
    assert service.unpin(sid)["pinned_at"] is None
    assert service.unpin(sid)["pinned_at"] is None
    service.pin(sid)
    # A trigger observes the row at the archive update boundary: clearing the
    # pin in a later statement would fail, even inside the same transaction.
    service.repo.conn.execute(
        "CREATE TEMP TRIGGER archive_requires_atomic_unpin "
        "BEFORE UPDATE OF archived_at ON agent_sessions "
        "WHEN NEW.archived_at IS NOT NULL AND NEW.pinned_at IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT, 'archive retained pin'); END"
    )
    archived = service.archive(sid)
    assert archived["archived_at"] and archived["pinned_at"] is None
    assert archived["status"] == "active" and archived["closed_at"] is None
    assert service.archive(sid) == archived
    assert service.get(sid) == archived
    assert service.messages(sid) == messages
    assert service.list_archived_sessions() == [archived]
    with pytest.raises(AgentSessionError):
        service.pin(sid)
    restored = service.restore(sid)
    assert restored["archived_at"] is None and restored["pinned_at"] is None
    assert service.restore(sid) == restored
    assert service.list_archived_sessions() == []
    for field in ("title", "task_id", "created_at", "updated_at", "status", "closed_at"):
        assert pinned[field] == archived[field] == restored[field] == before[field]


@pytest.mark.parametrize("method,args", [
    ("rename", ("New",)), ("reset_title", ()), ("pin", ()), ("unpin", ()),
    ("archive", ()), ("restore", ()),
])
def test_metadata_requires_active_existing_session(service, session, method, args):
    with pytest.raises(SessionNotFoundError):
        getattr(service, method)(999999, *args)
    closed = service.close(session["id"])
    with pytest.raises(SessionClosedError):
        getattr(service, method)(session["id"], *args)
    assert service.get(session["id"]) == closed


def test_sidebar_order_and_independent_recent_limit(service, repo, conn):
    sessions = []
    for i in range(20):
        task = repo.create(title=str(i), scheduled_date="2026-01-01")
        sessions.append(service.start_or_resume(task.id))
    ids = [s["id"] for s in sessions]
    # Two equally pinned Sessions must use id, not conversation recency.
    for i, sid in enumerate(ids):
        conn.execute("UPDATE agent_sessions SET updated_at=? WHERE id=?",
                     (f"2026-01-{i // 2 + 1:02d}T00:00:00", sid))
    conn.execute("UPDATE agent_sessions SET pinned_at='2026-01-01' WHERE id IN (?, ?)",
                 (ids[0], ids[1]))
    conn.execute("UPDATE agent_sessions SET updated_at='2099' WHERE id=?", (ids[0],))
    conn.commit()
    service.archive(ids[17])
    service.close(ids[18])
    service.archive(ids[19])
    result = service.list_sidebar_sessions(10)
    assert [s["id"] for s in result] == [ids[1], ids[0]] + list(reversed(ids[7:17]))
    assert len({s["id"] for s in result}) == 12
    assert len(service.list_sidebar_sessions(0)) == 2
    conn.execute("UPDATE agent_sessions SET pinned_at='2026-02-01' WHERE id=?", (ids[0],))
    conn.commit()
    assert [s["id"] for s in service.list_sidebar_sessions(0)] == [ids[0], ids[1]]
    # Lifecycle APIs keep archived conversations, regardless of projection.
    active = service.list_active_sessions()
    assert ids[17] in [s["id"] for s in active]
    assert service.list_recent_active_sessions(100) == active
    conn.execute("UPDATE agent_sessions SET archived_at='2026-02-01' WHERE id IN (?, ?)",
                 (ids[17], ids[19]))
    conn.commit()
    assert [s["id"] for s in service.list_archived_sessions()] == [ids[19], ids[17]]
    assert len(service.list_archived_sessions(1)) == 1
    with pytest.raises(ValueError):
        service.list_sidebar_sessions(-1)
    with pytest.raises(ValueError):
        service.list_archived_sessions(-1)
