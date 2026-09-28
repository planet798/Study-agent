"""Immutable approval identity, ownership, transactional events and verifier."""
import json
import sqlite3
import pytest
from app.database.agent_approval_repository import AgentApprovalRepository, ACTION
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.diagnostics.release_migration import inventory, verify


def approval_fixture(conn):
    task = TaskRepository(conn).create(title="approval task", scheduled_date="2026-09-15")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    call = agent.add_message(session["id"], "assistant", "",
        tool_calls_json=json.dumps([{"id": "call1", "name": ACTION, "arguments": "{}"}]))
    return task, session, call


def test_three_actions_bind_json_object_arguments_without_copying_note_body(conn):
    from app.database.agent_approval_repository import (
        ACTION_START_ASSESSMENT, ACTION_SAVE_NOTE, SUPPORTED_ACTIONS,
    )
    task, session, _call = approval_fixture(conn)
    agent = AgentRepository(conn)
    repository = AgentApprovalRepository(conn)
    assert SUPPORTED_ACTIONS == {ACTION, ACTION_START_ASSESSMENT, ACTION_SAVE_NOTE}
    for name, call_id, raw in ((ACTION_START_ASSESSMENT, "assess", "{}"),
                               (ACTION_SAVE_NOTE, "note", '{"title":"PRIVATE_NOTE_TITLE","content":"PRIVATE_NOTE_CONTENT"}')):
        assistant = agent.add_message(session["id"], "assistant", "",
            tool_calls_json=json.dumps([{"id":call_id,"name":name,"arguments":raw}]))
        row, reused = repository.create_request(session["id"], task.id, assistant["id"], call_id, name)
        assert not reused
        assert repository.get_bound_tool_call(row["id"]) == {
            "id":call_id, "name":name, "arguments":raw,
        }
    rows = [dict(row) for row in conn.execute("SELECT * FROM agent_approval_requests")]
    events = [dict(row) for row in conn.execute("SELECT * FROM agent_approval_events")]
    assert "PRIVATE_NOTE_CONTENT" not in str((rows, events))

    for raw in ("[1]", "bad JSON"):
        invalid = agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
            {"id":"bad", "name":ACTION_SAVE_NOTE, "arguments":raw}]))
        with pytest.raises(ValueError, match="object|JSON"):
            repository.create_request(session["id"], task.id, invalid["id"], "bad", ACTION_SAVE_NOTE)
    forged = agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
        {"id":"cross", "name":ACTION_START_ASSESSMENT, "arguments":"{}"}]))
    with pytest.raises(ValueError, match="binding"):
        repository.create_request(session["id"], task.id, forged["id"], "cross", ACTION_SAVE_NOTE)


def test_request_and_event_are_atomic_and_bound_to_persisted_call(conn):
    task, session, call = approval_fixture(conn)
    repository = AgentApprovalRepository(conn)
    before = inventory(conn)
    row, reused = repository.create_request(session["id"], task.id, call["id"], "call1")
    assert not reused and row["status"] == "pending"
    assert [event["event_type"] for event in repository.list_events(row["id"])] == ["requested"]
    assert repository.consistency_problems() == []
    assert verify(conn, before)["ok"]
    assert repository.create_request(session["id"], task.id, call["id"], "call1")[1]
    second = AgentRepository(conn).add_message(session["id"], "assistant", "",
        tool_calls_json=json.dumps([{"id": "call2", "name": ACTION, "arguments": "{}"}]))
    assert repository.create_request(session["id"], task.id, second["id"], "call2")[0]["id"] == row["id"]
    assert len(repository.list_pending_for_session(session["id"])) == 1
    duplicate = AgentRepository(conn).add_message(session["id"], "assistant", "",
        tool_calls_json=json.dumps([{"id":"duplicate","name":ACTION,"arguments":"{}"},
                                    {"id":"duplicate","name":ACTION,"arguments":"{}"}]))
    forged = AgentRepository(conn).add_message(session["id"], "assistant", "",
        tool_calls_json=json.dumps([{"id":"forged","name":ACTION,
                                     "arguments":"{\\\"task_id\\\":999}"}]))
    for bad in ((session["id"], task.id, duplicate["id"], "duplicate"),
                (session["id"], task.id, call["id"], "private tool id with spaces"),
                (session["id"], task.id, forged["id"], "forged"),
                (session["id"], task.id+1, call["id"], "call1"),
                (session["id"], task.id, call["id"], "fake"),
                (session["id"], task.id, call["id"], "call1", "unknown")):
        with pytest.raises(ValueError):
            repository.create_request(*bad)


