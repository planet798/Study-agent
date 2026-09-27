"""Agent Session / Message immutable-history verifier hardening (Agent-1.1)."""

from __future__ import annotations

import sqlite3

import pytest

from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.database.schema import migrate_stepwise
from app.diagnostics import release_migration as rm

EXPECTED_SESSION_FINGERPRINT = ("id", "task_id", "title", "created_at")
EXPECTED_MESSAGE_FINGERPRINT = (
    "id", "session_id", "role", "content", "tool_call_id", "tool_name",
    "tool_calls_json", "metadata_json", "created_at",
)


def _agent_history(conn, *, title="Agent study"):
    task = TaskRepository(conn).create(
        title=title, scheduled_date="2026-09-15", source="generated"
    )
    repo = AgentRepository(conn)
    session = repo.create_session(task.id, title=title)
    message = repo.add_message(
        session["id"],
        "assistant",
        "assistant response",
        tool_call_id="call-original",
        tool_name="read_topic",
        tool_calls_json='[{"id":"call-original","function":{"name":"read_topic"}}]',
        metadata_json='{"model":"test-model"}',
    )
    return task, session, message


def test_agent_tables_are_history_not_growth_and_have_v5_fingerprints():
    assert "agent_sessions" in rm.HISTORY_TABLES
    assert "agent_messages" in rm.HISTORY_TABLES
    assert "agent_sessions" not in rm.GROWTH_TABLES
    assert "agent_messages" not in rm.GROWTH_TABLES
    assert "agent_session_memory" in rm.GROWTH_TABLES
    assert "agent_session_memory" not in rm.HISTORY_TABLES
    assert "agent_session_memory" not in rm.FINGERPRINT_COLUMNS
    assert rm.FINGERPRINT_VERSION == 5
    assert rm.FINGERPRINT_COLUMNS["agent_sessions"] == EXPECTED_SESSION_FINGERPRINT
    assert rm.FINGERPRINT_COLUMNS["agent_messages"] == EXPECTED_MESSAGE_FINGERPRINT


def test_inventory_counts_agent_tables_on_v22(conn):
    _agent_history(conn)
    counts = rm.inventory(conn)["counts"]
    assert counts["agent_sessions"] == 1
    assert counts["agent_messages"] == 1
    assert counts["agent_session_memory"] == 0


def test_memory_summary_updates_are_derived_not_immutable_history(conn):
    from app.database.agent_memory_repository import AgentMemoryRepository

    _, session, message = _agent_history(conn)
    before = rm.inventory(conn)
    repository = AgentMemoryRepository(conn)
    first = repository.upsert(session["id"], message["id"], 1, "summary A")
    second = repository.upsert(session["id"], message["id"], 1, "summary B")

    result = rm.verify(conn, before=before)

    assert second["created_at"] == first["created_at"]
    assert result["ok"] is True, result
    assert "agent_session_memory" not in result["history_fingerprint_changes"]
    assert "agent_session_memory" not in result["history_modified_rows"]
    assert result["history_fingerprint_changes"] == {}


def test_v20_before_inventory_uses_none_then_v22_migration_verifies(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "v20.db"))
    try:
        migrate_stepwise(conn, target=20)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        before = rm.inventory(conn)
        assert before["counts"]["agent_sessions"] is None
        assert before["counts"]["agent_messages"] is None
        assert before["counts"]["agent_session_memory"] is None

        # Preserve representative pre-existing data while taking the v20 snapshot.
        conn.execute(
            "INSERT INTO tasks (title, description, category, estimated_minutes,"
            " priority, status, scheduled_date, postpone_count, created_at,"
            " updated_at, source) VALUES ('legacy task','','学习',1,1,'active',"
            "'2026-09-15',0,'2026-09-15T00:00:00','2026-09-15T00:00:00','manual')"
        )
        conn.commit()
        before = rm.inventory(conn)
        assert before["counts"]["agent_sessions"] is None
        assert before["counts"]["agent_messages"] is None
        assert before["counts"]["agent_session_memory"] is None

        migrate_stepwise(conn)
        after = rm.inventory(conn)
        assert after["schema_version"] == 22
        assert after["counts"]["agent_sessions"] >= 0
        assert after["counts"]["agent_messages"] >= 0
        assert after["counts"]["agent_session_memory"] == 0
        result = rm.verify(conn, before=before)
        assert result["ok"] is True, result
    finally:
        conn.close()


def test_new_session_and_messages_are_legal_history_growth(conn):
    _, _, _ = _agent_history(conn, title="Existing task")
    before = rm.inventory(conn)

    task2, session2, _ = _agent_history(conn, title="New task")
    repo = AgentRepository(conn)
    repo.add_message(session2["id"], "user", "new user message")
    repo.add_message(session2["id"], "assistant", "new assistant message")

    result = rm.verify(conn, before=before)
    assert result["ok"] is True, result
    assert result["history_new_rows"]["agent_sessions"] == 1
    assert result["history_new_rows"]["agent_messages"] == 3
    assert result["history_missing_ids"] == {}
    assert result["history_modified_rows"] == {}


