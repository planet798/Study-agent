"""Real v24 -> v25 migration and release verifier Workspace history."""

import copy

from app.database.connection import get_raw_connection, get_fresh_connection
from app.database.schema import SCHEMA_VERSION, get_schema_version, migrate_stepwise
from app.database.task_workspace_repository import TaskWorkspaceRepository
from app.diagnostics.release_migration import FINGERPRINT_VERSION, inventory, verify


def test_v24_to_v25_preserves_agent_and_learning_history(tmp_path):
    conn = get_raw_connection(tmp_path / "v24.db")
    assert migrate_stepwise(conn, target=24) == 24
    ts = "2026-01-01T12:00:00"
    task_id = conn.execute(
        "INSERT INTO tasks(title, scheduled_date, created_at, updated_at) "
        "VALUES ('SFT', '2026-01-01', ?, ?)", (ts, ts)
    ).lastrowid
    kp_id = conn.execute(
        "INSERT INTO knowledge_points(name, created_at, updated_at) VALUES ('SFT', ?, ?)",
        (ts, ts)
    ).lastrowid
    conn.execute(
        "INSERT INTO assessment_attempts(knowledge_point_id, task_id, questions_json, "
        "created_at) VALUES (?, ?, '{}', ?)", (kp_id, task_id, ts)
    )
    conn.execute(
        "INSERT INTO learning_outcomes(kind, title, content, created_at) "
        "VALUES ('note', 'SFT notes', 'example', ?)", (ts,)
    )
    session_id = conn.execute(
        "INSERT INTO agent_sessions(task_id, title, created_at, updated_at) "
        "VALUES (?, 'SFT', ?, ?)", (task_id, ts, ts)
    ).lastrowid
    user_id = conn.execute(
        "INSERT INTO agent_messages(session_id, role, content, created_at) "
        "VALUES (?, 'user', 'Question', ?)", (session_id, ts)
    ).lastrowid
    assistant_id = conn.execute(
        "INSERT INTO agent_messages(session_id, role, content, created_at) "
        "VALUES (?, 'assistant', 'Answer', ?)", (session_id, ts)
    ).lastrowid
    trace_id = conn.execute(
        "INSERT INTO agent_turn_traces(session_id, task_id, user_message_id, "
        "assistant_message_id, status, started_at, finished_at) "
        "VALUES (?, ?, ?, ?, 'succeeded', ?, ?)",
        (session_id, task_id, user_id, assistant_id, ts, ts)
    ).lastrowid
    conn.execute(
        "INSERT INTO agent_trace_events(trace_id, seq, kind, status, created_at) "
        "VALUES (?, 1, 'runtime', 'ok', ?)", (trace_id, ts)
    )
    conn.execute(
        "INSERT INTO agent_turn_evaluations(trace_id, evaluator_version, status, created_at) "
        "VALUES (?, 2, 'pass', ?)", (trace_id, ts)
    )
    approval_id = conn.execute(
        "INSERT INTO agent_approval_requests(session_id, task_id, assistant_message_id, "
        "tool_call_id, tool_name, requested_at) "
        "VALUES (?, ?, ?, 'call-1', 'request_save_learning_note', ?)",
        (session_id, task_id, assistant_id, ts)
    ).lastrowid
    conn.execute(
        "INSERT INTO agent_approval_events(approval_id, event_type, actor, created_at) "
        "VALUES (?, 'requested', 'agent', ?)", (approval_id, ts)
    )
    conn.commit()
    before = inventory(conn)
    assert before["counts"]["task_workspaces"] is None
    steps = []
    assert migrate_stepwise(conn, on_step=steps.append) == 25
    assert steps == [25] and get_schema_version(conn) == SCHEMA_VERSION
    assert inventory(conn)["counts"]["task_workspaces"] == 0
    assert verify(conn, before)["ok"]
    for table in ("tasks", "assessment_attempts", "learning_outcomes", "agent_sessions",
                  "agent_messages", "agent_turn_traces", "agent_trace_events",
                  "agent_turn_evaluations", "agent_approval_requests", "agent_approval_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1
    conn.close()


def test_binding_fingerprint_detects_immutable_changes_and_redacts_path(tmp_path):
    conn = get_fresh_connection(tmp_path / "fresh.db")
    assert get_schema_version(conn) == 25
    assert FINGERPRINT_VERSION == 7
    ids = [conn.execute(
        "INSERT INTO tasks(title, scheduled_date, created_at, updated_at) "
        "VALUES (?, '2026-01-01', 't', 't')", (title,)
    ).lastrowid for title in ("A", "B", "C")]
    repo = TaskWorkspaceRepository(conn)
    repo.upsert(ids[0], "managed")
    secret = "/private/home/SECRET_PROJECT"
    repo.upsert(ids[1], "local", secret)
    before = inventory(conn)
    assert before["counts"]["task_workspaces"] == 2
    assert secret not in str(before)
    assert verify(conn, copy.deepcopy(before))["ok"]
    original = repo.get_by_task(ids[1])
    changes = (
        ("task_id = ?", (ids[2],)),
        ("kind = 'managed', local_path = ''", ()),
        ("local_path = ?", ("/private/home/another",)),
        ("created_at = ?", ("yesterday",)),
    )
    for assignment, values in changes:
        conn.execute(f"UPDATE task_workspaces SET {assignment} WHERE id = ?",
                     (*values, original["id"]))
        conn.commit()
        result = verify(conn, before)
        assert not result["ok"] and result["history_modified_rows"]["task_workspaces"]
        assert secret not in str(result)
        conn.execute(
            "UPDATE task_workspaces SET task_id = ?, kind = ?, "
            "local_path = ?, created_at = ? WHERE id = ?",
            (original["task_id"], original["kind"], original["local_path"],
             original["created_at"], original["id"]),
        )
        conn.commit()
    assert verify(conn, before)["ok"]
    conn.execute("UPDATE task_workspaces SET updated_at = 'new' WHERE id = ?",
                 (original["id"],))
    conn.commit()
    assert verify(conn, before)["ok"]
    conn.close()
