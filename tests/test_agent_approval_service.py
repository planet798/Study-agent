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


def test_note_request_preview_conflict_and_canonical_outcome(conn):
    import json
    from app.database.skill_repository import LearningOutcomeRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.learning_outcome_service import LearningOutcomeService
    kp = AssessmentRepository(conn).create_knowledge_point("Note KP")
    tasks = TaskService(TaskRepository(conn))
    task = TaskRepository(conn).create("Note task", scheduled_date="2026-09-15",
                                      knowledge_point_id=kp["id"])
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    args = {"title": "  Private title  ", "content": "  Private content  "}
    def call(call_id, payload):
        return agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
            {"id": call_id, "name": "request_save_learning_note",
             "arguments": json.dumps(payload)}]))
    first = call("n1", args)
    outcomes = LearningOutcomeService(LearningOutcomeRepository(conn))
    service = AgentApprovalService(AgentApprovalRepository(conn), tasks, outcome_service=outcomes)
    before = conn.execute("SELECT COUNT(*) FROM learning_outcomes").fetchone()[0]
    with pytest.raises(ValueError, match="persisted call"):
        service.request_save_learning_note(session["id"], task.id, first["id"], "n1",
            {"title": "different", "content": "different"})
    assert not service.list_pending_for_session(session["id"])
    pending = service.request_save_learning_note(session["id"], task.id, first["id"], "n1", args)
    assert conn.execute("SELECT COUNT(*) FROM learning_outcomes").fetchone()[0] == before
    view = service.list_pending_views_for_session(session["id"])[0]
    assert view["note_title"] == "Private title" and view["note_preview"] == "Private content"
    same = call("n2", {"content": "Private content", "title": "Private title"})
    assert service.request_save_learning_note(session["id"], task.id, same["id"], "n2",
        {"content": "Private content", "title": "Private title"})["reused"]
    different = call("n3", {"title": "other", "content": "different"})
    from app.agent.approval.service import PendingActionError, InvalidNoteError
    with pytest.raises(PendingActionError):
        service.request_save_learning_note(session["id"], task.id, different["id"], "n3",
                                            {"title": "other", "content": "different"})
    with pytest.raises(InvalidNoteError):
        service.request_save_learning_note(session["id"], task.id, different["id"], "n3",
                                            {"title": "", "content": "x"})
    assert len(service.list_pending_for_session(session["id"])) == 1
    done = service.approve_and_execute(pending["approval_id"])
    assert done["action"] == "save_learning_note"
    note = LearningOutcomeRepository(conn).get(done["result"]["outcome_id"])
    assert (note["kind"], note["title"], note["content"]) == (
        "note", "Private title", "Private content")
    assert note["task_id"] is None and note["linked_kp_id"] == kp["id"]
    assert note["git_commit"] == ""
    assert tasks.get_status(task.id) == "active"
    assert conn.execute("SELECT COUNT(*) FROM capability_evidence").fetchone()[0] == 0
    tasks.outcome_service = outcomes
    tasks.complete_task(task.id)
    assert LearningOutcomeRepository(conn).get(note["id"])["content"] == "Private content"
    assert LearningOutcomeRepository(conn).get_by_task_id(task.id)["kind"] == "topic"


def test_assessment_request_resumes_pending_and_failure_preserves_mastery(conn):
    import json
    from app.database.assessment_repository import AssessmentRepository
    from app.services.assessment_service import AssessmentService
    from tests.test_assessment_flow import FakeClient, QUESTIONS_CONTENT
    assessment_repo = AssessmentRepository(conn)
    kp = assessment_repo.create_knowledge_point("Assessment KP")
    task = TaskRepository(conn).create("Assess", scheduled_date="2026-09-15",
                                      knowledge_point_id=kp["id"])
    tasks = TaskService(TaskRepository(conn))
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    def call(call_id):
        return agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
            {"id": call_id, "name": "request_start_assessment", "arguments": "{}"}]))
    client = FakeClient(responses=[QUESTIONS_CONTENT])
    svc = AgentApprovalService(AgentApprovalRepository(conn), tasks,
        assessment_service=AssessmentService(client, assessment_repo=assessment_repo))
    first = call("a1")
    pending = svc.request_start_assessment(session["id"], task.id, first["id"], "a1")
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    assert not client.calls
    started = svc.approve_and_execute(pending["approval_id"])
    attempt = started["result"]["attempt"]
    assert started["action"] == "start_assessment" and attempt["judge_status"] == "pending"
    assert len(client.calls) == 1
    assert assessment_repo.get_knowledge_point(kp["id"])["mastery_estimate"] == 0
    second = call("a2")
    resumed_pending = svc.request_start_assessment(session["id"], task.id, second["id"], "a2")
    resumed = svc.approve_and_execute(resumed_pending["approval_id"])
    assert resumed["result"]["attempt"]["id"] == attempt["id"]
    assert len(client.calls) == 1
    assert svc.approve_and_execute(resumed_pending["approval_id"])["status"] == "executed"
    assert len(client.calls) == 1