def test_closing_session_does_not_change_immutable_fingerprint(conn):
    _, session, _ = _agent_history(conn)
    # Fix an older updated_at so close_session is guaranteed to change it even
    # with SQLite timestamp precision at one second.
    conn.execute(
        "UPDATE agent_sessions SET updated_at='2000-01-01T00:00:00' WHERE id=?",
        (session["id"],),
    )
    conn.commit()
    before_row = conn.execute(
        "SELECT status, updated_at, closed_at FROM agent_sessions WHERE id=?",
        (session["id"],),
    ).fetchone()
    assert tuple(before_row) == ("active", "2000-01-01T00:00:00", None)
    before = rm.inventory(conn)

    closed = AgentRepository(conn).close_session(session["id"])
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None
    assert closed["updated_at"] != "2000-01-01T00:00:00"

    result = rm.verify(conn, before=before)
    assert result["ok"] is True, result
    assert result["history_modified_rows"] == {}


def test_deleted_agent_message_is_reported_as_missing_id(conn):
    _, _, message = _agent_history(conn)
    before = rm.inventory(conn)
    conn.execute("DELETE FROM agent_messages WHERE id=?", (message["id"],))
    conn.commit()

    result = rm.verify(conn, before=before)
    assert result["ok"] is False
    assert message["id"] in result["history_missing_ids"]["agent_messages"]


def test_deleted_agent_session_is_reported_as_missing_id(conn):
    _, session, message = _agent_history(conn)
    before = rm.inventory(conn)
    # Remove dependent message first to satisfy the FK; both historical rows
    # should be reported missing, including the session itself.
    conn.execute("DELETE FROM agent_messages WHERE id=?", (message["id"],))
    conn.execute("DELETE FROM agent_sessions WHERE id=?", (session["id"],))
    conn.commit()

    result = rm.verify(conn, before=before)
    assert result["ok"] is False
    assert session["id"] in result["history_missing_ids"]["agent_sessions"]


def test_tampered_agent_message_content_is_reported_as_modified(conn):
    _, _, message = _agent_history(conn)
    before = rm.inventory(conn)
    conn.execute(
        "UPDATE agent_messages SET content='tampered' WHERE id=?",
        (message["id"],),
    )
    conn.commit()

    result = rm.verify(conn, before=before)
    assert result["ok"] is False
    assert message["id"] in result["history_modified_rows"]["agent_messages"]


def test_moving_agent_message_to_another_session_is_reported_as_modified(conn):
    _, session, message = _agent_history(conn)
    _, other_session, _ = _agent_history(conn, title="Other task")
    before = rm.inventory(conn)
    conn.execute(
        "UPDATE agent_messages SET session_id=? WHERE id=?",
        (other_session["id"], message["id"]),
    )
    conn.commit()

    result = rm.verify(conn, before=before)
    assert result["ok"] is False
    assert message["id"] in result["history_modified_rows"]["agent_messages"]
    assert session["id"] != other_session["id"]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("tool_call_id", "call-tampered"),
        ("tool_name", "tampered_tool"),
        ("tool_calls_json", '[{"tampered":true}]'),
    ],
)
def test_tampered_agent_tool_fields_are_reported_as_modified(conn, column, value):
    _, _, message = _agent_history(conn)
    before = rm.inventory(conn)
    # Column name is a test-owned constant selected by parametrization.
    conn.execute(
        f"UPDATE agent_messages SET {column}=? WHERE id=?",
        (value, message["id"]),
    )
    conn.commit()

    result = rm.verify(conn, before=before)
    assert result["ok"] is False
    assert message["id"] in result["history_modified_rows"]["agent_messages"]


def test_old_v4_snapshot_remains_compatible(conn):
    _agent_history(conn)
    before = rm.inventory(conn)
    before["fingerprint_version"] = 4
    before["counts"].pop("agent_sessions", None)
    before["counts"].pop("agent_messages", None)
    before["fingerprints"].pop("agent_sessions", None)
    before["fingerprints"].pop("agent_messages", None)

    result = rm.verify(conn, before=before)
    assert result["fingerprint_version_match"] is False
    assert result["ok"] is True, result


def test_backup_filename_is_schema_neutral(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE sample (id INTEGER)")
    conn.commit()
    conn.close()

    backup = rm.backup_database(str(path), str(tmp_path / "backups"))
    assert "_before_migration_" in backup
    assert "_before_learning_system_v20_" not in backup
