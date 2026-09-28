"""Deterministic protocol/invariant checks over Trace metadata only."""

from __future__ import annotations

import json

EVALUATOR_VERSION = 2


class AgentTurnEvaluator:
    """Rule-based evaluator; has no database, model, or conversation dependency."""

    def __init__(self, max_tool_rounds: int = 4):
        if (isinstance(max_tool_rounds, bool) or not isinstance(max_tool_rounds, int)
                or max_tool_rounds < 1):
            raise ValueError("max_tool_rounds must be a positive integer")
        self.max_tool_rounds = max_tool_rounds
        self.version = EVALUATOR_VERSION

    def evaluate(self, trace: dict, events) -> dict:
        details = []
        malformed = False
        for event in events:
            try:
                value = event.get("details_json", "{}")
                parsed = json.loads(value) if isinstance(value, str) else value
                if not isinstance(parsed, dict):
                    malformed = True
                    parsed = {}
            except (json.JSONDecodeError, TypeError, AttributeError):
                malformed = True
                parsed = {}
            details.append(parsed)

        model_pairs = [
            (event, data) for event, data in zip(events, details)
            if event.get("kind") == "model"
        ]
        tool_pairs = [
            (event, data) for event, data in zip(events, details)
            if event.get("kind") == "tool"
        ]
        expected_memory_calls = sum(
            1 for _, data in model_pairs if data.get("purpose") == "memory_summary"
        )
        expected_tool_errors = sum(
            1 for event, data in tool_pairs
            if event.get("status") == "error" or data.get("ok") is False
        )
        prompt_tokens = sum(_safe_count(data.get("prompt_tokens")) for _, data in model_pairs)
        completion_tokens = sum(
            _safe_count(data.get("completion_tokens")) for _, data in model_pairs
        )
        total_tokens = sum(_safe_count(data.get("total_tokens")) for _, data in model_pairs)
        expected_usage_complete = all(
            event.get("status") == "ok" and data.get("usage_complete") is True
            for event, data in model_pairs
        )
        agent_returned_calls = sum(
            _safe_count(data.get("returned_tool_calls"))
            for _, data in model_pairs if data.get("purpose") == "agent"
        )
        expected_rounds = sum(
            1 for _, data in model_pairs
            if data.get("purpose") == "agent"
            and _safe_count(data.get("returned_tool_calls")) > 0
        )
        event_tool_kinds = {
            kind: sum(1 for _, data in tool_pairs if data.get("tool_kind") == kind)
            for kind in ("native", "mcp", "sandbox", "approval")
        }
        memory_prepare = next(
            (data for event, data in zip(events, details)
             if event.get("kind") == "memory" and event.get("name") == "prepare"),
            None,
        )
        expected_memory_compacted = bool(
            memory_prepare and memory_prepare.get("compacted")
        )
        expected_memory_omitted = bool(
            memory_prepare and memory_prepare.get("omitted_earlier")
        ) or any(
            event.get("kind") == "memory" and event.get("name") == "request_window"
            and data.get("omitted_earlier")
            for event, data in zip(events, details)
        )
        expected_memory_boundary = (
            memory_prepare.get("through_message_id") if memory_prepare else None
        )

        classified_tool_count = sum(event_tool_kinds.values())
        counts_consistent = not malformed and (
            trace.get("model_call_count") == len(model_pairs)
            and trace.get("memory_model_call_count") == expected_memory_calls
            and trace.get("tool_call_count") == len(tool_pairs)
            and classified_tool_count == len(tool_pairs)
            and trace.get("tool_error_count") == expected_tool_errors
            and trace.get("tool_rounds") == expected_rounds
            and trace.get("prompt_tokens") == prompt_tokens
            and trace.get("completion_tokens") == completion_tokens
            and trace.get("total_tokens") == total_tokens
            and bool(trace.get("usage_complete")) == expected_usage_complete
            and bool(trace.get("memory_compacted")) == expected_memory_compacted
            and bool(trace.get("memory_omitted_earlier")) == expected_memory_omitted
            and trace.get("memory_through_message_id") == expected_memory_boundary
        )
        protocol_complete = agent_returned_calls == len(tool_pairs)
        turn_succeeded = trace.get("status") == "succeeded"
        final_assistant_persisted = trace.get("assistant_message_id") is not None
        model_call_recorded = len(model_pairs) > 0
        rounds_respected = (
            isinstance(trace.get("tool_rounds"), int)
            and not isinstance(trace.get("tool_rounds"), bool)
            and 0 <= trace["tool_rounds"] <= self.max_tool_rounds
        )
        no_tool_errors = expected_tool_errors == 0
        usage_complete = bool(trace.get("usage_complete"))
        checks = {
            "turn_succeeded": turn_succeeded,
            "final_assistant_persisted": final_assistant_persisted,
            "event_counts_consistent": counts_consistent,
            "tool_protocol_complete": protocol_complete,
            "tool_round_limit_respected": rounds_respected,
            "no_tool_execution_errors": no_tool_errors,
            "model_usage_complete": usage_complete,
            "model_call_recorded": model_call_recorded,
        }
        core_checks = (
            "turn_succeeded", "final_assistant_persisted", "event_counts_consistent",
            "tool_protocol_complete", "tool_round_limit_respected", "model_call_recorded",
        )
        if any(not checks[name] for name in core_checks):
            status = "fail"
        elif (not no_tool_errors or not usage_complete or any(
            event.get("kind") == "memory" and event.get("name") == "summary"
            and event.get("status") == "error" for event in events
        )):
            status = "warn"
        else:
            status = "pass"

        metrics = {
            "duration_ms": _safe_count(trace.get("duration_ms")),
            "model_call_count": _safe_count(trace.get("model_call_count")),
            "memory_model_call_count": _safe_count(trace.get("memory_model_call_count")),
            "tool_call_count": _safe_count(trace.get("tool_call_count")),
            "tool_error_count": _safe_count(trace.get("tool_error_count")),
            "prompt_tokens": _safe_count(trace.get("prompt_tokens")),
            "completion_tokens": _safe_count(trace.get("completion_tokens")),
            "total_tokens": _safe_count(trace.get("total_tokens")),
            "usage_complete": usage_complete,
            "memory_compacted": bool(trace.get("memory_compacted")),
            "memory_omitted_earlier": bool(trace.get("memory_omitted_earlier")),
            "native_tool_calls": event_tool_kinds["native"],
            "mcp_tool_calls": event_tool_kinds["mcp"],
            "sandbox_tool_calls": event_tool_kinds["sandbox"],
            "approval_tool_calls": event_tool_kinds["approval"],
        }
        return {
            "evaluator_version": self.version,
            "status": status,
            "checks": checks,
            "metrics": metrics,
        }


def _safe_count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
