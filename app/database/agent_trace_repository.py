"""Transactional persistence for content-free Agent turn traces and ordered events."""

from __future__ import annotations

import json
import re
import sqlite3

_MAX_DETAILS_CHARS = 4096
_INT_MAX = (1 << 63) - 1
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{0,64}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SERVER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_ALLOWED_ERROR_CODES = {
    "", "ai_service_error", "context_too_large", "agent_runtime_error",
    "memory_error", "unexpected_error", "tool_error", "tool_execution_error",
    "invalid_tool_arguments", "tool_not_found", "invalid_arguments",
    "tool_execution_failed", "sandbox_tool_failed", "sandbox_execution_unavailable",
    "mcp_tool_failed", "mcp_result_too_large", "task_not_active", "approval_request_failed",
    "assessment_not_available", "invalid_note", "approval_action_already_pending",
}
_MODEL_SUCCESS_FIELDS = {
    "purpose", "model", "finish_reason", "request_message_count", "request_chars",
    "tool_definition_count", "response_chars", "returned_tool_calls", "prompt_tokens",
    "completion_tokens", "total_tokens", "usage_complete",
}
_EVENT_FIELDS = {
    ("memory", "prepare"): {
        "compacted", "omitted_earlier", "through_message_id", "error_code",
    },
    ("memory", "request_window"): {"omitted_earlier", "through_message_id"},
    ("memory", "summary"): {"error_code"},
    ("runtime", "task_context"): {"available"},
    ("runtime", "skill_selection"): {"skill_key"},
    ("mcp", "discovery"): {
        "available_server_keys", "unavailable_server_keys", "approved_tool_count",
    },
    ("sandbox", "scope"): {"file_tools", "execution_available", "tool_count"},
    ("tool", "*"): {"tool_kind", "ok", "error_code"},
    ("model", "*"): {
        "purpose", "error_code", "model", "finish_reason", "request_message_count",
        "request_chars", "tool_definition_count", "response_chars", "returned_tool_calls",
        "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete",
    },
}


def _required_event_fields(kind: str, name: str, status: str) -> set[str]:
    if kind == "model":
        return {"purpose", "error_code"} if status == "error" else _MODEL_SUCCESS_FIELDS
    if kind == "tool":
        return _EVENT_FIELDS[("tool", "*")] if status == "error" else {"tool_kind", "ok"}
    if kind == "memory" and name == "prepare":
        base = {"compacted", "omitted_earlier", "through_message_id"}
        return base | {"error_code"} if status == "error" else base
    if kind == "memory" and name == "summary":
        return {"error_code"}
    if kind == "memory" and name == "request_window":
        return {"omitted_earlier", "through_message_id"}
    if kind == "runtime" and name == "task_context":
        return {"available"}
    if kind == "runtime" and name == "skill_selection":
        return {"skill_key"}
    if kind == "mcp" and name == "discovery":
        return {"available_server_keys", "unavailable_server_keys", "approved_tool_count"}
    if kind == "sandbox" and name == "scope":
        return {"file_tools", "execution_available", "tool_count"}
    return set()


