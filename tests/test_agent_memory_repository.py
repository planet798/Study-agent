"""Persistence invariants for the rolling, Session-scoped derived memory row."""

from __future__ import annotations

import pytest

from app.agent.session import AgentSessionService
from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService


def _session(conn, title: str):
    task_service = TaskService(TaskRepository(conn))
    task = task_service.repo.create(
        title=title, scheduled_date="2026-09-15", source="generated"
    )
    service = AgentSessionService(AgentRepository(conn), task_service)
    return service, service.start_or_resume(task.id)


def test_create_read_and_session_isolation(conn):
    service, session_a = _session(conn, "Session A")
    _, session_b = _session(conn, "Session B")
    user = service.append_user_message(session_a["id"], "A question")
    repository = AgentMemoryRepository(conn)

    created = repository.upsert(
        session_a["id"], user["id"], 1, "A conversation summary"
    )

    assert repository.get_for_session(session_a["id"]) == created
    assert created["session_id"] == session_a["id"]
    assert created["through_message_id"] == user["id"]
    assert created["source_message_count"] == 1
    assert created["summary"] == "A conversation summary"
    assert created["format_version"] == 1
    assert repository.get_for_session(session_b["id"]) is None


def test_rolling_update_preserves_created_at_and_advances_updated_at(conn):
    service, session = _session(conn, "Rolling")
    first = service.append_user_message(session["id"], "first")
    repository = AgentMemoryRepository(conn)
    created = repository.upsert(session["id"], first["id"], 1, "summary A")
    second = service.append_assistant_message(session["id"], "second")

    updated = repository.upsert(
        session["id"], second["id"], 2, "summary B", format_version=1
    )

    assert updated["summary"] == "summary B"
    assert updated["through_message_id"] == second["id"]
    assert updated["source_message_count"] == 2
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] > created["updated_at"]
    assert [row["content"] for row in service.messages(session["id"])] == [
        "first", "second",
    ]


def test_upsert_validates_session_message_ownership_and_positive_counts(conn):
    service_a, session_a = _session(conn, "Owner A")
    service_b, session_b = _session(conn, "Owner B")
    message_a = service_a.append_user_message(session_a["id"], "A")
    message_b = service_b.append_user_message(session_b["id"], "B")
    repository = AgentMemoryRepository(conn)

    with pytest.raises(ValueError, match="belong"):
        repository.upsert(session_a["id"], message_b["id"], 1, "invalid owner")
    with pytest.raises(ValueError, match="source_message_count"):
        repository.upsert(session_a["id"], message_a["id"], 0, "invalid count")
    with pytest.raises(ValueError, match="summary"):
        repository.upsert(session_a["id"], message_a["id"], 1, "  ")
    with pytest.raises(ValueError, match="session does not exist"):
        repository.upsert(9999, message_a["id"], 1, "invalid session")
    with pytest.raises(ValueError, match="through_message_id"):
        repository.upsert(session_a["id"], 9999, 1, "missing message")


def test_rolling_boundary_and_source_count_cannot_move_backwards(conn):
    service, session = _session(conn, "Monotonic")
    one = service.append_user_message(session["id"], "one")
    two = service.append_assistant_message(session["id"], "two")
    repository = AgentMemoryRepository(conn)
    original = repository.upsert(session["id"], two["id"], 2, "summary")

    with pytest.raises(ValueError, match="move backwards"):
        repository.upsert(session["id"], one["id"], 2, "older")
    with pytest.raises(ValueError, match="source_message_count"):
        repository.upsert(session["id"], two["id"], 1, "bad count")

    assert repository.get_for_session(session["id"]) == original


def test_repository_detects_corrupt_cross_session_stored_boundary(conn):
    service_a, session_a = _session(conn, "Corrupt A")
    service_b, session_b = _session(conn, "Corrupt B")
    foreign = service_b.append_user_message(session_b["id"], "foreign")
    conn.execute(
        "INSERT INTO agent_session_memory "
        "(session_id, through_message_id, source_message_count, summary, "
        "format_version, created_at, updated_at) VALUES (?, ?, 1, 'bad', 1, 'x', 'x')",
        (session_a["id"], foreign["id"]),
    )
    conn.commit()

    with pytest.raises(ValueError, match="does not belong"):
        AgentMemoryRepository(conn).get_for_session(session_a["id"])


def test_session_service_messages_after_is_scoped_and_ordered(conn):
    service, session = _session(conn, "After")
    first = service.append_user_message(session["id"], "first")
    second = service.append_assistant_message(session["id"], "second")
    third = service.append_user_message(session["id"], "third")

    assert [row["id"] for row in service.messages_after(session["id"], second["id"])] == [
        third["id"],
    ]
    assert service.messages_after(session["id"], 0) == service.messages(session["id"])
    assert first["id"] < second["id"] < third["id"]