def test_atomic_rollback_and_valid_transitions(conn):
    task, session, call = approval_fixture(conn)
    repo = AgentApprovalRepository(conn)
    conn.execute("CREATE TRIGGER break_event BEFORE INSERT ON agent_approval_events "
                 "WHEN NEW.event_type='requested' BEGIN SELECT RAISE(ABORT,'test'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_request(session["id"], task.id, call["id"], "call1")
    assert repo.list_pending_for_session(session["id"]) == []
    conn.execute("DROP TRIGGER break_event")
    row, _ = repo.create_request(session["id"], task.id, call["id"], "call1")
    conn.execute("CREATE TRIGGER break_event BEFORE INSERT ON agent_approval_events "
                 "WHEN NEW.event_type='approved' BEGIN SELECT RAISE(ABORT,'test'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.transition(row["id"], "approved")
    assert repo.get(row["id"])["status"] == "pending"
    conn.execute("DROP TRIGGER break_event")
    repo.transition(row["id"], "approved")
    repo.transition(row["id"], "executed", "completed")
    assert [r["actor"] for r in repo.list_events(row["id"])] == ["agent", "user", "system"]
    assert repo.consistency_problems() == []
    with pytest.raises(ValueError):
        repo.transition(row["id"], "approved")
    with pytest.raises(ValueError):
        repo.transition(row["id"], "pending")


@pytest.mark.parametrize("column,value", [
    ("event_type", "failed"), ("actor", "agent"), ("code", "execution_failed"),
])
def test_verifier_detects_event_tampering(conn, column, value):
    task, session, call = approval_fixture(conn)
    repo = AgentApprovalRepository(conn)
    row, _ = repo.create_request(session["id"], task.id, call["id"], "call1")
    repo.transition(row["id"], "approved")
    repo.transition(row["id"], "executed", "completed")
    before = inventory(conn)
    conn.execute(f"UPDATE agent_approval_events SET {column}=? WHERE approval_id=? AND event_type='executed'", (value, row["id"]))
    conn.commit()
    assert row["id"] not in verify(conn, before)["history_missing_ids"].get("agent_approval_requests", [])
    assert not verify(conn, before)["ok"]
    assert "agent_approval_events" in verify(conn, before)["history_modified_rows"]


def test_verifier_detects_deleted_approval_event(conn):
    task, session, call = approval_fixture(conn)
    repo = AgentApprovalRepository(conn)
    row, _ = repo.create_request(session["id"], task.id, call["id"], "call1")
    repo.transition(row["id"], "rejected")
    before = inventory(conn)
    conn.execute("DELETE FROM agent_approval_events WHERE approval_id=? AND event_type='rejected'", (row["id"],))
    conn.commit()
    result = verify(conn, before)
    assert not result["ok"]
    assert "agent_approval_events" in result["history_missing_ids"]
    assert row["id"] in result["approval_consistency_problems"]


def test_request_ownership_and_history_tamper_detection(conn):
    task, session, call = approval_fixture(conn)
    other_task, other_session, other_call = approval_fixture(conn)
    repo = AgentApprovalRepository(conn)
    for args in ((session["id"], task.id, other_call["id"], "call1"),
                 (other_session["id"], task.id, call["id"], "call1")):
        with pytest.raises(ValueError):
            repo.create_request(*args)
    row, _ = repo.create_request(session["id"], task.id, call["id"], "call1")
    repo.transition(row["id"], "rejected")
    before = inventory(conn)
    conn.execute("UPDATE agent_approval_events SET actor='system' WHERE approval_id=? AND event_type='rejected'", (row["id"],))
    conn.commit()
    assert not verify(conn, before)["ok"]
    assert repo.consistency_problems() == [row["id"]]
    conn.execute("UPDATE agent_approval_events SET actor='user' WHERE approval_id=? AND event_type='rejected'", (row["id"],))
    conn.commit()
    conn.execute("UPDATE agent_approval_requests SET tool_name='tampered' WHERE id=?", (row["id"],))
    conn.commit()
    assert row["id"] in verify(conn, before)["history_modified_rows"]["agent_approval_requests"]
