"""Persistent Agent tool-loop tests: protocol, safety, bounds, and reconstruction."""

from __future__ import annotations

import json

import pytest

from app.agent.runtime import AgentRuntime, AgentRuntimeError
from app.agent.session import AgentSessionService
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA
from app.agent.tools.learning import build_learning_tool_registry
from app.agent.tools.registry import AgentToolRegistry
from app.ai.agent_protocol import AgentModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.database.agent_repository import AgentRepository
from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.database.topic_learning_repository import TopicLearningComponentRepository
from app.services.assessment_service import AssessmentService
from app.services.capability import EVIDENCE_TYPE_ASSESSMENT
from app.services.capability_service import CapabilityService
from app.services.learning_route_service import LearningRouteService
from app.services.learning_activity import ACTIVITY_THEORY
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.services.topic_learning_profile_service import TopicLearningProfileService


class FakeAgentModelClient(AgentModelClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def is_configured(self):
        return True

    def complete(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _UnusedAIClient:
    def is_configured(self):
        return False

    def chat(self, *args, **kwargs):
        raise AssertionError("tools must not call AI")


class ExplodingTool(AgentTool):
    @property
    def spec(self):
        return AgentToolSpec("explode", "throws", EMPTY_OBJECT_SCHEMA, True)

    def execute(self, context, arguments):
        raise RuntimeError("secret SQL traceback must not leak")


@pytest.fixture()
def agent_env(conn):
    task_repo = TaskRepository(conn)
    task_service = TaskService(task_repo)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    plan_repo = StudyPlanRepository(conn)
    plan_service = StudyPlanService(task_repo, plan_repo)
    component_repo = TopicLearningComponentRepository(conn)
    topic_service = TopicLearningProfileService(conn, component_repo)
    assessment_repo = AssessmentRepository(conn)
    assessment_service = AssessmentService(_UnusedAIClient(), assessment_repo)
    evidence_repo = CapabilityEvidenceRepository(conn)
    capability_service = CapabilityService(conn, evidence_repo)
    route = route_service.create_learning_route("Tool Runtime Route")
    plan = plan_repo.create_plan(
        "Tool Runtime Plan", "2026-01-01", "2027-01-01", route_id=route.id
    )
    phase = plan_repo.create_phase(
        plan.id, "Tool Runtime Phase", "2026-01-01", "2027-01-01"
    )
    topic = plan_repo.create_topic(phase.id, "Attention")
    component = component_repo.create(topic.id, ACTIVITY_THEORY)
    kp = assessment_repo.create_knowledge_point(
        "Attention KP", topic_id=topic.id, route_id=route.id
    )
    task_a = task_repo.create(
        title="Task A", scheduled_date="2026-09-15", source="generated",
        route_id=route.id, topic_id=topic.id, knowledge_point_id=kp["id"],
        component_id=component["id"], learning_activity_kind=ACTIVITY_THEORY,
    )
    registry = build_learning_tool_registry(
        task_service, route_service, plan_service, topic_service,
        assessment_service, capability_service,
    )
    session_service = AgentSessionService(AgentRepository(conn), task_service)
    return {
        "conn": conn, "task_repo": task_repo, "task_service": task_service,
        "assessment_repo": assessment_repo, "assessment_service": assessment_service,
        "evidence_repo": evidence_repo, "capability_service": capability_service,
        "route": route, "topic": topic, "component": component, "kp": kp,
        "task_a": task_a, "registry": registry,
        "session_service": session_service,
    }


def _call(name, call_id="call-1", arguments="{}"):
    return ModelToolCall(id=call_id, name=name, arguments=arguments)


def _session(env, task=None):
    task = task or env["task_a"]
    return env["session_service"].start_or_resume(task.id)


def _assistant_call(*calls, content=""):
    return ModelResponse(
        content=content, tool_calls=tuple(calls), finish_reason="tool_calls",
        model="fake-model", usage={"completion_tokens": 2},
    )


def _final(content="done"):
    return ModelResponse(content=content, finish_reason="stop", model="fake-model")


def test_one_tool_call_is_persisted_executed_and_returned_to_model(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context")),
        _final("Task A is about Attention."),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])

    result = runtime.send_message(session["id"], "What am I studying?")

    assert result.tool_rounds == 1
    assert len(result.tool_messages) == 1
    first, second = client.requests
    assert len(first.tools) == 6
    assert [m.role for m in first.messages] == ["system", "user"]
    assert [m.role for m in second.messages] == [
        "system", "user", "assistant", "tool",
    ]
    assistant_call = second.messages[2]
    assert assistant_call.tool_calls == (_call("get_task_context"),)
    tool_result = json.loads(second.messages[3].content)
    assert tool_result["ok"] is True
    assert tool_result["data"]["task_id"] == env["task_a"].id
    assert second.messages[3].tool_call_id == "call-1"
    assert second.messages[3].name == "get_task_context"

    stored = env["session_service"].messages(session["id"])
    assert [m["role"] for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert json.loads(stored[1]["tool_calls_json"]) == [{
        "id": "call-1", "name": "get_task_context", "arguments": "{}",
    }]
    assert stored[2]["tool_call_id"] == "call-1"
    assert stored[2]["tool_name"] == "get_task_context"
    assert json.loads(stored[2]["content"])["ok"] is True
    assert result.assistant_message["content"] == "Task A is about Attention."


def test_multiple_tool_calls_execute_in_model_order(agent_env):
    env = agent_env
    session = _session(env)
    env["assessment_repo"].update_knowledge_point(
        env["kp"]["id"], last_assessed_at="2026-09-15T12:00:00",
        mastery_estimate=0.6,
    )
    env["evidence_repo"].create_or_update_by_key(
        knowledge_point_id=env["kp"]["id"], capability_level=1,
        evidence_type=EVIDENCE_TYPE_ASSESSMENT, evidence_key="runtime-test-cap",
    )
    client = FakeAgentModelClient([_assistant_call(
        _call("get_task_context", "id-task"),
        _call("get_mastery", "id-mastery"),
        _call("get_capability", "id-capability"),
    ), _final("Context loaded")])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])

    result = runtime.send_message(session["id"], "load context")
    stored = env["session_service"].messages(session["id"])
    tool_rows = [m for m in stored if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_rows] == [
        "id-task", "id-mastery", "id-capability",
    ]
    assert [m["tool_name"] for m in tool_rows] == [
        "get_task_context", "get_mastery", "get_capability",
    ]
    assert [json.loads(m["content"])["ok"] for m in tool_rows] == [True, True, True]
    assert result.tool_rounds == 1 and len(result.tool_messages) == 3
    assert [m.role for m in client.requests[1].messages] == [
        "system", "user", "assistant", "tool", "tool", "tool",
    ]


