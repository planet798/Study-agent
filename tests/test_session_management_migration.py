"""Populated real v25 -> v26 migration and immutable-history contracts."""

import pytest

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository
from app.database.connection import get_raw_connection
from app.database.repository import TaskRepository
from app.database.schema import SCHEMA_VERSION, migrate_stepwise
from app.diagnostics import release_migration as rm

pytestmark = pytest.mark.migration


@pytest.fixture
def migrated(tmp_path):
    conn = get_raw_connection(tmp_path / "v25.db")
    try:
        assert migrate_stepwise(conn, target=25) == 25
        repo = AgentRepository(conn)
        tasks = TaskRepository(conn)
        task = tasks.create(title="Original task", scheduled_date="2026-01-01")
        other = tasks.create(title="Other task", scheduled_date="2026-01-01")
        session = repo.create_session(task.id, "Immutable snapshot")
        repo.add_message(session["id"], "user", "Original question 字")
        repo.add_message(session["id"], "assistant", "Original answer")
        from app.database.task_workspace_repository import TaskWorkspaceRepository
        TaskWorkspaceRepository(conn).upsert(task.id, "managed")
        before = rm.inventory(conn)
        original_messages = repo.list_messages(session["id"])
        assert "display_title" not in session
        steps = []
        assert migrate_stepwise(conn, on_step=steps.append) == SCHEMA_VERSION == 27
        assert steps == [26, 27]
        row = repo.get_session(session["id"])
        assert {key: row[key] for key in session} == session
        assert row["display_title"] == ""
        assert row["pinned_at"] is None and row["archived_at"] is None
        assert repo.list_messages(session["id"]) == original_messages
        assert rm.verify(conn, before)["ok"]
        assert migrate_stepwise(conn) == 27
        yield conn, AgentSessionService(repo, None), session, other, before
    finally:
        conn.close()


def test_v25_migration_and_each_metadata_operation_verifies(migrated):
    conn, service, session, _, before = migrated
    from app.agent.eval.evaluator import EVALUATOR_VERSION

    assert EVALUATOR_VERSION == 2
    assert rm.FINGERPRINT_VERSION == 7
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(agent_sessions)")}
    assert columns["display_title"][2:5] == ("TEXT", 1, "''")
    for name in ("pinned_at", "archived_at"):
        assert columns[name][2:5] == ("TEXT", 0, None)
    assert rm.FINGERPRINT_COLUMNS["agent_sessions"] == ("id", "task_id", "title", "created_at")
    for method, args in [
        ("rename", ("Visible override",)), ("reset_title", ()),
        ("pin", ()), ("unpin", ()), ("pin", ()),
        ("archive", ()), ("restore", ()),
    ]:
        row = getattr(service, method)(session["id"], *args)
        assert row["title"] == session["title"]
        assert row["updated_at"] == session["updated_at"]
        result = rm.verify(conn, before)
        assert result["ok"], result
        assert result["history_modified_rows"] == {}


@pytest.mark.parametrize("mutation", ["title", "task_id", "delete_message", "delete_session"])
def test_v25_immutable_history_still_protected(migrated, mutation):
    conn, service, session, other, before = migrated
    sid = session["id"]
    if mutation == "title":
        conn.execute("UPDATE agent_sessions SET title='tampered' WHERE id=?", (sid,))
    elif mutation == "task_id":
        conn.execute("UPDATE agent_sessions SET task_id=? WHERE id=?", (other.id, sid))
    else:
        conn.execute("DELETE FROM agent_messages WHERE session_id=?", (sid,))
        if mutation == "delete_session":
            conn.execute("DELETE FROM agent_sessions WHERE id=?", (sid,))
    conn.commit()
    result = rm.verify(conn, before)
    assert not result["ok"]
    if mutation in {"title", "task_id"}:
        assert sid in result["history_modified_rows"]["agent_sessions"]
    else:
        assert result["history_missing_ids"]["agent_messages"]
        if mutation == "delete_session":
            assert sid in result["history_missing_ids"]["agent_sessions"]
