"""In-memory collector ordering, timing, usage, and metadata bounds."""

from __future__ import annotations

import json

import pytest

from app.agent.trace.collector import AgentTraceCollector
from app.ai.agent_protocol import AgentModelClient, ModelMessage, ModelRequest, ModelResponse, ModelToolCall
from app.ai.interface import AIServiceError


class FakeClock:
    def __init__(self):
        self.ns = 0
        self.wall = 0

    def monotonic_ns(self):
        self.ns += 5_000_000
        return self.ns

    def wall_clock(self):
        self.wall += 1
        return f"2026-10-01T12:00:{self.wall:02d}"


class FakeModel(AgentModelClient):
    def __init__(self, response=None, error=None):
        self.response = response or ModelResponse(content="PRIVATE RESPONSE", model="fake-model")
        self.error = error

    def is_configured(self):
        return True

    def complete(self, request):
        if self.error is not None:
            raise self.error
        return self.response


def _collector(clock=None):
    clock = clock or FakeClock()
    collector = AgentTraceCollector(
        1, 2, 3, monotonic_ns=clock.monotonic_ns, wall_clock=clock.wall_clock
    )
    return collector, clock


def test_monotonic_timing_event_order_and_no_content_in_model_metadata():
    collector, _ = _collector()
    request = ModelRequest(
        messages=(ModelMessage("system", "SYSTEM_PRIVATE_SENTINEL"),
                  ModelMessage("user", "USER_PRIVATE_SENTINEL")),
        tools=({"name": "registered_tool", "description": "PRIVATE TOOL SCHEMA"},),
    )
    collector.record_event("memory", "prepare", "ok", 7, {
        "compacted": True, "omitted_earlier": False, "through_message_id": 55,
    })
    collector.complete_model(
        FakeModel(ModelResponse(
            content="ASSISTANT_PRIVATE_SENTINEL", finish_reason="stop",
            model="qwen-plus", usage={"prompt_tokens": 10, "completion_tokens": 4,
                                         "total_tokens": 14},
        )), request, purpose="agent",
    )
    trace, events = collector.finalize(status="succeeded", assistant_message_id=4)

    model_details = json.loads(events[1]["details_json"])
    assert [event["seq"] for event in events] == [1, 2]
    assert [event["kind"] for event in events] == ["memory", "model"]
    assert events[1]["duration_ms"] == 5
    assert model_details["request_message_count"] == 2
    assert model_details["tool_definition_count"] == 1
    assert model_details["response_chars"] == len("ASSISTANT_PRIVATE_SENTINEL")
    assert (trace["model_call_count"], trace["memory_model_call_count"]) == (1, 0)
    assert (trace["prompt_tokens"], trace["completion_tokens"], trace["total_tokens"]) == (
        10, 4, 14,
    )
    assert trace["usage_complete"] == 1
    encoded = json.dumps((trace, events))
    for secret in (
        "SYSTEM_PRIVATE_SENTINEL", "USER_PRIVATE_SENTINEL", "ASSISTANT_PRIVATE_SENTINEL",
        "PRIVATE TOOL SCHEMA",
    ):
        assert secret not in encoded


def test_usage_aliases_derive_total_and_memory_calls_are_in_total_subset():
    collector, _ = _collector()
    request = ModelRequest(messages=(ModelMessage("user", "private"),))
    collector.complete_model(
        FakeModel(ModelResponse(content="summary", model="qwen", usage={
            "input_tokens": 20, "output_tokens": 5,
        })), request, purpose="memory_summary",
    )
    collector.complete_model(
        FakeModel(ModelResponse(content="answer", model="qwen", usage={
            "prompt_tokens": 7, "completion_tokens": 3,
            "total_tokens": 10,
        })), request, purpose="agent",
    )
    trace, events = collector.finalize(status="succeeded", assistant_message_id=9)

    first, second = [json.loads(event["details_json"]) for event in events]
    assert first["total_tokens"] == 25 and first["usage_complete"] is True
    assert trace["model_call_count"] == 2
    assert trace["memory_model_call_count"] == 1
    assert (trace["prompt_tokens"], trace["completion_tokens"], trace["total_tokens"]) == (
        27, 8, 35,
    )
    assert second["purpose"] == "agent"


def test_missing_or_invalid_usage_marks_incomplete_but_keeps_known_tokens():
    for usage in (None, {"prompt_tokens": True, "completion_tokens": 6}):
        collector, _ = _collector()
        collector.complete_model(
            FakeModel(ModelResponse(content="answer", usage=usage)),
            ModelRequest(messages=(ModelMessage("user", "p"),)), purpose="agent",
        )
        trace, events = collector.finalize(status="succeeded", assistant_message_id=5)
        details = json.loads(events[0]["details_json"])
        assert details["usage_complete"] is False
        assert trace["usage_complete"] == 0
        if usage is not None:
            assert trace["completion_tokens"] == 6
            assert trace["prompt_tokens"] == 0


def test_agent_model_error_records_only_purpose_and_finite_error_code():
    collector, _ = _collector()
    with pytest.raises(AIServiceError):
        collector.complete_model(
            FakeModel(error=AIServiceError("SECRET URL and API key")),
            ModelRequest(messages=(ModelMessage("user", "PRIVATE"),)), purpose="agent",
        )
    trace, events = collector.finalize(
        status="failed", assistant_message_id=None, error_code="ai_service_error"
    )
    assert json.loads(events[0]["details_json"]) == {
        "purpose": "agent", "error_code": "ai_service_error",
    }
    assert events[0]["status"] == "error"
    assert trace["model_call_count"] == 1
    assert trace["usage_complete"] == 0
    assert "SECRET" not in json.dumps((trace, events))


def test_tool_events_record_kind_outcome_and_error_code_without_payload():
    collector, _ = _collector()
    collector.record_tool_call("mcp_docs_search", {"ok": True, "data": "MCP_SECRET"}, 3)
    collector.record_tool_call(
        "sandbox_run", {"ok": False, "error": {"code": "sandbox_tool_failed",
                                                  "message": "SANDBOX_OUTPUT_SECRET"}}, 4
    )
    trace, events = collector.finalize(status="failed", assistant_message_id=None)
    assert json.loads(events[0]["details_json"]) == {"tool_kind": "mcp", "ok": True}
    assert json.loads(events[1]["details_json"]) == {
        "tool_kind": "sandbox", "ok": False, "error_code": "sandbox_tool_failed",
    }
    assert trace["tool_call_count"] == 2 and trace["tool_error_count"] == 1
    assert "MCP_SECRET" not in json.dumps((trace, events))
    assert "SANDBOX_OUTPUT_SECRET" not in json.dumps((trace, events))


def test_details_are_allowlisted_and_bounded():
    collector, _ = _collector()
    with pytest.raises(ValueError, match="unsupported"):
        collector.record_event("runtime", "task_context", "ok", details={
            "available": True, "task_description": "DO_NOT_STORE",
        })
    with pytest.raises(ValueError, match="invalid"):
        collector.record_event("mcp", "discovery", "ok", details={
            "available_server_keys": [f"srv_{i}" for i in range(65)],
            "unavailable_server_keys": [], "approved_tool_count": 0,
        })
    assert collector._events == []