def test_invalid_extra_id_argument_is_not_executed_and_error_is_persisted(agent_env):
    env = agent_env
    session = _session(env)
    task_b = env["task_repo"].create(
        title="Secret Task B", scheduled_date="2026-09-15", source="generated"
    )
    client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context", arguments=json.dumps({"task_id": task_b.id}))),
        _final("I cannot read another task."),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])

    runtime.send_message(session["id"], "Read task B")
    tool_row = env["session_service"].messages(session["id"])[2]
    result = json.loads(tool_row["content"])
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert "Secret Task B" not in tool_row["content"]
    assert task_b.id != env["task_a"].id


def test_malformed_json_and_non_object_arguments_become_tool_results(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([
        _assistant_call(
            _call("get_task_context", "bad-json", "not json"),
            _call("get_mastery", "array-args", "[]"),
        ),
        _final(),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])

    runtime.send_message(session["id"], "read")
    rows = [m for m in env["session_service"].messages(session["id"]) if m["role"] == "tool"]
    assert [json.loads(m["content"])["error"]["code"] for m in rows] == [
        "invalid_arguments", "invalid_arguments",
    ]


def test_unknown_tool_returns_controlled_result_then_model_can_continue(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([
        _assistant_call(_call("unknown_tool")),
        _final("That tool is not available."),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])
    runtime.send_message(session["id"], "call unknown")
    tool = env["session_service"].messages(session["id"])[2]
    assert json.loads(tool["content"]) == {
        "ok": False,
        "error": {
            "code": "tool_not_found",
            "message": "Tool is not available: unknown_tool.",
        },
    }


def test_tool_handler_failure_is_sanitized_and_does_not_crash_turn(agent_env):
    env = agent_env
    session = _session(env)
    registry = AgentToolRegistry()
    registry.register(ExplodingTool())
    client = FakeAgentModelClient([
        _assistant_call(_call("explode")), _final("Recovered safely"),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=registry)
    runtime.send_message(session["id"], "explode")
    tool = env["session_service"].messages(session["id"])[2]
    assert json.loads(tool["content"]) == {
        "ok": False,
        "error": {
            "code": "tool_execution_failed",
            "message": "Tool execution failed.",
        },
    }
    assert "secret" not in tool["content"] and "traceback" not in tool["content"]


def test_tool_call_protocol_is_validated_before_execution(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([_assistant_call(_call("get_task_context", ""))])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])
    with pytest.raises(AgentRuntimeError, match="id is missing"):
        runtime.send_message(session["id"], "bad protocol")
    assert [m["role"] for m in env["session_service"].messages(session["id"])] == ["user"]


@pytest.mark.parametrize(
    "call",
    [
        ModelToolCall(id="", name="get_task_context", arguments="{}"),
        ModelToolCall(id="call", name="", arguments="{}"),
        ModelToolCall(id="call", name="get_task_context", arguments=123),
    ],
)
def test_malformed_tool_call_protocol_is_rejected_before_execution(agent_env, call):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([_assistant_call(call)])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])
    with pytest.raises(AgentRuntimeError):
        runtime.send_message(session["id"], "bad protocol")
    assert [m["role"] for m in env["session_service"].messages(session["id"])] == ["user"]


