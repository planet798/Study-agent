"""Transaction-bound approval metadata and append-only authorization events."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager

from ..utils.date_utils import now_iso

ACTION = "request_complete_current_task"
_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
CODES = {"", "completed", "already_done", "task_not_active", "task_state_changed", "execution_failed"}
TRANSITIONS = {"pending": {"approved", "rejected"}, "approved": {"executed", "failed"}}
ACTORS = {"requested": "agent", "approved": "user", "rejected": "user",
          "executed": "system", "failed": "system"}


class AgentApprovalRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @contextmanager
    def _atomic(self):
        # Preserve any caller-owned transaction while rolling back partial events.
        nested = self.conn.in_transaction
        self.conn.execute("SAVEPOINT approval_write")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK TO SAVEPOINT approval_write")
            self.conn.execute("RELEASE SAVEPOINT approval_write")
            raise
        else:
            self.conn.execute("RELEASE SAVEPOINT approval_write")
            if not nested:
                self.conn.commit()

    def get(self, approval_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM agent_approval_requests WHERE id=?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def get_by_tool_call(self, session_id: int, tool_call_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_approval_requests WHERE session_id=? AND tool_call_id=?",
            (session_id, tool_call_id),
        ).fetchone()
        return dict(row) if row else None

    def find_pending(self, session_id: int, tool_name: str = ACTION) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_approval_requests WHERE session_id=? AND tool_name=? AND status='pending'",
            (session_id, tool_name),
        ).fetchone()
        return dict(row) if row else None

    def list_pending_for_session(self, session_id: int) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM agent_approval_requests WHERE session_id=? AND status='pending' ORDER BY id",
            (session_id,),
        )]

    def list_events(self, approval_id: int) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM agent_approval_events WHERE approval_id=? ORDER BY id",
            (approval_id,),
        )]

    def create_request(self, session_id: int, task_id: int, assistant_message_id: int,
                       tool_call_id: str, tool_name: str = ACTION) -> tuple[dict, bool]:
        if tool_name != ACTION or not isinstance(tool_call_id, str) or not _TOOL_CALL_ID.fullmatch(tool_call_id):
            raise ValueError("unknown approval action or missing tool call identity")
        with self._atomic():
            session = self.conn.execute("SELECT task_id FROM agent_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None or session[0] != task_id:
                raise ValueError("approval session/task ownership invalid")
            message = self.conn.execute(
                "SELECT session_id, role, tool_calls_json FROM agent_messages WHERE id=?",
                (assistant_message_id,),
            ).fetchone()
            if message is None or message[0] != session_id or message[1] != "assistant":
                raise ValueError("approval assistant message ownership invalid")
            try:
                calls = json.loads(message[2])
            except (TypeError, ValueError):
                raise ValueError("invalid persisted tool calls") from None
            bound = ([call for call in calls if isinstance(call, dict)
                      and call.get("id") == tool_call_id]
                     if isinstance(calls, list) else [])
            if (len(bound) != 1 or bound[0].get("name") != tool_name
                    or bound[0].get("arguments") != "{}"):
                raise ValueError("approval tool call binding invalid")
            existing = self.get_by_tool_call(session_id, tool_call_id)
            if existing:
                if existing["assistant_message_id"] != assistant_message_id or existing["tool_name"] != tool_name:
                    raise ValueError("approval tool call replay mismatch")
                return existing, True
            pending = self.find_pending(session_id, tool_name)
            if pending:
                return pending, True
            ts = now_iso()
            cursor = self.conn.execute(
                "INSERT INTO agent_approval_requests "
                "(session_id,task_id,assistant_message_id,tool_call_id,tool_name,requested_at) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, task_id, assistant_message_id, tool_call_id, tool_name, ts),
            )
            approval_id = cursor.lastrowid
            self._event(approval_id, "requested", "", ts)
        return self.get(approval_id), False

    def transition(self, approval_id: int, to_status: str, code: str = "") -> dict:
        if code not in CODES or not isinstance(code, str):
            raise ValueError("invalid approval code")
        with self._atomic():
            row = self.get(approval_id)
            if row is None or to_status not in TRANSITIONS.get(row["status"], set()):
                raise ValueError("invalid approval transition")
            ts = now_iso()
            if to_status in ("approved", "rejected"):
                cursor = self.conn.execute(
                    "UPDATE agent_approval_requests SET status=?,decided_at=? WHERE id=? AND status='pending'",
                    (to_status, ts, approval_id),
                )
            else:
                cursor = self.conn.execute(
                    "UPDATE agent_approval_requests SET status=?,executed_at=?,failure_code=? "
                    "WHERE id=? AND status='approved'",
                    (to_status, ts, code if to_status == "failed" else "", approval_id),
                )
            if cursor.rowcount != 1:
                raise ValueError("approval transition was superseded")
            self._event(approval_id, to_status, code, ts)
        return self.get(approval_id)

    def _event(self, approval_id: int, event_type: str, code: str, ts: str):
        self.conn.execute(
            "INSERT INTO agent_approval_events (approval_id,event_type,actor,code,created_at) VALUES (?,?,?,?,?)",
            (approval_id, event_type, ACTORS[event_type], code, ts),
        )

    def consistency_problems(self) -> list[int]:
        problems = []
        for row in self.conn.execute("SELECT id,status FROM agent_approval_requests ORDER BY id"):
            events = self.list_events(row["id"])
            types = [event["event_type"] for event in events]
            valid = {"pending": ["requested"], "approved": ["requested", "approved"],
                     "rejected": ["requested", "rejected"],
                     "executed": ["requested", "approved", "executed"],
                     "failed": ["requested", "approved", "failed"]}
            if types != valid[row["status"]] or any(
                event["actor"] != ACTORS[event["event_type"]] for event in events
            ):
                problems.append(row["id"])
        return problems
