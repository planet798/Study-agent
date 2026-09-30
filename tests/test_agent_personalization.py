"""P-1D deterministic advisory context, runtime separation and worker wiring."""

import json
import logging
import sqlite3
import threading

import pytest

from app.agent.personalization_context import AgentPersonalizationContextBuilder
from app.agent.runtime import AGENT_SYSTEM_PROMPT, AgentRuntime
from app.agent.session import AgentSessionService
from app.agent.skills.base import AgentSkill
from app.agent.skills import AgentSkillSelector, build_default_agent_skill_registry
from app.agent.memory.compactor import ConversationWindow
from app.agent.tools.learning import GetTaskContextTool
from app.agent.tools.registry import AgentToolRegistry
from app.ai.agent_protocol import ModelResponse, ModelToolCall
from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.personalization_repository import PersonalizationRepository
from app.services.personalization_service import PersonalizationService


class Model:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def is_configured(self):
        return True

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else ModelResponse('Answer')


@pytest.fixture
def env(conn, repo, task_service):
    task = repo.create(title='Personalization task', scheduled_date='2026-01-01')
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    service = PersonalizationService(PersonalizationRepository(conn))
    builder = AgentPersonalizationContextBuilder(service)
    model = Model()
    runtime = AgentRuntime(sessions, model, personalization_context_builder=builder)
    return conn, task, sessions, session, service, builder, model, runtime


def snapshots(conn):
    return {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')]
            for t in ('agent_personalization_settings', 'agent_personal_memories', 'prompt_overrides')}


def test_optional_dependency_and_default_settings_are_byte_compatible(env):
    _, _, sessions, session, _, builder, _, runtime = env
    old = AgentRuntime(sessions, Model())
    assert old.personalization_context_builder is None
    assert builder.build() is None
    assert runtime.build_system_message(session) == old.build_system_message(session)
    assert runtime.build_system_message(session).content.startswith(AGENT_SYSTEM_PROMPT)
    assert 'PERSONALIZATION' not in runtime.build_system_message(session).content
    assert runtime.build_request(session) == old.build_request(session)


@pytest.mark.parametrize('text', ['数据结构示例默认使用 C++。', '先讲直觉\n再解释原理\n\t{{literal}}'])
def test_instructions_only_preserves_authored_content(env, text):
    _, _, _, session, service, builder, _, runtime = env
    service.set_instructions(text)
    context = builder.build()
    assert '### Personal Instructions\n' + text in context
    assert '### Personal Memories' not in context
    assert context in runtime.build_system_message(session).content
    service.set_instructions('')
    assert builder.build() is None


@pytest.mark.parametrize('auto_enabled', [False, True])
def test_master_off_skips_memory_read_and_injection(env, monkeypatch, auto_enabled):
    conn, _, _, session, service, builder, _, runtime = env
    service.add_manual_memory('ENABLED_BUT_MASTER_OFF')
    service.set_auto_memory_enabled(auto_enabled)
    before = snapshots(conn)

    def forbidden(*args, **kwargs):
        pytest.fail('master off must not list or extract memories')

    monkeypatch.setattr(service, 'list_memories', forbidden)
    assert builder.build() is None
    runtime.send_message(session['id'], 'Hello')
    assert snapshots(conn) == before


@pytest.mark.parametrize('auto_enabled', [False, True])
def test_master_on_enabled_disabled_mixed_order_and_auto_independence(env, auto_enabled):
    conn, _, _, session, service, builder, model, runtime = env
    first = service.add_manual_memory('FIRST preference')
    disabled = service.add_manual_memory('DISABLED preference')
    third = service.add_manual_memory('THIRD preference\ncontinuation')
    service.disable_memory(disabled['id'])
    service.set_memory_enabled(True)
    original = builder.build()
    service.set_auto_memory_enabled(auto_enabled)
    before = snapshots(conn)
    changes = conn.total_changes
    assert builder.build() == original
    assert conn.total_changes == changes
    assert original.index('FIRST preference') < original.index('THIRD preference')
    assert '- THIRD preference\n  continuation' in original
    assert 'DISABLED preference' not in original
    assert '### Personal Instructions' not in original
    runtime.send_message(session['id'], 'Read preferences')
    assert original in model.requests[0].messages[0].content
    assert snapshots(conn) == before
    assert [m['id'] for m in service.list_memories()] == [first['id'], disabled['id'], third['id']]


