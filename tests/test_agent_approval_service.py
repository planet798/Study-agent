"""Explicit user decision and canonical TaskService completion semantics."""
import pytest
from app.agent.approval.service import AgentApprovalService, ApprovalRequestError
from app.database.agent_approval_repository import AgentApprovalRepository
from app.database.agent_repository import AgentRepository
from app.database.repository import TaskRepository
from app.services.task_service import TaskService
from app.ai.agent_protocol import ModelToolCall


def setup(conn, task_service=None):
    service = task_service or TaskService(TaskRepository(conn))
    task = service.create_task("task", scheduled_date="2026-09-15")
    session = AgentRepository(conn).create_session(task.id)
    call = AgentRepository(conn).add_message(session["id"], "assistant", "",
        tool_calls_json='[{"id":"c1","name":"request_complete_current_task","arguments":"{}"}]')
    approval = AgentApprovalService(AgentApprovalRepository(conn), service)
    return service, task, session, call, approval


def request(task, session, call, approval):
    return approval.request_complete_current_task(session["id"], task.id, call["id"], "c1")


def test_get_pending_for_session_enforces_ownership_status_and_known_action(conn):
    _service, task, session, call, approval = setup(conn)
    pending = request(task, session, call, approval)
    approval_id = pending["approval_id"]
    assert approval.get_pending_for_session(approval_id, session["id"])["id"] == approval_id
    assert approval.get_pending_for_session(approval_id, session["id"] + 1) is None
    assert approval.get_pending_for_session(approval_id + 1000, session["id"]) is None
    approval.reject(approval_id)
    assert approval.get_pending_for_session(approval_id, session["id"]) is None

    _service2, task2, session2, call2, approval2 = setup(conn)
    other = request(task2, session2, call2, approval2)
    conn.execute("UPDATE agent_approval_requests SET tool_name='unknown_action' WHERE id=?",
                 (other["approval_id"],))
    conn.commit()
    assert approval2.get_pending_for_session(other["approval_id"], session2["id"]) is None


def test_request_reject_and_replay(conn):
    service, task, session, call, approval = setup(conn)
    pending = request(task, session, call, approval)
    assert pending["approval_required"] and not pending["reused"]
    assert request(task, session, call, approval)["reused"]
    row = approval.reject(pending["approval_id"])
    assert row["status"] == "rejected"
    assert service.get_status(task.id) == "active"
    assert [e["event_type"] for e in approval.repository.list_events(row["id"])] == ["requested", "rejected"]
    with pytest.raises(ValueError):
        approval.approve_and_execute(row["id"])
    with pytest.raises(ValueError):
        request(task, session, call, approval)
    assert len(approval.repository.list_events(row["id"])) == 2


def test_approve_executes_once_and_reuses_existing_service_hooks(conn):
    class Outcome:
        calls = 0
        def generate_from_task(self, task):
            self.calls += 1
    class Capability:
        calls = 0
        def sync_from_task_obj(self, task):
            self.calls += 1
    outcome, capability = Outcome(), Capability()
    service = TaskService(TaskRepository(conn), outcome, capability)
    service, task, session, call, approval = setup(conn, service)
    pending = request(task, session, call, approval)
    done = approval.approve_and_execute(pending["approval_id"])
    assert service.get_status(task.id) == "done"
    assert done["status"] == "executed"
    assert (outcome.calls, capability.calls) == (1, 1)
    assert approval.approve_and_execute(done["id"])["status"] == "executed"
    assert (outcome.calls, capability.calls) == (1, 1)
    assert [e["event_type"] for e in approval.repository.list_events(done["id"])] == ["requested", "approved", "executed"]


def test_already_done_and_changed_state_and_execution_error(conn):
    service, task, session, call, approval = setup(conn)
    row = request(task, session, call, approval)
    service.complete_task(task.id)
    assert approval.approve_and_execute(row["approval_id"])["status"] == "executed"
    assert approval.repository.list_events(row["approval_id"])[-1]["code"] == "already_done"
    service2, task2, session2, call2, approval2 = setup(conn)
    row2 = request(task2, session2, call2, approval2)
    service2.mark_not_done(task2.id, "not ready")
    assert approval2.approve_and_execute(row2["approval_id"])["failure_code"] == "task_state_changed"
    service3, task3, session3, call3, approval3 = setup(conn)
    row3 = request(task3, session3, call3, approval3)
    def explode(task_id):
        raise RuntimeError("SECRET_DATABASE_ERROR")
    service3.complete_task = explode
    assert approval3.approve_and_execute(row3["approval_id"])["failure_code"] == "execution_failed"
    assert "SECRET" not in str(approval3.repository.list_events(row3["approval_id"]))


def test_production_executor_uses_canonical_today_outcome_and_capability_hooks(conn):
    from app.main import build_agent_approval_executor
    worker_service = build_agent_approval_executor(conn)
    assert worker_service.task_service.outcome_service is not None
    assert worker_service.task_service.capability_service is not None
    assert (worker_service.task_service.outcome_service.capability_service
            is worker_service.task_service.capability_service)
    assert worker_service.task_service.repo.conn is conn
    assert worker_service.repository.conn is conn


def test_request_non_active_or_missing_identity_does_not_create_approval(conn):
    service, task, session, call, approval = setup(conn)
    with pytest.raises(ValueError):
        approval.request_complete_current_task(session["id"], task.id, 0, "")
    service.complete_task(task.id)
    with pytest.raises(ApprovalRequestError):
        request(task, session, call, approval)
    assert approval.list_pending_for_session(session["id"]) == []
