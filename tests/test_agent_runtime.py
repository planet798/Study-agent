"""AgentRuntime 测试（Agent-1，no-tool）。

使用 FakeAgentModelClient：
- first turn：system + user → assistant；
- second turn：确认真 multi-turn；
- 模型失败：user 已保存、assistant 不写；
- blank input / closed session：不调用模型、不写库；
- session 隔离；
- 意外 tool_calls 一律 fail safe；
- 元数据不含 secret。
"""

from __future__ import annotations

import pytest

from app.agent.runtime import AgentRuntime, AgentRuntimeError
from app.agent.session import AgentSessionService, SessionClosedError
from app.ai.agent_protocol import (
    AgentModelClient,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from app.ai.interface import AIServiceError
from app.database.agent_repository import AgentRepository


class FakeAgentModelClient(AgentModelClient):
    """记录每次收到的 ModelRequest；响应可控。"""

    def __init__(self, responses=None, error: Exception | None = None):
        self.requests: list[ModelRequest] = []
        self._responses = list(responses or [])
        self._error = error
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse(content="默认回复", finish_reason="stop", model="fake")


@pytest.fixture()
def agent_repo(conn):
    return AgentRepository(conn)


@pytest.fixture()
def session_service(agent_repo, task_service):
    return AgentSessionService(agent_repo, task_service)


@pytest.fixture()
def runtime(session_service):
    client = FakeAgentModelClient()
    return AgentRuntime(session_service, client), client


@pytest.fixture()
def task(repo):
    return repo.create(title="学习 Transformer", scheduled_date="2026-09-15",
                       source="generated")


def test_first_turn_persists_user_and_assistant(runtime, session_service, task):
    rt, client = runtime
    session = session_service.start_or_resume(task.id)
    result = rt.send_message(session["id"], "帮我理解 self-attention")

    # model 收到 system + user
    request = client.requests[0]
    assert [m.role for m in request.messages] == ["system", "user"]
    assert "学习 Transformer" in request.messages[0].content
    assert request.messages[1].content == "帮我理解 self-attention"
    assert request.tools == ()

    # 只持久化 2 条业务 message（system 不落库）
    rows = session_service.messages(session["id"])
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "帮我理解 self-attention"),
        ("assistant", "默认回复"),
    ]
    assert result.user_message["role"] == "user"
    assert result.assistant_message["role"] == "assistant"
    assert result.session_id == session["id"]


def test_second_turn_sends_full_history(runtime, session_service, task):
    rt, client = runtime
    session = session_service.start_or_resume(task.id)
    rt.send_message(session["id"], "u1")
    rt.send_message(session["id"], "u2")

    second = client.requests[1]
    assert [m.role for m in second.messages] == [
        "system", "user", "assistant", "user"
    ]
    assert [m.content for m in second.messages[1:]] == [
        "u1", "默认回复", "u2"
    ]
    assert len(session_service.messages(session["id"])) == 4


def test_model_failure_keeps_user_message_and_skips_assistant(
    session_service, task
):
    client = FakeAgentModelClient(error=AIServiceError("网络错误"))
    rt = AgentRuntime(session_service, client)
    session = session_service.start_or_resume(task.id)

    with pytest.raises(AIServiceError):
        rt.send_message(session["id"], "会失败的一轮")

    rows = session_service.messages(session["id"])
    assert [(r["role"], r["content"]) for r in rows] == [("user", "会失败的一轮")]
    assert client.calls == 1  # 不自动重试


def test_blank_input_does_not_call_model_or_write_db(runtime, session_service, task):
    rt, client = runtime
    session = session_service.start_or_resume(task.id)
    for blank in ("", "   "):
        with pytest.raises(Exception):
            rt.send_message(session["id"], blank)
    assert client.calls == 0
    assert session_service.messages(session["id"]) == []


def test_closed_session_does_not_call_model(runtime, session_service, task):
    rt, client = runtime
    session = session_service.start_or_resume(task.id)
    session_service.close(session["id"])
    with pytest.raises(SessionClosedError):
        rt.send_message(session["id"], "hello")
    assert client.calls == 0
    assert session_service.messages(session["id"]) == []


def test_session_isolation_in_model_request(runtime, session_service, repo, task):
    rt, client = runtime
    other = repo.create(title="学习 KV Cache", scheduled_date="2026-09-15",
                        source="generated")
    a = session_service.start_or_resume(task.id)
    b = session_service.start_or_resume(other.id)
    rt.send_message(a["id"], "A 的内容")
    rt.send_message(b["id"], "B 的内容")

    a_texts = " ".join(m.content for m in client.requests[0].messages)
    b_texts = " ".join(m.content for m in client.requests[1].messages)
    assert "A 的内容" in a_texts and "B 的内容" not in a_texts
    assert "B 的内容" in b_texts and "A 的内容" not in b_texts
    assert "学习 KV Cache" in b_texts and "学习 KV Cache" not in a_texts


def test_unexpected_tool_calls_fail_safe(session_service, task):
    client = FakeAgentModelClient(responses=[ModelResponse(
        content="",
        tool_calls=(ModelToolCall(id="c1", name="do_thing", arguments="{}"),),
        finish_reason="tool_calls",
    )])
    rt = AgentRuntime(session_service, client)
    session = session_service.start_or_resume(task.id)

    with pytest.raises(AgentRuntimeError):
        rt.send_message(session["id"], "调用工具吧")

    # 不执行、不伪造 tool result；user message 保留，assistant 不写
    rows = session_service.messages(session["id"])
    assert [(r["role"], r["content"]) for r in rows] == [("user", "调用工具吧")]


def test_assistant_metadata_has_no_secret(runtime, session_service, task):
    rt, client = runtime
    session = session_service.start_or_resume(task.id)
    client._responses = [ModelResponse(
        content="ok", finish_reason="stop", model="deepseek-chat",
        usage={"prompt_tokens": 3, "completion_tokens": 4},
    )]
    rt.send_message(session["id"], "hi")
    assistant = session_service.messages(session["id"])[-1]
    metadata = assistant["metadata_json"]
    assert "deepseek-chat" in metadata
    assert "usage" in metadata
    for secret in ("api_key", "Authorization", "secret_ref", "sk-"):
        assert secret not in metadata