def test_assessment_unavailable_reject_and_generation_failure(conn):
    from app.agent.approval.service import AssessmentUnavailableError
    import json
    from app.database.assessment_repository import AssessmentRepository
    from app.services.assessment_service import AssessmentService
    from tests.test_assessment_flow import FakeClient
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("Activity", scheduled_date="2026-09-15")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    message = agent.add_message(session["id"], "assistant", "",
        tool_calls_json='[{"id":"a1","name":"request_start_assessment","arguments":"{}"}]')
    client = FakeClient(error=RuntimeError("private AI failure"))
    service = AgentApprovalService(AgentApprovalRepository(conn), task_service,
        assessment_service=AssessmentService(client, assessment_repo=AssessmentRepository(conn)))
    with pytest.raises(AssessmentUnavailableError):
        service.request_start_assessment(session["id"], task.id, message["id"], "a1")
    assert not client.calls
    kp = AssessmentRepository(conn).create_knowledge_point("Available KP")
    conn.execute("UPDATE tasks SET knowledge_point_id=? WHERE id=?", (kp["id"], task.id))
    conn.commit()
    pending = service.request_start_assessment(session["id"], task.id, message["id"], "a1")
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    failed = service.approve_and_execute(pending["approval_id"])
    assert failed["failure_code"] == "assessment_generation_failed"
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    assert AssessmentRepository(conn).get_knowledge_point(kp["id"])["mastery_estimate"] == 0
    assert "private AI failure" not in str(service.repository.list_events(pending["approval_id"]))
    another = agent.add_message(session["id"], "assistant", "",
        tool_calls_json='[{"id":"a2","name":"request_start_assessment","arguments":"{}"}]')
    next_pending = service.request_start_assessment(session["id"], task.id, another["id"], "a2")
    service.reject(next_pending["approval_id"])
    assert len(client.calls) == 1


def test_note_validators_and_execution_reload_persisted_payload(conn):
    import json
    from app.agent.approval.service import InvalidNoteError
    from app.database.skill_repository import LearningOutcomeRepository
    from app.services.learning_outcome_service import LearningOutcomeService
    task_service = TaskService(TaskRepository(conn))
    task = task_service.create_task("Note", scheduled_date="2026-09-15")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    note = {"title": "  Title  ", "content": "  Body  "}
    assistant = agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
        {"id": "n1", "name": "request_save_learning_note", "arguments": json.dumps(note)}]))
    service = AgentApprovalService(AgentApprovalRepository(conn), task_service,
        outcome_service=LearningOutcomeService(LearningOutcomeRepository(conn)))
    for invalid in ({"title": "", "content": "body"},
                    {"title": "x" * 121, "content": "body"},
                    {"title": "title", "content": "y" * 8001},
                    {"title": "title", "content": "body", "extra": "bad"},
                    {"title": 1, "content": "body"}):
        with pytest.raises(InvalidNoteError):
            service.request_save_learning_note(session["id"], task.id, assistant["id"], "n1", invalid)
    assert not service.list_pending_for_session(session["id"])
    pending = service.request_save_learning_note(session["id"], task.id, assistant["id"], "n1", note)
    # A test-only corruption simulates a payload that fails execution-time validation.
    conn.execute("UPDATE agent_messages SET tool_calls_json=? WHERE id=?",
        (json.dumps([{"id":"n1","name":"request_save_learning_note",
                      "arguments":json.dumps({"title":"", "content":"bad"})}]), assistant["id"]))
    conn.commit()
    assert service.approve_and_execute(pending["approval_id"])["failure_code"] == "note_invalid"
    assert conn.execute("SELECT COUNT(*) FROM learning_outcomes").fetchone()[0] == 0


def test_note_save_failure_and_rejection_keep_application_state(conn):
    import json
    tasks = TaskService(TaskRepository(conn))
    task = tasks.create_task("Note fail", scheduled_date="2026-09-15")
    agent = AgentRepository(conn)
    session = agent.create_session(task.id)
    def call(call_id):
        return agent.add_message(session["id"], "assistant", "", tool_calls_json=json.dumps([
            {"id":call_id, "name":"request_save_learning_note",
             "arguments":json.dumps({"title":"Note", "content":"Body"})}]))
    class BrokenOutcomes:
        calls = 0
        def create_learning_note_for_task(self, task, title, content):
            self.calls += 1
            raise RuntimeError("PRIVATE_DATABASE_EXCEPTION")
    outcomes = BrokenOutcomes()
    service = AgentApprovalService(AgentApprovalRepository(conn), tasks, outcome_service=outcomes)
    first = call("n1")
    pending = service.request_save_learning_note(session["id"], task.id, first["id"], "n1",
        {"title":"Note", "content":"Body"})
    service.reject(pending["approval_id"])
    assert outcomes.calls == 0
    second = call("n2")
    pending = service.request_save_learning_note(session["id"], task.id, second["id"], "n2",
        {"title":"Note", "content":"Body"})
    failed = service.approve_and_execute(pending["approval_id"])
    assert failed["failure_code"] == "note_save_failed"
    assert "PRIVATE_DATABASE_EXCEPTION" not in str(service.repository.list_events(pending["approval_id"]))
    assert tasks.get_status(task.id) == "active"
    assert conn.execute("SELECT COUNT(*) FROM learning_outcomes").fetchone()[0] == 0


def test_request_non_active_or_missing_identity_does_not_create_approval(conn):
    service, task, session, call, approval = setup(conn)
    with pytest.raises(ValueError):
        approval.request_complete_current_task(session["id"], task.id, 0, "")
    service.complete_task(task.id)
    with pytest.raises(ApprovalRequestError):
        request(task, session, call, approval)
    assert approval.list_pending_for_session(session["id"]) == []