def test_all_enabled_memories_in_id_order_without_hidden_budget(env):
    _, _, _, _, service, builder, _, _ = env
    service.set_memory_enabled(True)
    contents = [f'Preference-{i:03d}:' + 'x' * 980 for i in range(40)]
    for content in contents:
        service.add_manual_memory(content)
    context = builder.build()
    for content in contents:
        assert content in context
    positions = [context.index(content) for content in contents]
    assert positions == sorted(positions)


def test_builder_works_on_sqlite_read_only_connection(env):
    conn, _, _, _, service, _, _, _ = env
    service.set_instructions('Read-only instructions')
    service.set_memory_enabled(True)
    service.add_manual_memory('Read-only memory')
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    read_only = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        builder = AgentPersonalizationContextBuilder(
            PersonalizationService(PersonalizationRepository(read_only))
        )
        context = builder.build()
        assert 'Read-only instructions' in context and 'Read-only memory' in context
        assert read_only.total_changes == 0
    finally:
        read_only.close()


def test_only_disabled_memories_produce_no_section(env):
    _, _, _, session, service, builder, _, runtime = env
    memory = service.add_manual_memory('DISABLED_ONLY')
    service.disable_memory(memory['id'])
    service.set_memory_enabled(True)
    assert builder.build() is None
    assert 'BEGIN_PERSONALIZATION' not in runtime.build_system_message(session).content


def test_session_source_only_content_no_provenance_and_read_only(env):
    conn, _, sessions, session, service, builder, _, runtime = env
    message = sessions.append_user_message(session['id'], 'Raw conversation MUST_NOT_INJECT')
    memory = service.add_session_memory('Distilled preference', session['id'], message['id'])
    service.set_memory_enabled(True)
    before = snapshots(conn)
    changes = conn.total_changes
    context = builder.build()
    assert 'Distilled preference' in context
    for field in ('source_session_id', 'source_message_id', 'created_at', 'updated_at', 'Raw conversation'):
        assert field not in context
    assert 'session' not in context
    assert conn.total_changes == changes
    assert snapshots(conn) == before
    assert service.list_memories()[0] == memory
    assert context in runtime.build_system_message(session).content


def test_combined_layers_and_current_request_precedence(env):
    _, _, sessions, session, service, builder, model, runtime = env
    service.set_instructions('数据结构示例默认使用 C++。')
    service.add_manual_memory('长期背景：初学者')
    service.set_memory_enabled(True)
    task_context = {'task': {'title': 'Current task'}}
    skill = AgentSkill('teach-concept', 'Teach', 'Description', 'Teaching strategy')
    content = runtime.build_system_message(
        session, task_context=task_context, agent_skill=skill, session_memory='Old conversation summary',
    ).content
    markers = ['BEGIN_AGENT_SKILL', 'BEGIN_PERSONALIZATION', 'BEGIN_TASK_CONTEXT_JSON', 'BEGIN_SESSION_MEMORY']
    assert [content.index(marker) for marker in markers] == sorted(content.index(marker) for marker in markers)
    assert content.index('### Personal Instructions') < content.index('### Personal Memories')
    assert '长期偏好和背景信息' in content
    assert '以本轮明确请求为准' in content
    assert '不是安全策略或不可覆盖的系统规则' in content
    runtime.send_message(session['id'], '这次请用 Python。')
    request = model.requests[0]
    assert request.messages[-1].role == 'user'
    assert request.messages[-1].content == '这次请用 Python。'
    assert '默认使用 C++' in request.messages[0].content
    assert '以本轮明确请求为准' in request.messages[0].content
    assert sessions.messages(session['id'])[0]['content'] == '这次请用 Python。'


@pytest.mark.parametrize('failed_api', ['get_settings', 'list_memories'])
@pytest.mark.parametrize('error', [sqlite3.OperationalError('SECRET /private/db.sqlite'),
                                  RuntimeError('PROGRAM_BUG private details')])
