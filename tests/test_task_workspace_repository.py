"""Task Workspace persistence and database constraints."""

import sqlite3

import pytest

from app.database.task_workspace_repository import TaskWorkspaceRepository


def test_upsert_switch_clear_and_timestamp(conn, repo):
    first = repo.create("Task A")
    other = repo.create("Task B")
    workspaces = TaskWorkspaceRepository(conn)
    assert workspaces.count_by_kind() == {"managed": 0, "local": 0}
    managed = workspaces.upsert(first.id, "managed", "")
    assert managed["kind"] == "managed" and managed["local_path"] == ""
    assert managed["created_at"] and managed["updated_at"]
    local = workspaces.upsert(first.id, "local", "/tmp/my_project")
    assert local["id"] == managed["id"]
    assert local["created_at"] == managed["created_at"]
    assert local["kind"] == "local" and local["local_path"] == "/tmp/my_project"
    restored = workspaces.upsert(first.id, "managed", "")
    assert restored["id"] == managed["id"] and restored["local_path"] == ""
    workspaces.upsert(other.id, "local", "/tmp/another")
    assert workspaces.count_by_kind() == {"managed": 1, "local": 1}
    workspaces.delete_for_task(first.id)
    assert workspaces.get_by_task(first.id) is None
    assert workspaces.get_by_task(other.id) is not None


def test_constraints_and_cascade(conn, repo):
    task = repo.create("Task")
    workspaces = TaskWorkspaceRepository(conn)
    with pytest.raises(sqlite3.IntegrityError):
        workspaces.upsert(987654, "managed", "")
    with pytest.raises(sqlite3.IntegrityError):
        workspaces.upsert(task.id, "local", " ")
    with pytest.raises(sqlite3.IntegrityError):
        workspaces.upsert(task.id, "managed", "/tmp/not-allowed")
    workspaces.upsert(task.id, "local", "/tmp/project")
    conn.execute("DELETE FROM tasks WHERE id = ?", (task.id,))
    conn.commit()
    assert workspaces.get_by_task(task.id) is None