def test_maximum_tool_rounds_stops_unbounded_model_loop(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context", f"call-{i}"))
        for i in range(1, 4)
    ])
    runtime = AgentRuntime(
        env["session_service"], client, tool_registry=env["registry"],
        max_tool_rounds=2,
    )
    with pytest.raises(AgentRuntimeError, match=r"exceeded limit \(2\)"):
        runtime.send_message(session["id"], "keep calling tools")

    assert len(client.requests) == 3  # 2 executed rounds + one over-limit decision
    rows = env["session_service"].messages(session["id"])
    assert [m["role"] for m in rows] == ["user", "assistant", "tool", "assistant", "tool"]
    assert [m["tool_call_id"] for m in rows if m["role"] == "tool"] == [
        "call-1", "call-2",
    ]


def test_new_runtime_reconstructs_persisted_tool_history_on_later_user_turn(agent_env):
    env = agent_env
    session = _session(env)
    first_client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context")), _final("Loaded once"),
    ])
    first_runtime = AgentRuntime(
        env["session_service"], first_client, tool_registry=env["registry"]
    )
    first_runtime.send_message(session["id"], "first user turn")

    # New Runtime instance: no in-memory conversation cache is available.
    second_client = FakeAgentModelClient([_final("second answer")])
    second_runtime = AgentRuntime(
        env["session_service"], second_client, tool_registry=env["registry"]
    )
    second_runtime.send_message(session["id"], "second user turn")
    request = second_client.requests[0]
    assert [message.role for message in request.messages] == [
        "system", "user", "assistant", "tool", "assistant", "user",
    ]
    call_message, tool_message = request.messages[2:4]
    assert call_message.tool_calls == (_call("get_task_context"),)
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.name == "get_task_context"
    assert json.loads(tool_message.content)["data"]["title"] == "Task A"


def test_task_a_session_cannot_read_task_b_by_argument_and_other_session_is_isolated(agent_env):
    env = agent_env
    task_b = env["task_repo"].create(
        title="Task B private", scheduled_date="2026-09-15", source="generated"
    )
    session_a = _session(env, env["task_a"])
    session_b = _session(env, task_b)
    client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context", arguments='{"task_id":999}')),
        _final("A turn"),
        _assistant_call(_call("get_task_context", "call-b")),
        _final("B turn"),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])
    runtime.send_message(session_a["id"], "A asks")
    runtime.send_message(session_b["id"], "B asks")

    a_tool = env["session_service"].messages(session_a["id"])[2]
    b_tool = env["session_service"].messages(session_b["id"])[2]
    assert json.loads(a_tool["content"])["error"]["code"] == "invalid_arguments"
    assert json.loads(b_tool["content"])["data"]["title"] == "Task B private"
    assert all("Task B private" not in m.content for m in client.requests[0].messages)
    assert all("Task A" not in m.content for m in client.requests[2].messages)


@pytest.mark.migration
@pytest.mark.integration
def test_tool_conversation_is_legal_verifier_history_growth(agent_env):
    env = agent_env
    from app.diagnostics import release_migration as rm

    session = _session(env)
    before = rm.inventory(env["conn"])
    client = FakeAgentModelClient([
        _assistant_call(_call("get_task_context")), _final("Loaded"),
    ])
    runtime = AgentRuntime(env["session_service"], client, tool_registry=env["registry"])
    runtime.send_message(session["id"], "read task")

    result = rm.verify(env["conn"], before=before)
    assert result["ok"] is True, result
    assert result["history_new_rows"]["agent_messages"] >= 4


def test_tool_registry_none_preserves_agent1_no_tool_behavior(agent_env):
    env = agent_env
    session = _session(env)
    client = FakeAgentModelClient([_final("no tools")])
    runtime = AgentRuntime(env["session_service"], client)
    result = runtime.send_message(session["id"], "hello")
    assert result.tool_rounds == 0
    assert result.tool_messages == ()
    assert client.requests[0].tools == ()
    assert "当前没有可用工具" in client.requests[0].messages[0].content