def test_read_failure_is_safe_observable_and_turn_continues(env, monkeypatch, caplog, failed_api, error):
    _, _, _, session, service, _, model, runtime = env
    service.set_instructions('Preference omitted atomically on failure')
    service.set_memory_enabled(True)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(service, failed_api, fail)
    with caplog.at_level(logging.WARNING, logger='app.agent.runtime'):
        result = runtime.send_message(session['id'], 'Continue')
    assert result.assistant_message['content'] == 'Answer'
    system = model.requests[0].messages[0].content
    assert 'BEGIN_PERSONALIZATION' not in system
    assert 'SECRET' not in system and 'PROGRAM_BUG' not in system
    assert 'context unavailable' in caplog.text
    assert type(error).__name__ in caplog.text
    assert str(error) not in caplog.text


def test_cross_session_independent_of_session_memory(env, repo):
    conn, _, sessions, first, service, builder, model, runtime = env
    other = repo.create(title='Other task', scheduled_date='2026-01-01')
    second = sessions.start_or_resume(other.id)
    old = sessions.append_user_message(first['id'], 'Old question')
    compact = AgentMemoryRepository(conn)
    original = compact.upsert(first['id'], old['id'], 1, 'First Session only')
    service.set_instructions('Shared preference')
    service.add_session_memory('Shared distilled content', first['id'], old['id'])
    service.set_memory_enabled(True)
    context = builder.build()
    for session in (first, second):
        runtime.send_message(session['id'], 'Question')
        assert context in model.requests[-1].messages[0].content
    assert compact.get_for_session(first['id']) == original
    assert compact.get_for_session(second['id']) is None


def test_snapshot_stable_in_tool_loop_next_turn_reloads(env, task_service, monkeypatch):
    _, _, _, session, service, builder, model, runtime = env
    service.set_instructions('First preference')
    registry = AgentToolRegistry()
    registry.register(GetTaskContextTool(task_service))
    runtime.tool_registry = registry
    calls = []
    original = builder.build

    def build():
        calls.append(True)
        return original()

    monkeypatch.setattr(builder, 'build', build)
    model.responses = [
        ModelResponse('', tool_calls=(ModelToolCall('call-1', 'get_task_context', '{}'),), finish_reason='tool_calls'),
        ModelResponse('Finished tool turn'), ModelResponse('Next turn'),
    ]
    original_complete = model.complete

    def complete(request):
        response = original_complete(request)
        if len(model.requests) == 1:
            service.set_instructions('Next preference')  # simulate Settings change mid-turn
        return response

    model.complete = complete
    runtime.send_message(session['id'], 'First turn')
    assert len(calls) == 1
    assert model.requests[0].messages[0] == model.requests[1].messages[0]
    assert 'First preference' in model.requests[1].messages[0].content
    runtime.send_message(session['id'], 'Second turn')
    assert len(calls) == 2
    assert 'Next preference' in model.requests[-1].messages[0].content
    assert 'First preference' not in model.requests[-1].messages[0].content


def test_real_task_context_skill_and_session_window_unchanged(env, task_service):
    from app.agent.context import AgentTaskContextBuilder
    _, _, sessions, session, service, _, model, runtime = env
    service.set_instructions('Personal guidance')
    registry = AgentToolRegistry()
    registry.register(GetTaskContextTool(task_service))
    runtime.context_builder = AgentTaskContextBuilder(registry)
    runtime.skill_selector = AgentSkillSelector(build_default_agent_skill_registry())
    earlier = sessions.append_user_message(session['id'], 'Earlier question')
    sessions.append_assistant_message(session['id'], 'Earlier answer')

    class Window:
        def prepare_turn(self, sid, current_id):
            assert sid == session['id']
            return ConversationWindow(summary='Existing compaction', through_message_id=earlier['id'])

    runtime.memory_compactor = Window()
    runtime.send_message(session['id'], 'Current question')
    content = model.requests[0].messages[0].content
    markers = ['BEGIN_AGENT_SKILL', 'BEGIN_PERSONALIZATION', 'BEGIN_TASK_CONTEXT_JSON', 'BEGIN_SESSION_MEMORY']
    assert all(marker in content for marker in markers)
    assert [content.index(m) for m in markers] == sorted(content.index(m) for m in markers)
    context = json.loads(content.split('BEGIN_TASK_CONTEXT_JSON\n')[1].split('\nEND_TASK_CONTEXT_JSON')[0])
    assert context['task']['title'] == 'Personalization task'
    summary = json.loads(content.split('BEGIN_SESSION_MEMORY\n')[1].split('\nEND_SESSION_MEMORY')[0])
    assert summary == {'summary': 'Existing compaction'}
    assert model.requests[0].messages[-1].content == 'Current question'


