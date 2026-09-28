"""Deterministic, metadata-only protocol evaluation rules."""

from __future__ import annotations

import json

from app.agent.eval.evaluator import AgentTurnEvaluator, EVALUATOR_VERSION


def _event(seq, kind, name, status, details):
    return {
        "seq": seq, "kind": kind, "name": name, "status": status,
        "duration_ms": 3,
        "details_json": json.dumps(details, separators=(",", ":")),
        "created_at": "2026-10-01T12:00:00",
    }


def _model(seq=1, *, purpose="agent", calls=0, usage=True, tokens=(10, 4, 14), status="ok"):
    prompt, completion, total = tokens
    details = {
        "purpose": purpose,
        "returned_tool_calls": calls,
        "usage_complete": usage,
    }
    if status == "ok":
        details.update({
            "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total,
        })
    else:
        details["error_code"] = "ai_service_error"
    return _event(seq, "model", "agent" if purpose == "agent" else "memory_summary",
                  status, details)


def _trace(events, *, status="succeeded", assistant=101, tool_rounds=0,
           model_count=None, memory_model_count=None, tool_count=0, tool_errors=0,
           usage_complete=True, tokens=(10, 4, 14)):
    models = [event for event in events if event["kind"] == "model"]
    return {
        "status": status, "assistant_message_id": assistant,
        "tool_rounds": tool_rounds,
        "model_call_count": len(models) if model_count is None else model_count,
        "memory_model_call_count": (
            sum(json.loads(event["details_json"]).get("purpose") == "memory_summary"
                for event in models)
            if memory_model_count is None else memory_model_count
        ),
        "tool_call_count": tool_count, "tool_error_count": tool_errors,
        "prompt_tokens": tokens[0], "completion_tokens": tokens[1],
        "total_tokens": tokens[2], "usage_complete": int(usage_complete),
        "duration_ms": 25, "memory_compacted": 0, "memory_omitted_earlier": 0,
    }


def test_clean_success_passes_without_numeric_quality_score():
    events = [_model()]
    result = AgentTurnEvaluator().evaluate(_trace(events), events)

    assert result["evaluator_version"] == EVALUATOR_VERSION == 1
    assert result["status"] == "pass"
    assert all(result["checks"][key] for key in (
        "turn_succeeded", "final_assistant_persisted", "event_counts_consistent",
        "tool_protocol_complete", "tool_round_limit_respected", "model_usage_complete",
    ))
    assert "score" not in result["metrics"]


def test_missing_usage_and_controlled_tool_error_warn_not_fail():
    no_usage = [_model(usage=False, tokens=(0, 0, 0))]
    usage_trace = _trace(no_usage, usage_complete=False, tokens=(0, 0, 0))
    assert AgentTurnEvaluator().evaluate(usage_trace, no_usage)["status"] == "warn"

    tool_events = [
        _model(calls=1),
        _event(2, "tool", "get_task_context", "error", {
            "tool_kind": "native", "ok": False, "error_code": "tool_error",
        }),
    ]
    trace = _trace(tool_events, tool_rounds=1, tool_count=1, tool_errors=1)
    evaluation = AgentTurnEvaluator().evaluate(trace, tool_events)
    assert evaluation["status"] == "warn"
    assert evaluation["checks"]["tool_protocol_complete"] is True
    assert evaluation["checks"]["no_tool_execution_errors"] is False


def test_failed_turn_and_missing_final_assistant_fail():
    failed_events = [_model(status="error", usage=False, tokens=(0, 0, 0))]
    failed = _trace(
        failed_events, status="failed", assistant=None, usage_complete=False,
        tokens=(0, 0, 0),
    )
    evaluation = AgentTurnEvaluator().evaluate(failed, failed_events)
    assert evaluation["status"] == "fail"
    assert evaluation["checks"]["turn_succeeded"] is False
    assert evaluation["checks"]["final_assistant_persisted"] is False

    events = [_model()]
    missing_final = _trace(events, assistant=None)
    assert AgentTurnEvaluator().evaluate(missing_final, events)["status"] == "fail"


def test_counter_and_tool_protocol_mismatches_fail():
    mismatch_events = [_model(calls=2), _event(2, "tool", "echo", "ok", {
        "tool_kind": "native", "ok": True,
    })]
    mismatch = _trace(mismatch_events, tool_rounds=1, tool_count=2)
    result = AgentTurnEvaluator().evaluate(mismatch, mismatch_events)
    assert result["status"] == "fail"
    assert result["checks"]["event_counts_consistent"] is False
    assert result["checks"]["tool_protocol_complete"] is False

    consistent_events = [_model(calls=1), _event(2, "tool", "echo", "ok", {
        "tool_kind": "native", "ok": True,
    })]
    inconsistent_summary = _trace(
        consistent_events, tool_rounds=1, tool_count=1, model_count=2
    )
    assert AgentTurnEvaluator().evaluate(
        inconsistent_summary, consistent_events
    )["status"] == "fail"


def test_round_limit_is_supplied_by_runtime_configuration():
    events = [
        _model(1, calls=1),
        _event(2, "tool", "echo", "ok", {"tool_kind": "native", "ok": True}),
        _model(3, calls=1),
        _event(4, "tool", "echo", "ok", {"tool_kind": "native", "ok": True}),
        _model(5, calls=0),
    ]
    trace = _trace(events, tool_rounds=2, tool_count=2, tokens=(30, 12, 42))
    assert AgentTurnEvaluator(max_tool_rounds=1).evaluate(trace, events)["status"] == "fail"
    assert AgentTurnEvaluator(max_tool_rounds=2).evaluate(trace, events)["status"] == "pass"


def test_evaluator_needs_a_model_event_for_a_succeeded_turn():
    trace = _trace([], model_count=0)
    result = AgentTurnEvaluator().evaluate(trace, [])
    assert result["status"] == "fail"
    assert result["checks"]["model_call_recorded"] is False
