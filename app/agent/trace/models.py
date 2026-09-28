"""Small, content-free value types and bounds for operational Agent tracing."""

from __future__ import annotations

from dataclasses import dataclass

MAX_TRACE_DETAILS_CHARS = 4096
TRACE_ERROR_CODES = frozenset({
    "ai_service_error",
    "context_too_large",
    "agent_runtime_error",
    "memory_error",
    "unexpected_error",
})


@dataclass(frozen=True)
class TraceReceipt:
    trace_id: int = 0
    evaluation_status: str = ""
