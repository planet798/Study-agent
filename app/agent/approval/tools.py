"""A model can request approval, never execute application mutation."""
from __future__ import annotations

from ..tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA, AgentToolError
from .service import AgentApprovalService, ApprovalRequestError


class ApprovalToolError(AgentToolError):
    code = "approval_request_failed"


class TaskNotActiveToolError(ApprovalToolError):
    code = "task_not_active"


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
        try:
            return self.service.request_complete_current_task(
                context.session_id, context.task_id,
                context.assistant_message_id, context.tool_call_id,
            )
        except ApprovalRequestError:
            raise TaskNotActiveToolError("Current task is not active.") from None
        except ValueError:
            raise ApprovalToolError("Approval request could not be created.") from None
