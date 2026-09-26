"""AgentSessionService 测试（Agent-1）。

覆盖：Task-bound 创建、resume、Task 隔离、close 语义（≠ 完成 Task）、
closed 后可新建 active session、每个 Task 只允许一个 active session、
消息顺序稳定、消息不可编辑/删除。
"""

from __future__ import annotations

import pytest

from app.agent.session import (
    AgentSessionError,
    AgentSessionService,
    SessionClosedError,
    SessionNotFoundError,
)
from app.database.agent_repository import AgentRepository


@pytest.fixture()
def agent_repo(conn):
    return AgentRepository(conn)


@pytest.fixture()
def session_service(agent_repo, task_service):
    return AgentSessionService(agent_repo, task_service)


@pytest.fixture()
def task(repo):
    return repo.create(title="学习 Transformer", scheduled_date="2026-09-15",
                       source="generated")


def test_start_session_from_task(session_service, task):
    session = session_service.start_or_resume(task.id)
    assert session["task_id"] == task.id
    assert session["status"] == "active"
    assert session["title"] == "学习 Transformer"
    assert session["created_at"] and session["updated_at"]
    assert session["closed_at"] is None


def test_start_or_resume_same_task_returns_same_active_session(session_service, task):
    first = session_service.start_or_resume(task.id)
    second = session_service.start_or_resume(task.id)
    third = session_service.start_or_resume(task.id)
    assert first["id"] == second["id"] == third["id"]
    assert len(session_service.list_sessions_for_task(task.id)) == 1


def test_second_task_gets_isolated_session(session_service, repo, task):
    other = repo.create(title="学习 KV Cache", scheduled_date="2026-09-15",
                        source="generated")
    a = session_service.start_or_resume(task.id)
    b = session_service.start_or_resume(other.id)
    assert a["id"] != b["id"]
    assert a["task_id"] != b["task_id"]

    session_service.append_user_message(a["id"], "A 的消息")
    session_service.append_user_message(b["id"], "B 的消息")
    a_msgs = [m["content"] for m in session_service.messages(a["id"])]
    b_msgs = [m["content"] for m in session_service.messages(b["id"])]
    assert a_msgs == ["A 的消息"]
    assert b_msgs == ["B 的消息"]


def test_start_unknown_task_raises(session_service):
    from app.services.task_service import TaskNotFoundError
    with pytest.raises(TaskNotFoundError):
        session_service.start_or_resume(999999)


def test_close_session_does_not_complete_task(session_service, task_service, task):
    session = session_service.start_or_resume(task.id)
    closed = session_service.close(session["id"])
    assert closed["status"] == "closed"
    assert closed["closed_at"]
    # Task 状态 / Mastery / Capability 完全不受影响
    assert task_service.get_task(task.id).status == "active"


def test_closed_session_allows_new_active_session(session_service, task):
    first = session_service.start_or_resume(task.id)
    session_service.close(first["id"])
    second = session_service.start_or_resume(task.id)
    assert second["id"] != first["id"]
    assert second["status"] == "active"
    sessions = session_service.list_sessions_for_task(task.id)
    assert [s["status"] for s in sessions] == ["closed", "active"]


def test_only_one_active_session_per_task_enforced_at_db_level(
    session_service, agent_repo, task
):
    import sqlite3
    session_service.start_or_resume(task.id)
    with pytest.raises(sqlite3.IntegrityError):
        agent_repo.create_session(task.id, title="dup")


def test_closed_session_cannot_send_messages(session_service, task):
    session = session_service.start_or_resume(task.id)
    session_service.close(session["id"])
    with pytest.raises(SessionClosedError):
        session_service.append_user_message(session["id"], "还能聊吗")
    with pytest.raises(SessionClosedError):
        session_service.append_assistant_message(session["id"], "不能")
    assert session_service.messages(session["id"]) == []


def test_blank_messages_are_rejected_and_not_persisted(session_service, task):
    session = session_service.start_or_resume(task.id)
    for blank in ("", "   ", "\n\t "):
        with pytest.raises(AgentSessionError):
            session_service.append_user_message(session["id"], blank)
    assert session_service.messages(session["id"]) == []


def test_message_order_is_stable(session_service, task):
    session = session_service.start_or_resume(task.id)
    session_service.append_user_message(session["id"], "u1")
    session_service.append_assistant_message(session["id"], "a1")
    session_service.append_user_message(session["id"], "u2")
    session_service.append_assistant_message(session["id"], "a2")
    rows = session_service.messages(session["id"])
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2"),
    ]
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids)
    assert session_service.count_messages(session["id"]) == 4


def test_messages_are_immutable(session_service, agent_repo, task):
    session = session_service.start_or_resume(task.id)
    session_service.append_user_message(session["id"], "原始内容")
    assert not hasattr(agent_repo, "update_message")
    assert not hasattr(agent_repo, "delete_message")
    assert not hasattr(session_service, "edit_message")
    assert not hasattr(session_service, "delete_message")


def test_missing_session_raises(session_service):
    with pytest.raises(SessionNotFoundError):
        session_service.get(424242)
    with pytest.raises(SessionNotFoundError):
        session_service.messages(424242)
