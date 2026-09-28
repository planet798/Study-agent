"""In-memory, content-free Agent turn event collector."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from ...ai.agent_protocol import AgentModelClient, ModelRequest, ModelResponse
from ...utils.date_utils import now_iso
from .models import MAX_TRACE_DETAILS_CHARS, TRACE_ERROR_CODES

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SAFE_SERVER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_MAX_SQLITE_INT = (1 << 63) - 1

_DETAIL_FIELDS = {
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
}
_MODEL_FIELDS = {
    "purpose", "error_code", "model", "finish_reason", "request_message_count",
    "request_chars", "tool_definition_count", "response_chars", "returned_tool_calls",
    "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete",
}
_TOOL_FIELDS = {"tool_kind", "ok", "error_code"}
_MODEL_SUCCESS_FIELDS = {
    "purpose", "model", "finish_reason", "request_message_count", "request_chars",
    "tool_definition_count", "response_chars", "returned_tool_calls", "prompt_tokens",
    "completion_tokens", "total_tokens", "usage_complete",
}


def _required_detail_fields(kind: str, name: str, status: str) -> set[str]:
    if kind == "model":
        return {"purpose", "error_code"} if status == "error" else _MODEL_SUCCESS_FIELDS
    if kind == "tool":
        return _TOOL_FIELDS if status == "error" else {"tool_kind", "ok"}
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


class AgentTraceCollector:
    """Pure-Python event/timing accumulator; owns no persistence or runtime services."""

    def __init__(
        self,
        session_id: int,
        task_id: int,
        user_message_id: int,
        *,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        wall_clock: Callable[[], str] = now_iso,
    ):
        self.session_id = self._positive_id(session_id, "session_id")
        self.task_id = self._positive_id(task_id, "task_id")
        self.user_message_id = self._positive_id(user_message_id, "user_message_id")
        self._monotonic_ns = monotonic_ns
        self._wall_clock = wall_clock
        self._started_ns = monotonic_ns()
        self._started_at = self._safe_wall_clock()
        self._events: list[dict] = []
        self._finalized = False

    @staticmethod
    def _positive_id(value: int, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value

    def timer_start(self) -> int:
        return self._monotonic_ns()

    def elapsed_ms(self, started_ns: int) -> int:
        return max(0, (self._monotonic_ns() - int(started_ns)) // 1_000_000)

    def record_event(
        self,
        kind: str,
        name: str,
        status: str,
        duration_ms: int = 0,
        details: dict | None = None,
    ) -> None:
        """Validate and append a bounded event containing only allowlisted metadata."""
        if self._finalized:
            raise RuntimeError("trace collector is already finalized")
        if not isinstance(kind, str) or kind not in {
            "runtime", "memory", "model", "tool", "mcp", "sandbox"
        }:
            raise ValueError("invalid trace event kind")
        if not isinstance(status, str) or status not in {"info", "ok", "error"}:
            raise ValueError("invalid trace event status")
        duration = self._nonnegative_int(duration_ms, "duration_ms")
        safe_name = self._safe_event_name(kind, name)
        clean = self._clean_details(kind, safe_name, status, details or {})
        encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded) > MAX_TRACE_DETAILS_CHARS:
            raise ValueError("trace event details exceed the configured bound")
        self._events.append({
            "seq": len(self._events) + 1,
            "kind": kind,
            "name": safe_name,
            "status": status,
            "duration_ms": duration,
            "details_json": encoded,
            "created_at": self._safe_wall_clock(),
        })

    def complete_model(
        self,
        model_client: AgentModelClient,
        request: ModelRequest,
        *,
        purpose: str,
    ) -> ModelResponse:
        """Call the model once, observe safe metadata, and propagate its original error."""
        if purpose not in {"agent", "memory_summary"}:
            raise ValueError("invalid model-call purpose")
        try:
            started = self.timer_start()
        except Exception:
            started = None
        try:
            response = model_client.complete(request)
        except Exception as exc:
            duration = self._elapsed_safely(started)
            self._record_safely(
                "model", "memory_summary" if purpose == "memory_summary" else "agent",
                "error", duration,
                {"purpose": purpose, "error_code": error_code_for_exception(exc)},
            )
            raise

        duration = self._elapsed_safely(started)
        if not isinstance(response, ModelResponse):
            self._record_safely(
                "model", "memory_summary" if purpose == "memory_summary" else "agent",
                "error", duration,
                {"purpose": purpose, "error_code": "unexpected_error"},
            )
            raise TypeError("Agent model client returned an invalid response")

        usage = _normalize_usage(response.usage)
        details = {
            "purpose": purpose,
            "model": _safe_model_name(response.model),
            "finish_reason": _safe_finish_reason(response.finish_reason),
            "request_message_count": len(request.messages),
            "request_chars": _estimate_request_chars(request),
            "tool_definition_count": len(request.tools),
            "response_chars": len(response.content) if isinstance(response.content, str) else 0,
            "returned_tool_calls": (
                len(response.tool_calls)
                if isinstance(response.tool_calls, (tuple, list)) else 0
            ),
            **usage,
        }
        self._record_safely(
            "model", "memory_summary" if purpose == "memory_summary" else "agent",
            "ok", duration, details,
        )
        return response

    def record_tool_call(
        self,
        name: str,
        envelope: Any,
        duration_ms: int,
        *,
        execution_error: bool = False,
        tool_kind: str | None = None,
    ) -> None:
        safe_name = name if isinstance(name, str) and _IDENTIFIER_RE.fullmatch(name) else "other_tool"
        kind = tool_kind if tool_kind in ("native", "mcp", "sandbox", "approval") else classify_tool_kind(safe_name)
        if execution_error:
            details = {"tool_kind": kind, "ok": False,
                       "error_code": "tool_execution_error"}
            self._record_safely("tool", safe_name, "error", duration_ms, details)
            return
        ok = isinstance(envelope, dict) and type(envelope.get("ok")) is bool
        succeeded = bool(ok and envelope["ok"])
        details = {"tool_kind": kind, "ok": succeeded}
        if not succeeded:
            error = envelope.get("error") if isinstance(envelope, dict) else None
            code = error.get("code") if isinstance(error, dict) else "tool_error"
            details["error_code"] = _safe_error_code(code, fallback="tool_error")
        self._record_safely(
            "tool", safe_name, "ok" if succeeded else "error", duration_ms, details
        )

    def finalize(
        self,
        *,
        status: str,
        assistant_message_id: int | None,
        error_code: str = "",
    ) -> tuple[dict, tuple[dict, ...]]:
        """Freeze one completed turn summary and its ordered events."""
        if self._finalized:
            raise RuntimeError("trace collector is already finalized")
        if status not in {"succeeded", "failed"}:
            raise ValueError("invalid trace status")
        if assistant_message_id is not None:
            assistant_message_id = self._positive_id(
                assistant_message_id, "assistant_message_id"
            )
        if status == "succeeded" and not assistant_message_id:
            raise ValueError("successful trace requires a persisted assistant message")
        safe_error = _safe_error_code(error_code, fallback="") if error_code else ""
        finished_ns = self._monotonic_ns()
        finished_at = self._safe_wall_clock()
        duration = max(0, (finished_ns - self._started_ns) // 1_000_000)
        details = [json.loads(event["details_json"]) for event in self._events]
        model_events = [event for event in self._events if event["kind"] == "model"]
        tool_events = [event for event in self._events if event["kind"] == "tool"]
        memory_detail = next(
            (item for event, item in zip(self._events, details)
             if event["kind"] == "memory" and event["name"] == "prepare"),
            None,
        )
        memory_omitted = bool(
            memory_detail and memory_detail.get("omitted_earlier")
        ) or any(
            event["kind"] == "memory" and event["name"] == "request_window"
            and item.get("omitted_earlier")
            for event, item in zip(self._events, details)
        )
        skill_detail = next(
            (item for event, item in zip(self._events, details)
             if event["kind"] == "runtime" and event["name"] == "skill_selection"),
            None,
        )
        trace = {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": assistant_message_id,
            "status": status,
            "skill_key": skill_detail.get("skill_key", "") if skill_detail else "",
            "memory_compacted": int(bool(memory_detail and memory_detail.get("compacted"))),
            "memory_omitted_earlier": int(memory_omitted),
            "memory_through_message_id": (
                memory_detail.get("through_message_id") if memory_detail else None
            ),
            "tool_rounds": sum(
                1 for event, item in zip(self._events, details)
                if event["kind"] == "model" and item.get("purpose") == "agent"
                and item.get("returned_tool_calls", 0) > 0
            ),
            "model_call_count": len(model_events),
            "memory_model_call_count": sum(
                1 for event in self._events
                if event["kind"] == "model" and json.loads(event["details_json"]).get(
                    "purpose"
                ) == "memory_summary"
            ),
            "tool_call_count": len(tool_events),
            "tool_error_count": sum(1 for event in tool_events if event["status"] == "error"),
            "prompt_tokens": _bounded_sum(
                item.get("prompt_tokens", 0) for item in details if "purpose" in item
            ),
            "completion_tokens": _bounded_sum(
                item.get("completion_tokens", 0) for item in details if "purpose" in item
            ),
            "total_tokens": _bounded_sum(
                item.get("total_tokens", 0) for item in details if "purpose" in item
            ),
            "usage_complete": int(all(
                event["status"] == "ok" and item.get("usage_complete") is True
                for event, item in zip(self._events, details)
                if event["kind"] == "model"
            )),
            "error_code": safe_error,
            "started_at": self._started_at,
            "finished_at": finished_at,
            "duration_ms": duration,
        }
        self._finalized = True
        return trace, tuple(dict(event) for event in self._events)

    def _record_safely(self, *args, **kwargs) -> None:
        try:
            self.record_event(*args, **kwargs)
        except Exception:  # telemetry must never break an Agent turn
            return

    def _elapsed_safely(self, started: int | None) -> int:
        if started is None:
            return 0
        try:
            return self.elapsed_ms(started)
        except Exception:
            return 0

    def _safe_wall_clock(self) -> str:
        try:
            value = self._wall_clock()
        except Exception:
            value = now_iso()
        return value if isinstance(value, str) and value else now_iso()

    @staticmethod
    def _nonnegative_int(value, field: str) -> int:
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < 0 or value > _MAX_SQLITE_INT):
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    @staticmethod
    def _safe_event_name(kind: str, name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("trace event name must be text")
        if kind == "tool":
            return name if _IDENTIFIER_RE.fullmatch(name) else "other_tool"
        valid = {
            "memory": {"prepare", "request_window", "summary"},
            "runtime": {"task_context", "skill_selection"},
            "model": {"agent", "memory_summary"},
            "mcp": {"discovery"},
            "sandbox": {"scope"},
        }
        if name not in valid.get(kind, set()):
            raise ValueError("invalid trace event name")
        return name

    @classmethod
    def _clean_details(cls, kind: str, name: str, status: str, details: dict) -> dict:
        if not isinstance(details, dict):
            raise ValueError("trace event details must be an object")
        allowed = _DETAIL_FIELDS.get((kind, name))
        required = _required_detail_fields(kind, name, status)
        if kind == "model":
            allowed = _MODEL_FIELDS
        elif kind == "tool":
            allowed = _TOOL_FIELDS
        if (allowed is None or set(details) - allowed
                or not required.issubset(details)):
            raise ValueError("trace event details contain unsupported or missing fields")
        clean: dict = {}
        for key, value in details.items():
            if key in {"compacted", "omitted_earlier", "available", "file_tools",
                       "execution_available", "ok", "usage_complete"}:
                if type(value) is not bool:
                    raise ValueError(f"trace detail {key} must be boolean")
                clean[key] = value
            elif key in {"through_message_id", "approved_tool_count", "tool_count",
                         "request_message_count", "request_chars", "tool_definition_count",
                         "response_chars", "returned_tool_calls", "prompt_tokens",
                         "completion_tokens", "total_tokens"}:
                clean[key] = cls._nonnegative_int(value, key)
            elif key in {"available_server_keys", "unavailable_server_keys"}:
                if not isinstance(value, (tuple, list)) or len(value) > 64:
                    raise ValueError(f"trace detail {key} is invalid")
                if any(not isinstance(item, str) or not _SAFE_SERVER_KEY_RE.fullmatch(item)
                       for item in value):
                    raise ValueError(f"trace detail {key} contains an invalid server key")
                clean[key] = list(value)
            elif key == "purpose":
                if not isinstance(value, str) or value not in {"agent", "memory_summary"}:
                    raise ValueError("invalid trace model purpose")
                clean[key] = value
            elif key == "tool_kind":
                if not isinstance(value, str) or value not in {"native", "mcp", "sandbox", "approval"}:
                    raise ValueError("invalid trace tool kind")
                clean[key] = value
            elif key == "error_code":
                clean[key] = _safe_error_code(value, fallback="unexpected_error")
            elif key == "model":
                clean[key] = _safe_model_name(value)
            elif key == "finish_reason":
                clean[key] = _safe_finish_reason(value)
            elif key == "skill_key":
                if not isinstance(value, str) or (value and not _KEY_RE.fullmatch(value)):
                    raise ValueError("invalid Agent Skill key")
                clean[key] = value
            else:
                raise ValueError("unsupported trace detail")
        return clean


def classify_tool_kind(name: str) -> str:
    if name.startswith("sandbox_"):
        return "sandbox"
    if name.startswith("mcp_"):
        return "mcp"
    return "native"


def error_code_for_exception(error: Exception) -> str:
    names = {cls.__name__ for cls in type(error).__mro__}
    if "AIServiceError" in names:
        return "ai_service_error"
    if "AgentContextTooLargeError" in names:
        return "context_too_large"
    if "AgentMemoryError" in names:
        return "memory_error"
    if "AgentRuntimeError" in names:
        return "agent_runtime_error"
    return "unexpected_error"


def _safe_error_code(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value in TRACE_ERROR_CODES | {
        "tool_error", "tool_execution_error", "invalid_tool_arguments",
        "tool_not_found", "sandbox_tool_failed", "sandbox_execution_unavailable",
        "mcp_tool_failed", "mcp_result_too_large", "task_not_active", "approval_request_failed", "",
    }:
        return value
    return fallback


def _safe_model_name(value: Any) -> str:
    if (isinstance(value, str) and _MODEL_RE.fullmatch(value)
            and "://" not in value and "@" not in value
            and not value.lower().startswith("sk-")):
        return value
    return "unknown"


def _safe_finish_reason(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    if value in {"stop", "length", "tool_calls", "function_call", "content_filter", "error"}:
        return value
    return "other" if value else "unknown"


def _bounded_sum(values) -> int:
    total = 0
    for value in values:
        if value > _MAX_SQLITE_INT - total:
            return _MAX_SQLITE_INT
        total += value
    return total


def _nonnegative_usage(value: Any) -> int | None:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < 0 or value > _MAX_SQLITE_INT):
        return None
    return value


def _usage_alias(usage: dict, primary: str, secondary: str) -> tuple[int | None, bool]:
    if primary in usage:
        parsed = _nonnegative_usage(usage[primary])
        return parsed, parsed is None
    if secondary in usage:
        parsed = _nonnegative_usage(usage[secondary])
        return parsed, parsed is None
    return None, False


def _normalize_usage(usage: Any) -> dict:
    if not isinstance(usage, dict):
        return {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "usage_complete": False,
        }
    prompt, prompt_invalid = _usage_alias(usage, "prompt_tokens", "input_tokens")
    completion, completion_invalid = _usage_alias(
        usage, "completion_tokens", "output_tokens"
    )
    if "total_tokens" in usage:
        total = _nonnegative_usage(usage["total_tokens"])
        total_invalid = total is None
    elif prompt is not None and completion is not None:
        total = prompt + completion
        total_invalid = total > _MAX_SQLITE_INT
        if total_invalid:
            total = None
    else:
        total = None
        total_invalid = False
    complete = (
        prompt is not None and completion is not None and total is not None
        and not prompt_invalid and not completion_invalid and not total_invalid
        and total == prompt + completion
    )
    return {
        "prompt_tokens": prompt if prompt is not None else 0,
        "completion_tokens": completion if completion is not None else 0,
        "total_tokens": total if total is not None else 0,
        "usage_complete": bool(complete),
    }


def _estimate_request_chars(request: ModelRequest) -> int:
    total = 0
    for message in request.messages:
        total += 8 + sum(len(value or "") for value in (
            message.role, message.content, message.name, message.tool_call_id,
        ))
        total += sum(len(call.id) + len(call.name) + len(call.arguments)
                     for call in message.tool_calls)
    try:
        total += len(json.dumps(request.tools, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        pass
    return total
