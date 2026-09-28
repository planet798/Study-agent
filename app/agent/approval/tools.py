"""Local model tools can request approval metadata, never execute application writes."""
from __future__ import annotations

from ..tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA, AgentToolError
from .service import (AgentApprovalService, ApprovalRequestError, AssessmentUnavailableError,
                      InvalidNoteError, PendingActionError)

NOTE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
    "required": ["title", "content"], "additionalProperties": False,
}


class ApprovalToolError(AgentToolError):
    code = "approval_request_failed"


class TaskNotActiveToolError(ApprovalToolError):
    code = "task_not_active"


class AssessmentUnavailableToolError(ApprovalToolError):
    code = "assessment_not_available"


class InvalidNoteToolError(ApprovalToolError):
    code = "invalid_note"


class PendingActionToolError(ApprovalToolError):
    code = "approval_action_already_pending"


def _request(service, action, context, arguments):
    try:
        if action == "complete":
            return service.request_complete_current_task(
                context.session_id, context.task_id,
                context.assistant_message_id, context.tool_call_id,
            )
        if action == "assessment":
            return service.request_start_assessment(
                context.session_id, context.task_id,
                context.assistant_message_id, context.tool_call_id,
            )
        return service.request_save_learning_note(
            context.session_id, context.task_id,
            context.assistant_message_id, context.tool_call_id, arguments,
        )
    except PendingActionError:
        raise PendingActionToolError("Resolve the existing pending action first.") from None
    except AssessmentUnavailableError:
        raise AssessmentUnavailableToolError("Assessment is unavailable.") from None
    except InvalidNoteError:
        raise InvalidNoteToolError("Note arguments are invalid.") from None
    except ApprovalRequestError:
        raise TaskNotActiveToolError("Current task is not active.") from None
    except ValueError:
        raise ApprovalToolError("Approval request could not be created.") from None


class RequestCompleteCurrentTaskTool(AgentTool):
    def __init__(self, service: AgentApprovalService):
        self.service = service

    @property
    def spec(self) -> AgentToolSpec:
        return AgentToolSpec(
            name="request_complete_current_task",
            description=("Requests explicit user approval to mark the current task completed. "
                         "This tool does NOT complete the task itself."),
            parameters=EMPTY_OBJECT_SCHEMA, read_only=False, mutation_scope="approval",
        )

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        return _request(self.service, "complete", context, arguments)


class RequestStartAssessmentTool(AgentTool):
    def __init__(self, service: AgentApprovalService):
        self.service = service

    @property
    def spec(self) -> AgentToolSpec:
        return AgentToolSpec(
            name="request_start_assessment",
            description="Requests explicit user approval to start or resume formal Assessment; does not create questions or change Mastery.",
            parameters=EMPTY_OBJECT_SCHEMA, read_only=False, mutation_scope="approval",
        )

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        return _request(self.service, "assessment", context, arguments)


class RequestSaveLearningNoteTool(AgentTool):
    def __init__(self, service: AgentApprovalService):
        self.service = service

    @property
    def spec(self) -> AgentToolSpec:
        return AgentToolSpec(
            name="request_save_learning_note",
            description="Requests explicit user approval to save a bounded learning note; does not save it yet.",
            parameters=NOTE_SCHEMA, read_only=False, mutation_scope="approval",
        )

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        return _request(self.service, "note", context, arguments)
