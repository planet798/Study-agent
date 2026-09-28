"""User-decision boundary for the sole approved application write."""
from __future__ import annotations

from ...database.agent_approval_repository import ACTION, AgentApprovalRepository
from ...database.schema import STATUS_ACTIVE, STATUS_DONE
from ...services.task_service import TaskService


class ApprovalRequestError(Exception):
    code = "task_not_active"


class AgentApprovalService:
    def __init__(self, repository: AgentApprovalRepository, task_service: TaskService):
        self.repository = repository
        self.task_service = task_service

    def request_complete_current_task(self, session_id: int, task_id: int,
                                      assistant_message_id: int, tool_call_id: str) -> dict:
        if not assistant_message_id or not tool_call_id:
            raise ValueError("missing persisted assistant tool-call identity")
        if self.task_service.get_task(task_id).status != STATUS_ACTIVE:
            raise ApprovalRequestError("The current task is not active.")
        row, reused = self.repository.create_request(
            session_id, task_id, assistant_message_id, tool_call_id, ACTION
        )
        if row["status"] != "pending":
            raise ValueError("approval call was already resolved")
        return {"approval_required": True, "approval_id": row["id"],
                "action": "complete_current_task", "status": "pending", "reused": reused}

    def list_pending_for_session(self, session_id: int) -> list[dict]:
        return self.repository.list_pending_for_session(session_id)

    def get_pending_for_session(self, approval_id: int, session_id: int) -> dict | None:
        """Return only a recognized pending action owned by this Session."""
        row = self.repository.get(int(approval_id))
        if (row is None or row["status"] != "pending"
                or row["session_id"] != int(session_id)
                or row["tool_name"] != ACTION):
            return None
        return row

    def reject(self, approval_id: int) -> dict:
        row = self.repository.get(approval_id)
        if row is None or row["tool_name"] != ACTION or row["status"] != "pending":
            raise ValueError("approval is no longer pending")
        return self.repository.transition(approval_id, "rejected")

    def approve_and_execute(self, approval_id: int) -> dict:
        row = self.repository.get(approval_id)
        if row is None or row["tool_name"] != ACTION:
            raise ValueError("unknown approval")
        if row["status"] in ("executed", "failed"):
            return row  # replay never runs application completion again
        if row["status"] == "rejected":
            raise ValueError("rejected approval cannot be executed")
        if row["status"] == "pending":
            row = self.repository.transition(approval_id, "approved")
        try:
            status = self.task_service.get_task(row["task_id"]).status
            if status == STATUS_DONE:
                code = "already_done"
            elif status != STATUS_ACTIVE:
                return self.repository.transition(approval_id, "failed", "task_state_changed")
            else:
                self.task_service.complete_task(row["task_id"])
                code = "completed"
        except Exception:  # do not persist raw exception or leak internal details
            return self.repository.transition(approval_id, "failed", "execution_failed")
        return self.repository.transition(approval_id, "executed", code)
