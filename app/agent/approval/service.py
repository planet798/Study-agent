"""User-decision boundary: model requests metadata, user approves canonical actions."""
from __future__ import annotations

import json

from ...database.agent_approval_repository import (
    ACTION, ACTION_COMPLETE_TASK, ACTION_SAVE_NOTE, ACTION_START_ASSESSMENT,
    SUPPORTED_ACTIONS, AgentApprovalRepository,
)
from ...database.schema import STATUS_ACTIVE, STATUS_DONE
from ...services.task_service import TaskService


class ApprovalRequestError(Exception):
    code = "task_not_active"


class AssessmentUnavailableError(ApprovalRequestError):
    code = "assessment_not_available"


class InvalidNoteError(ApprovalRequestError):
    code = "invalid_note"


class PendingActionError(ApprovalRequestError):
    code = "approval_action_already_pending"


class AgentApprovalService:
    def __init__(self, repository: AgentApprovalRepository, task_service: TaskService,
                 assessment_service=None, outcome_service=None):
        self.repository = repository
        self.task_service = task_service
        self.assessment_service = assessment_service
        self.outcome_service = outcome_service

    @staticmethod
    def _parse_arguments(action: str, arguments: dict) -> dict:
        """The same canonical validator is used at request and execution time."""
        if not isinstance(arguments, dict):
            raise ValueError("approval arguments must be an object")
        if action in (ACTION_COMPLETE_TASK, ACTION_START_ASSESSMENT):
            if arguments:
                raise ValueError("this action accepts no arguments")
            return {}
        if action == ACTION_SAVE_NOTE:
            if set(arguments) != {"title", "content"}:
                raise InvalidNoteError("Note needs title and content only.")
            title, content = arguments["title"], arguments["content"]
            if not isinstance(title, str) or not isinstance(content, str):
                raise InvalidNoteError("Note fields must be text.")
            title, content = title.strip(), content.strip()
            if not 1 <= len(title) <= 120 or not 1 <= len(content) <= 8000:
                raise InvalidNoteError("Note length is outside the allowed range.")
            return {"title": title, "content": content}
        raise ValueError("unknown approval action")

    def _request(self, action: str, session_id: int, task_id: int,
                 assistant_message_id: int, tool_call_id: str, arguments: dict) -> dict:
        if not assistant_message_id or not tool_call_id:
            raise ValueError("missing persisted assistant tool-call identity")
        canonical = self._parse_arguments(action, arguments)
        bound = self.repository.get_tool_call_for_request(
            session_id, task_id, assistant_message_id, tool_call_id, action
        )
        persisted = self._parse_arguments(action, json.loads(bound["arguments"]))
        if canonical != persisted:
            raise ValueError("approval request arguments do not match the persisted call")
        task = self.task_service.get_task(task_id)
        if action == ACTION_START_ASSESSMENT:
            if task.status != STATUS_ACTIVE or task.knowledge_point_id is None:
                raise AssessmentUnavailableError("Assessment is unavailable for this task.")
            if (self.assessment_service is not None and task.route_id is not None):
                kp = self.assessment_service.get_knowledge_point(task.knowledge_point_id)
                if kp is None or (kp.get("route_id") is not None
                                  and kp["route_id"] != task.route_id):
                    raise AssessmentUnavailableError("Assessment route is unavailable.")
        elif task.status != STATUS_ACTIVE:
            raise ApprovalRequestError("The current task is not active.")
        # Repository binds the current request to the immutable assistant call.
        row, reused = self.repository.create_request(
            session_id, task_id, assistant_message_id, tool_call_id, action
        )
        if row["status"] != "pending":
            raise ValueError("approval call was already resolved")
        if action == ACTION_SAVE_NOTE and reused:
            existing = self._arguments_for(row)
            if existing != canonical:
                raise PendingActionError("Resolve the existing pending note first.")
        return {"approval_required": True, "approval_id": row["id"],
                "action": {ACTION_COMPLETE_TASK: "complete_current_task",
                           ACTION_START_ASSESSMENT: "start_assessment",
                           ACTION_SAVE_NOTE: "save_learning_note"}[action],
                "status": "pending", "reused": reused}

    def request_complete_current_task(self, session_id: int, task_id: int,
                                      assistant_message_id: int, tool_call_id: str) -> dict:
        return self._request(ACTION_COMPLETE_TASK, session_id, task_id,
                             assistant_message_id, tool_call_id, {})

    def request_start_assessment(self, session_id: int, task_id: int,
                                 assistant_message_id: int, tool_call_id: str) -> dict:
        return self._request(ACTION_START_ASSESSMENT, session_id, task_id,
                             assistant_message_id, tool_call_id, {})

    def request_save_learning_note(self, session_id: int, task_id: int,
                                   assistant_message_id: int, tool_call_id: str,
                                   arguments: dict) -> dict:
        return self._request(ACTION_SAVE_NOTE, session_id, task_id,
                             assistant_message_id, tool_call_id, arguments)

    def _arguments_for(self, row: dict) -> dict:
        call = self.repository.get_bound_tool_call(row["id"])
        return self._parse_arguments(row["tool_name"], json.loads(call["arguments"]))

    def list_pending_for_session(self, session_id: int) -> list[dict]:
        return self.repository.list_pending_for_session(session_id)

    def _view(self, row: dict) -> dict | None:
        action = row["tool_name"]
        if action not in SUPPORTED_ACTIONS or row["status"] != "pending":
            return None
        try:
            arguments = self._arguments_for(row)
        except (ValueError, ApprovalRequestError):
            return None
        view = {"id": row["id"], "session_id": row["session_id"],
                "status": "pending", "tool_name": action,
                "action": {ACTION_COMPLETE_TASK: "complete_current_task",
                           ACTION_START_ASSESSMENT: "start_assessment",
                           ACTION_SAVE_NOTE: "save_learning_note"}[action]}
        if action == ACTION_SAVE_NOTE:
            view["note_title"] = arguments["title"]
            view["note_preview"] = arguments["content"][:500]
        return view

    def list_pending_views_for_session(self, session_id: int) -> list[dict]:
        views = (self._view(row) for row in self.repository.list_pending_for_session(session_id))
        return [view for view in views if view is not None]

    def get_pending_for_session(self, approval_id: int, session_id: int) -> dict | None:
        row = self.repository.get(int(approval_id))
        if row is None or row["session_id"] != int(session_id):
            return None
        return self._view(row)

    def reject(self, approval_id: int) -> dict:
        row = self.repository.get(approval_id)
        if row is None or row["tool_name"] not in SUPPORTED_ACTIONS or row["status"] != "pending":
            raise ValueError("approval is no longer pending")
        return self.repository.transition(approval_id, "rejected")

    def approve_and_execute(self, approval_id: int) -> dict:
        row = self.repository.get(approval_id)
        if row is None or row["tool_name"] not in SUPPORTED_ACTIONS:
            raise ValueError("unknown approval")
        if row["status"] in ("executed", "failed"):
            return row  # existing complete-task callers and replay protection
        if row["status"] == "rejected":
            raise ValueError("rejected approval cannot be executed")
        if row["status"] == "pending":
            row = self.repository.transition(approval_id, "approved")
        action = row["tool_name"]
        try:
            args = self._arguments_for(row)  # never trust cached UI/model arguments
        except (ValueError, ApprovalRequestError):
            return self.repository.transition(approval_id, "failed",
                "note_invalid" if action == ACTION_SAVE_NOTE else "assessment_not_available")
        if action == ACTION_COMPLETE_TASK:
            return self._execute_completion(row)
        if action == ACTION_START_ASSESSMENT:
            return self._execute_assessment(row)
        return self._execute_note(row, args)

    def _execute_completion(self, row: dict) -> dict:
        try:
            status = self.task_service.get_task(row["task_id"]).status
            if status == STATUS_DONE:
                code = "already_done"
            elif status != STATUS_ACTIVE:
                return self.repository.transition(row["id"], "failed", "task_state_changed")
            else:
                self.task_service.complete_task(row["task_id"])
                code = "completed"
        except Exception:
            return self.repository.transition(row["id"], "failed", "execution_failed")
        return self.repository.transition(row["id"], "executed", code)

    def _execute_assessment(self, row: dict) -> dict:
        try:
            task = self.task_service.get_task(row["task_id"])
            if task.status != STATUS_ACTIVE or task.knowledge_point_id is None or self.assessment_service is None:
                return self.repository.transition(row["id"], "failed", "assessment_not_available")
            kp = self.assessment_service.get_knowledge_point(task.knowledge_point_id)
            if kp is None or (task.route_id is not None and kp.get("route_id") is not None
                              and kp["route_id"] != task.route_id):
                return self.repository.transition(row["id"], "failed", "assessment_not_available")
            attempt = self.assessment_service.get_pending_attempt_for_task(task.id)
            if attempt is not None and attempt["knowledge_point_id"] != task.knowledge_point_id:
                return self.repository.transition(row["id"], "failed", "assessment_not_available")
            code = "assessment_resumed" if attempt is not None else "assessment_started"
            if attempt is None:
                attempt = self.assessment_service.start_assessment(
                    knowledge_point_id=task.knowledge_point_id, task_id=task.id,
                    activity_kind=task.learning_activity_kind,
                )
        except Exception:
            return self.repository.transition(row["id"], "failed", "assessment_generation_failed")
        approval = self.repository.transition(row["id"], "executed", code)
        return {"status": "executed", "approval": approval, "action": "start_assessment",
                "result": {"attempt": attempt}}

    def _execute_note(self, row: dict, args: dict) -> dict:
        try:
            task = self.task_service.get_task(row["task_id"])
            if task.status != STATUS_ACTIVE or self.outcome_service is None:
                return self.repository.transition(row["id"], "failed", "note_invalid")
            outcome = self.outcome_service.create_learning_note_for_task(
                task, args["title"], args["content"]
            )
        except Exception:
            return self.repository.transition(row["id"], "failed", "note_save_failed")
        approval = self.repository.transition(row["id"], "executed", "note_saved")
        return {"status": "executed", "approval": approval, "action": "save_learning_note",
                "result": {"outcome_id": outcome["id"]}}
