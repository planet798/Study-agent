"""Transactional Trace repository validation and ownership tests."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.database.agent_repository import AgentRepository
from app.database.agent_trace_repository import AgentTraceRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService
from app.agent.session import AgentSessionService
from app.utils.date_utils import now_iso


def _environment(conn, title="Trace task"):
    task = TaskRepository(conn).create(
        title=title, scheduled_date="2026-10-01", source="manual"
    )
    sessions = AgentSessionService(AgentRepository(conn), TaskService(TaskRepository(conn)))
    session = sessions.start_or_resume(task.id)
    user = sessions.append_user_message(session["id"], "private user content")
    assistant = sessions.append_assistant_message(session["id"], "private assistant content")
    return task, session, user, assistant


def _trace(task, session, user, assistant=None, **overrides):
    trace = {
        "session_id": session["id"], "task_id": task.id,
        "user_message_id": user["id"],
        "assistant_message_id": assistant["id"] if assistant else None,
        "status": "succeeded" if assistant else "failed", "skill_key": "teach-concept",
        "memory_compacted": 0, "memory_omitted_earlier": 0,
        "memory_through_message_id": None, "tool_rounds": 0,
        "model_call_count": 1, "memory_model_call_count": 0,
        "tool_call_count": 0, "tool_error_count": 0,
        "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
        "usage_complete": 1, "error_code": "" if assistant else "ai_service_error",
        "started_at": "2026-10-01T12:00:00", "finished_at": "2026-10-01T12:00:01",
        "duration_ms": 1000,
    }
    trace.update(overrides)
    return trace


def _event(seq=1, **overrides):
    event = {
        "seq": seq, "kind": "runtime", "name": "task_context", "status": "ok",
        "duration_ms": 1, "details_json": json.dumps({"available": True}),
        "created_at": now_iso(),
    }
    event.update(overrides)
    return event


def test_record_success_and_failed_traces_with_ordered_events(conn):
    task, session, user, assistant = _environment(conn)
    repository = AgentTraceRepository(conn)

    success = repository.record_turn(
        _trace(task, session, user, assistant),
        (_event(1), _event(2, name="skill_selection",
                           details_json='{"skill_key":"teach-concept"}')),
    )
    failed_user = AgentRepository(conn).add_message(session["id"], "user", "failed turn")
    failed = repository.record_turn(
        _trace(task, session, failed_user), (),
    )

    assert success["status"] == "succeeded"
    assert success["assistant_message_id"] == assistant["id"]
    assert failed["status"] == "failed" and failed["assistant_message_id"] is None
    assert [event["seq"] for event in repository.list_events(success["id"])] == [1, 2]
    assert [row["id"] for row in repository.list_for_session(session["id"], limit=1)] == [
        failed["id"]
    ]
    assert repository.list_events(failed["id"]) == []


def test_trace_and_all_events_rollback_as_one_transaction(conn):
    task, session, user, assistant = _environment(conn)
    conn.execute(
        "CREATE TRIGGER fail_second_trace_event BEFORE INSERT ON agent_trace_events "
        "WHEN NEW.seq = 2 BEGIN SELECT RAISE(ABORT, 'injected event failure'); END"
    )
    conn.commit()
    repository = AgentTraceRepository(conn)

    with pytest.raises(sqlite3.IntegrityError):
        repository.record_turn(
            _trace(task, session, user, assistant), (_event(1), _event(2)),
        )

    assert conn.execute("SELECT COUNT(*) FROM agent_turn_traces").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_trace_events").fetchone()[0] == 0


def test_seq_must_be_contiguous_unique_and_start_at_one(conn):
    task, session, user, assistant = _environment(conn)
    repository = AgentTraceRepository(conn)
    for events in ((_event(0),), (_event(1), _event(1)), (_event(2),)):
        with pytest.raises(ValueError, match="seq"):
            repository.record_turn(_trace(task, session, user, assistant), events)
    assert conn.execute("SELECT COUNT(*) FROM agent_turn_traces").fetchone()[0] == 0


def test_repository_rejects_wrong_session_task_and_message_ownership(conn):
    task_a, session_a, user_a, assistant_a = _environment(conn, "A")
    task_b, session_b, user_b, assistant_b = _environment(conn, "B")
    repository = AgentTraceRepository(conn)

    with pytest.raises(ValueError, match="session does not exist"):
        repository.record_turn(
            _trace(task_a, session_a, user_a, assistant_a, session_id=99999), ()
        )
    with pytest.raises(ValueError, match="task_id"):
        repository.record_turn(
            _trace(task_b, session_a, user_a, assistant_a), ()
        )
    with pytest.raises(ValueError, match="user_message_id"):
        repository.record_turn(
            _trace(task_a, session_a, user_b, assistant_a), ()
        )
    with pytest.raises(ValueError, match="assistant_message_id"):
        repository.record_turn(
            _trace(task_a, session_a, user_a, assistant_b), ()
        )
    assistant_role = AgentRepository(conn).add_message(session_a["id"], "tool", "{}")
    with pytest.raises(ValueError, match="assistant_message_id"):
        repository.record_turn(
            _trace(task_a, session_a, user_a, assistant_role), ()
        )
    tool_role = AgentRepository(conn).add_message(session_a["id"], "assistant", "not user")
    with pytest.raises(ValueError, match="user_message_id"):
        repository.record_turn(
            _trace(task_a, session_a, tool_role, assistant_a), ()
        )


def test_event_details_are_bounded_and_allowlisted(conn):
    task, session, user, assistant = _environment(conn)
    repository = AgentTraceRepository(conn)
    bad_events = (
        _event(details_json='{"prompt":"PRIVATE"}'),
        _event(details_json=json.dumps({"available": True, "user_text": "SECRET"})),
        _event(1, name="skill_selection",
               details_json=json.dumps({"skill_key": "x" * 5000})),
    )
    for event in bad_events:
        with pytest.raises(ValueError):
            repository.record_turn(_trace(task, session, user, assistant), (event,))
    assert conn.execute("SELECT COUNT(*) FROM agent_turn_traces").fetchone()[0] == 0


@pytest.mark.migration  # release verifier derived-growth contract
def test_trace_and_evaluation_growth_is_derived_not_historical(conn):
    from app.database.agent_evaluation_repository import AgentEvaluationRepository
    from app.diagnostics.release_migration import inventory, verify

    task, session, user, assistant = _environment(conn)
    before = inventory(conn)
    traces = AgentTraceRepository(conn)
    trace = traces.record_turn(
        _trace(task, session, user, assistant), (_event(1),)
    )
    evaluation = AgentEvaluationRepository(conn).upsert(
        trace["id"], 1, "pass", {"turn_succeeded": True}, {"duration_ms": 1}
    )

    after = inventory(conn)
    result = verify(conn, before=before)

    assert evaluation["status"] == "pass"
    assert after["counts"]["agent_turn_traces"] == 1
    assert after["counts"]["agent_trace_events"] == 1
    assert after["counts"]["agent_turn_evaluations"] == 1
    assert result["ok"] is True, result
    assert result["history_fingerprint_changes"] == {}
    assert all(table not in result["history_fingerprint_changes"] for table in (
        "agent_turn_traces", "agent_trace_events", "agent_turn_evaluations",
    ))


def test_evaluation_repository_upserts_by_trace_and_version(conn):
    from app.database.agent_evaluation_repository import AgentEvaluationRepository

    task, session, user, assistant = _environment(conn)
    trace = AgentTraceRepository(conn).record_turn(
        _trace(task, session, user, assistant), ()
    )
    repository = AgentEvaluationRepository(conn)
    assert repository.get_for_trace(trace["id"], 1) is None
    first = repository.upsert(
        trace["id"], 1, "pass", {"turn_succeeded": True}, {"duration_ms": 5}
    )
    replaced = repository.upsert(
        trace["id"], 1, "warn", {"turn_succeeded": True}, {"usage": False}
    )
    other_version = repository.upsert(
        trace["id"], 2, "fail", {"turn_succeeded": False}, {"duration_ms": 5}
    )

    assert first["status"] == "pass"
    assert replaced["id"] == first["id"] and replaced["status"] == "warn"
    assert len(repository.list_for_trace(trace["id"])) == 2
    assert other_version["evaluator_version"] == 2
    with pytest.raises(ValueError, match="does not exist"):
        repository.upsert(99999, 1, "pass", {}, {})
    with pytest.raises(ValueError, match="status"):
        repository.upsert(trace["id"], 3, "score", {}, {})


def test_trace_indexes_and_schema_are_present(conn):
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    assert {"idx_agent_turn_traces_session", "idx_agent_turn_traces_task"} <= indexes
    expected = {
        "agent_turn_traces": {
            "id", "session_id", "task_id", "user_message_id", "assistant_message_id",
            "status", "skill_key", "memory_compacted", "memory_omitted_earlier",
            "memory_through_message_id", "tool_rounds", "model_call_count",
            "memory_model_call_count", "tool_call_count", "tool_error_count",
            "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete",
            "error_code", "started_at", "finished_at", "duration_ms",
        },
        "agent_trace_events": {
            "id", "trace_id", "seq", "kind", "name", "status", "duration_ms",
            "details_json", "created_at",
        },
        "agent_turn_evaluations": {
            "id", "trace_id", "evaluator_version", "status", "checks_json",
            "metrics_json", "created_at",
        },
    }
    for table, columns in expected.items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert actual == columns