def test_real_compaction_model_does_not_receive_personalization(env):
    from app.agent.memory.compactor import AgentMemoryCompactor, SUMMARY_SYSTEM_PROMPT
    from app.agent.memory.policy import AgentMemoryPolicy
    conn, _, sessions, session, service, _, model, runtime = env
    service.set_instructions('PRIVATE_LONG_TERM_GUIDANCE')
    service.add_manual_memory('PRIVATE_DISTILLED_MEMORY')
    service.set_memory_enabled(True)
    before = snapshots(conn)
    for i in range(8):
        sessions.append_user_message(session['id'], f'Old question {i}: ' + 'q' * 200)
        sessions.append_assistant_message(session['id'], 'a' * 200)
    policy = AgentMemoryPolicy(
        history_budget_chars=5000, compaction_trigger_chars=3000, target_tail_chars=1000,
        keep_recent_turns=1, summary_input_max_chars=4000, summary_max_chars=500,
    )
    runtime.memory_compactor = AgentMemoryCompactor(
        sessions, AgentMemoryRepository(conn), model, policy,
    )
    def complete(request):
        model.requests.append(request)
        if request.messages[0].content == SUMMARY_SYSTEM_PROMPT:
            return ModelResponse('Actual compaction summary')
        return ModelResponse('Normal answer')

    model.complete = complete
    result = runtime.send_message(session['id'], 'Current question')
    assert result.memory_compacted
    assert len(model.requests) >= 2  # existing policy may require multiple compaction passes
    normal_request = model.requests[-1]
    for summary_request in model.requests[:-1]:
        assert summary_request.messages[0].content == SUMMARY_SYSTEM_PROMPT
        for message in summary_request.messages:
            assert 'PRIVATE_LONG_TERM_GUIDANCE' not in message.content
            assert 'PRIVATE_DISTILLED_MEMORY' not in message.content
    assert 'PRIVATE_LONG_TERM_GUIDANCE' in normal_request.messages[0].content
    assert 'PRIVATE_DISTILLED_MEMORY' in normal_request.messages[0].content
    assert 'Actual compaction summary' in normal_request.messages[0].content
    assert snapshots(conn) == before


@pytest.mark.ui
@pytest.mark.threaded
def test_production_factory_uses_worker_connection_not_settings_service(qtbot, env, tmp_path):
    from app.main import build_agent_runtime
    from app.ui.ai_worker import AgentTurnWorker
    conn, _, _, session, main_service, _, _, _ = env
    main_service.set_instructions('Worker-visible preference')
    main_service.add_manual_memory('Worker-visible memory')
    main_service.set_memory_enabled(True)
    before = snapshots(conn)
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    mcp = tmp_path / 'mcp.json'
    mcp.write_text('{"version":1,"servers":[]}', encoding='utf-8')
    sandbox = tmp_path / 'sandbox.json'
    sandbox.write_text('{"version":1,"enabled":false}', encoding='utf-8')
    records = []

    def factory(fresh_conn):
        runtime = build_agent_runtime(fresh_conn, db_path=path, mcp_config_path=mcp, sandbox_config_path=sandbox)
        worker_service = runtime.personalization_context_builder.service
        assert worker_service is not main_service
        assert worker_service.repository.conn is fresh_conn
        assert fresh_conn is not conn
        runtime.model_client = Model()
        records.append((threading.get_ident(), runtime, fresh_conn))
        return runtime

    worker = AgentTurnWorker(path, factory, session['id'], '这次请用 Python。')
    with qtbot.waitSignal(worker.succeeded, timeout=7000) as signal:
        worker.start()
    assert worker.wait(7000)
    assert signal.args[0].assistant_message['content'] == 'Answer'
    thread, runtime, fresh_conn = records[0]
    assert thread != threading.get_ident()
    content = runtime.model_client.requests[0].messages[0].content
    assert 'Worker-visible preference' in content and 'Worker-visible memory' in content
    assert runtime.model_client.requests[0].messages[-1].content == '这次请用 Python。'
    assert snapshots(conn) == before
    with pytest.raises(sqlite3.ProgrammingError):
        fresh_conn.execute('SELECT 1')