class AgentTraceRepository:
    """Trace/event persistence; never reads or writes conversation message content."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record_turn(self, trace: dict, events: tuple[dict, ...]) -> dict:
        clean = self._validate_trace(trace)
        clean_events = self._validate_events(events)
        self._validate_ownership(clean)
        savepoint = "agent_trace_write"
        nested = self.conn.in_transaction
        if nested:
            self.conn.execute(f"SAVEPOINT {savepoint}")
        else:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            columns = tuple(clean)
            placeholders = ", ".join("?" for _ in columns)
            cursor = self.conn.execute(
                f"INSERT INTO agent_turn_traces ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                tuple(clean[key] for key in columns),
            )
            trace_id = int(cursor.lastrowid)
            for event in clean_events:
                self.conn.execute(
                    "INSERT INTO agent_trace_events "
                    "(trace_id, seq, kind, name, status, duration_ms, details_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (trace_id, event["seq"], event["kind"], event["name"],
                     event["status"], event["duration_ms"], event["details_json"],
                     event["created_at"]),
                )
            if nested:
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.conn.commit()
        except Exception:
            if nested:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.conn.rollback()
            raise
        result = self.get(trace_id)
        if result is None:  # defensive: INSERT succeeded, so this should be impossible
            raise RuntimeError("persisted Agent trace could not be read")
        return result

    def get(self, trace_id: int) -> dict | None:
        tid = self._positive_int(trace_id, "trace_id")
        row = self.conn.execute(
            "SELECT * FROM agent_turn_traces WHERE id = ?", (tid,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_events(self, trace_id: int) -> list[dict]:
        tid = self._positive_int(trace_id, "trace_id")
        rows = self.conn.execute(
            "SELECT * FROM agent_trace_events WHERE trace_id = ? ORDER BY seq",
            (tid,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_for_session(self, session_id: int, limit: int = 100) -> list[dict]:
        sid = self._positive_int(session_id, "session_id")
        bounded_limit = self._positive_int(limit, "limit")
        if bounded_limit > 1000:
            raise ValueError("limit must not exceed 1000")
        rows = self.conn.execute(
            "SELECT * FROM agent_turn_traces WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (sid, bounded_limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _validate_ownership(self, trace: dict) -> None:
        session = self.conn.execute(
            "SELECT task_id FROM agent_sessions WHERE id = ?",
            (trace["session_id"],),
        ).fetchone()
        if session is None:
            raise ValueError("trace session does not exist")
        if int(session[0]) != trace["task_id"]:
            raise ValueError("trace task_id does not own session_id")
        user = self.conn.execute(
            "SELECT session_id, role FROM agent_messages WHERE id = ?",
            (trace["user_message_id"],),
        ).fetchone()
        if (user is None or int(user[0]) != trace["session_id"]
                or user[1] != "user"):
            raise ValueError("trace user_message_id must reference a user message in its session")
        assistant_id = trace["assistant_message_id"]
        if assistant_id is not None:
            assistant = self.conn.execute(
                "SELECT session_id, role FROM agent_messages WHERE id = ?",
                (assistant_id,),
            ).fetchone()
            if (assistant is None or int(assistant[0]) != trace["session_id"]
                    or assistant[1] != "assistant"):
                raise ValueError(
                    "trace assistant_message_id must reference an assistant message in its session"
                )

    @classmethod
    def _validate_trace(cls, trace: dict) -> dict:
        if not isinstance(trace, dict):
            raise ValueError("trace must be an object")
        required = {
            "session_id", "task_id", "user_message_id", "assistant_message_id",
            "status", "skill_key", "memory_compacted", "memory_omitted_earlier",
            "memory_through_message_id", "tool_rounds", "model_call_count",
            "memory_model_call_count", "tool_call_count", "tool_error_count",
            "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete",
            "error_code", "started_at", "finished_at", "duration_ms",
        }
        if set(trace) != required:
            raise ValueError("trace fields are invalid")
        clean = dict(trace)
        for name in ("session_id", "task_id", "user_message_id"):
            clean[name] = cls._positive_int(clean[name], name)
        if clean["assistant_message_id"] is not None:
            clean["assistant_message_id"] = cls._positive_int(
                clean["assistant_message_id"], "assistant_message_id"
            )
        if (not isinstance(clean["status"], str)
                or clean["status"] not in {"succeeded", "failed"}):
            raise ValueError("trace status is invalid")
        if clean["status"] == "succeeded" and clean["assistant_message_id"] is None:
            raise ValueError("successful trace requires an assistant message")
        for flag in ("memory_compacted", "memory_omitted_earlier", "usage_complete"):
            if type(clean[flag]) is not int or clean[flag] not in (0, 1):
                raise ValueError(f"{flag} must be 0 or 1")
        if clean["memory_through_message_id"] is not None:
            clean["memory_through_message_id"] = cls._nonnegative_int(
                clean["memory_through_message_id"], "memory_through_message_id"
            )
        for name in (
            "tool_rounds", "model_call_count", "memory_model_call_count", "tool_call_count",
            "tool_error_count", "prompt_tokens", "completion_tokens", "total_tokens",
            "duration_ms",
        ):
            clean[name] = cls._nonnegative_int(clean[name], name)
        if clean["memory_model_call_count"] > clean["model_call_count"]:
            raise ValueError("memory_model_call_count cannot exceed model_call_count")
        if clean["tool_error_count"] > clean["tool_call_count"]:
            raise ValueError("tool_error_count cannot exceed tool_call_count")
        if not isinstance(clean["skill_key"], str) or not _KEY_RE.fullmatch(clean["skill_key"]):
            raise ValueError("trace skill_key is invalid")
        if (not isinstance(clean["error_code"], str)
                or clean["error_code"] not in _ALLOWED_ERROR_CODES):
            raise ValueError("trace error_code is invalid")
        for name in ("started_at", "finished_at"):
            if (not isinstance(clean[name], str) or not clean[name]
                    or len(clean[name]) > 64):
                raise ValueError(f"trace {name} is invalid")
        return clean

    @classmethod
    def _validate_events(cls, events) -> tuple[dict, ...]:
        if not isinstance(events, (tuple, list)):
            raise ValueError("events must be an ordered sequence")
        clean_events = []
        for expected_seq, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise ValueError("trace event must be an object")
            required = {
                "seq", "kind", "name", "status", "duration_ms", "details_json", "created_at",
            }
            if set(event) != required:
                raise ValueError("trace event fields are invalid")
            if (isinstance(event["seq"], bool) or not isinstance(event["seq"], int)
                    or event["seq"] != expected_seq):
                raise ValueError("trace event seq must be contiguous and start at 1")
            kind = event["kind"]
            status = event["status"]
            name = event["name"]
            if (not isinstance(kind, str)
                    or kind not in {"runtime", "memory", "model", "tool", "mcp", "sandbox"}):
                raise ValueError("trace event kind is invalid")
            if not isinstance(status, str) or status not in {"info", "ok", "error"}:
                raise ValueError("trace event status is invalid")
            if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
                raise ValueError("trace event name is invalid")
            duration = cls._nonnegative_int(event["duration_ms"], "event duration_ms")
            details = event["details_json"]
            if not isinstance(details, str) or len(details) > _MAX_DETAILS_CHARS:
                raise ValueError("trace event details exceed the configured bound")
            try:
                parsed = json.loads(details)
            except (json.JSONDecodeError, TypeError):
                raise ValueError("trace event details must be valid JSON") from None
            cls._validate_event_details(kind, name, status, parsed)
            created_at = event["created_at"]
            if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
                raise ValueError("trace event created_at is invalid")
            clean_events.append({
                "seq": expected_seq, "kind": kind, "name": name, "status": status,
                "duration_ms": duration, "details_json": details, "created_at": created_at,
            })
        return tuple(clean_events)

    @staticmethod
    def _validate_event_details(kind: str, name: str, status: str, details) -> None:
        if not isinstance(details, dict):
            raise ValueError("trace event details must be a JSON object")
        allowed = _EVENT_FIELDS.get((kind, name)) or _EVENT_FIELDS.get((kind, "*"))
        required = _required_event_fields(kind, name, status)
        if (allowed is None or set(details) - allowed
                or not required.issubset(details)):
            raise ValueError("trace event details contain unsupported or missing fields")
        for key, value in details.items():
            if key in {"available", "compacted", "omitted_earlier", "file_tools",
                       "execution_available", "ok", "usage_complete"}:
                if type(value) is not bool:
                    raise ValueError(f"trace detail {key} must be boolean")
            elif key in {"through_message_id", "approved_tool_count", "tool_count",
                         "request_message_count", "request_chars", "tool_definition_count",
                         "response_chars", "returned_tool_calls", "prompt_tokens",
                         "completion_tokens", "total_tokens"}:
                AgentTraceRepository._nonnegative_int(value, key)
            elif key in {"available_server_keys", "unavailable_server_keys"}:
                if (not isinstance(value, list) or len(value) > 64
                        or any(not isinstance(v, str) or not _SERVER_KEY_RE.fullmatch(v)
                               for v in value)):
                    raise ValueError(f"trace detail {key} is invalid")
            elif key == "purpose":
                if not isinstance(value, str) or value not in {"agent", "memory_summary"}:
                    raise ValueError("trace purpose is invalid")
            elif key == "tool_kind":
                if not isinstance(value, str) or value not in {"native", "mcp", "sandbox", "approval"}:
                    raise ValueError("trace tool_kind is invalid")
            elif key == "error_code":
                if not isinstance(value, str) or value not in _ALLOWED_ERROR_CODES:
                    raise ValueError("trace error_code is invalid")
            elif key == "model":
                if (not isinstance(value, str) or len(value) > 128
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value)
                        or "://" in value or "@" in value or value.lower().startswith("sk-")):
                    raise ValueError("trace model identifier is invalid")
            elif key == "finish_reason":
                if (not isinstance(value, str) or value not in {
                    "stop", "length", "tool_calls", "function_call",
                    "content_filter", "error", "other", "unknown",
                }):
                    raise ValueError("trace finish_reason is invalid")
            elif key == "skill_key":
                if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
                    raise ValueError("trace skill key is invalid")
            else:
                raise ValueError("unsupported trace detail")

    @staticmethod
    def _positive_int(value, name: str) -> int:
        if (isinstance(value, bool) or not isinstance(value, int)
                or value <= 0 or value > _INT_MAX):
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _nonnegative_int(value, name: str) -> int:
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < 0 or value > _INT_MAX):
            raise ValueError(f"{name} must be a non-negative integer")
        return value
