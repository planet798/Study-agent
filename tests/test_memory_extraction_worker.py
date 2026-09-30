"""E-B incremental success events, hard consent gates and bounded QThread lifecycle."""

import json
import logging
import sqlite3
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from app.agent.memory_extraction import (
    CompletedMemoryExtractionTurn, MemoryExtractionService, consent_allowed,
)
from app.agent.personal_memory_extractor import PersonalMemoryExtractor
from app.agent.runtime import AgentRuntime
from app.agent.session import AgentSessionService
from app.ai.agent_protocol import ModelResponse
from app.database.agent_repository import AgentRepository
from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.personalization_repository import PersonalizationRepository
from app.services.personalization_service import PersonalizationService
from app.ui.ai_worker import AgentTurnWorker
from app.ui.memory_extraction_worker import MemoryExtractionCoordinator, MemoryExtractionWorker

pytestmark = [pytest.mark.ui, pytest.mark.threaded]
SOURCE = '以后数据结构示例默认使用 C++。'


class Model:
    def __init__(self, *, block=False, raw=None, error=None, configured=True):
        self.requests = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = block
        self.error = error
        self.configured = configured
        self.raw = raw if raw is not None else json.dumps([dict(
            content=SOURCE, kind='long_term_preference', evidence=SOURCE, reason='长期偏好。',
        )], ensure_ascii=False)

    def is_configured(self):
        return self.configured

    def complete(self, request):
        self.requests.append(request)
        self.entered.set()
        if self.block:
            assert self.release.wait(3), 'test release timeout'
        if self.error:
            raise self.error
        return ModelResponse(self.raw)


@pytest.fixture
def env(conn, repo, task_service):
    task = repo.create(title='TASK_SENTINEL', scheduled_date='2026-01-01')
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    session = sessions.start_or_resume(task.id)
    user = sessions.append_user_message(session['id'], SOURCE)
    assistant = sessions.append_assistant_message(session['id'], 'ASSISTANT_SENTINEL')
    settings = PersonalizationService(PersonalizationRepository(conn))
    path = conn.execute('PRAGMA database_list').fetchone()[2]
    return conn, path, sessions, session, user, assistant, settings


def enable(env, master=True, auto=True):
    env[-1].set_memory_enabled(master)
    env[-1].set_auto_memory_enabled(auto)


def event(env, consent=True):
    return CompletedMemoryExtractionTurn(env[3]['id'], env[4]['id'], env[5]['id'], consent)


def factory(model, captures=None):
    def build(fresh):
        from app.database.repository import TaskRepository
        from app.services.task_service import TaskService
        if captures is not None:
            captures.append((fresh, threading.get_ident()))
        return MemoryExtractionService(
            PersonalizationService(PersonalizationRepository(fresh)),
            AgentSessionService(AgentRepository(fresh), TaskService(TaskRepository(fresh))),
            PersonalMemoryExtractor(model),
        )
    return build


def run_worker(qtbot, worker):
    batches = []
    worker.candidates_ready.connect(batches.append)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        worker.start()
    assert worker.wait(1000)
    return batches


def wait_idle(qtbot, coordinator):
    qtbot.waitUntil(lambda: not coordinator.is_running, timeout=5000)


@pytest.mark.parametrize(('master', 'auto'), [(False, False), (True, False), (False, True), (True, True)])
def test_all_consent_combinations_and_no_settings_backfill(qtbot, env, master, auto):
    enable(env, master, auto)
    model = Model()
    c = MemoryExtractionCoordinator(env[1], factory(model))
    assert not c.is_running and c.pending_count == 0  # creation/toggle never reads old turns
    accepted = c.enqueue(event(env, master and auto))
    assert accepted is (master and auto)
    wait_idle(qtbot, c)
    assert len(model.requests) == len(c.candidate_batches) == int(master and auto)
    c.shutdown()


def test_no_start_consent_not_retroactive(qtbot, env):
    enable(env)
    model = Model()
    c = MemoryExtractionCoordinator(env[1], factory(model))
    assert not c.enqueue(event(env, False))
    assert model.requests == []
    c.shutdown()


def test_worker_rechecks_before_factory_or_model(qtbot, env):
    enable(env)
    turn = event(env)
    env[-1].set_memory_enabled(False)  # stored auto remains true
    model = Model()
    captures = []
    worker = MemoryExtractionWorker(env[1], factory(model, captures), turn)
    assert run_worker(qtbot, worker) == []
    assert captures == [] and model.requests == []


def test_revocation_during_model_blocks_results(qtbot, env):
    enable(env)
    model = Model(block=True)
    c = MemoryExtractionCoordinator(env[1], factory(model))
    published = []
    c.candidates_ready.connect(published.append)
    try:
        assert c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        env[-1].set_auto_memory_enabled(False)
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert published == [] and list(c.candidate_batches) == []
    c.shutdown()


def test_publication_rechecks_queued_result_consent(qtbot, env):
    enable(env)
    model = Model()
    c = MemoryExtractionCoordinator(env[1], factory(model))
    assert c.enqueue(event(env))
    # Finish worker without pumping GUI events, then revoke before queued publish.
    assert c._active.wait(3000)
    env[-1].set_memory_enabled(False)
    wait_idle(qtbot, c)
    assert len(model.requests) == 1 and not c.candidate_batches
    c.shutdown()


@pytest.mark.parametrize('stage', ['before_worker', 'during_model', 'queued_publish'])
def test_revocation_then_reenable_cannot_revive_old_job(qtbot, env, stage):
    enable(env)
    base = event(env)
    turn = CompletedMemoryExtractionTurn(base.session_id, base.user_message_id,
                                        base.assistant_message_id, True,
                                        env[-1].get_settings()['updated_at'])
    model = Model(block=stage == 'during_model')
    if stage == 'before_worker':
        env[-1].set_auto_memory_enabled(False)
        env[-1].set_auto_memory_enabled(True)
        worker = MemoryExtractionWorker(env[1], factory(model), turn)
        assert run_worker(qtbot, worker) == [] and model.requests == []
        return
    c = MemoryExtractionCoordinator(env[1], factory(model))
    try:
        assert c.enqueue(turn)
        if stage == 'during_model':
            qtbot.waitUntil(model.entered.is_set, timeout=3000)
        else:
            assert c._active.wait(3000)
        env[-1].set_auto_memory_enabled(False)
        env[-1].set_auto_memory_enabled(True)
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert len(model.requests) == 1 and not c.candidate_batches
    c.shutdown()


def test_settings_revision_change_conservatively_discards_inflight_results(qtbot, env):
    enable(env)
    model = Model(block=True)
    c = MemoryExtractionCoordinator(env[1], factory(model))
    try:
        c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        env[-1].set_instructions('Edited instructions')
        assert consent_allowed(env[-1])  # instructions still save/apply normally
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert not c.candidate_batches
    c.shutdown()


def test_valid_transient_batch_fresh_connection_closes_and_no_writes(qtbot, env, monkeypatch):
    conn, path, sessions, session, user, assistant, settings = env
    enable(env)
    AgentMemoryRepository(conn).upsert(session['id'], user['id'], 1, 'SUMMARY_SENTINEL')
    settings.add_manual_memory('MEMORY_SENTINEL')
    settings.set_instructions('INSTRUCTION_SENTINEL')
    tables = ['tasks', 'agent_sessions', 'agent_messages', 'agent_personal_memories',
              'agent_personalization_settings', 'agent_session_memory', 'prompt_overrides']
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
    model = Model()
    captures = []

    def forbidden(*args, **kwargs):
        raise AssertionError('no history/compaction read')

    monkeypatch.setattr(AgentRepository, 'list_messages', forbidden)
    monkeypatch.setattr(AgentRepository, 'list_messages_after', forbidden)
    monkeypatch.setattr(AgentMemoryRepository, 'get_for_session', forbidden)
    monkeypatch.setattr(PersonalizationRepository, 'list_memories', forbidden)
    from app.agent.context import AgentTaskContextBuilder
    monkeypatch.setattr(AgentTaskContextBuilder, 'build', forbidden)
    worker = MemoryExtractionWorker(path, factory(model, captures), event(env))
    batch, = run_worker(qtbot, worker)
    assert batch.session_id == session['id'] and batch.source_message_id == user['id']
    assert batch.candidates[0].content == SOURCE
    fresh, thread = captures[0]
    assert fresh is not conn and thread != threading.get_ident()
    with pytest.raises(sqlite3.ProgrammingError):
        fresh.execute('SELECT 1')
    assert json.loads(model.requests[0].messages[1].content) == {'user_message': SOURCE}
    for t in tables:
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] == before[t]


@pytest.mark.parametrize('invalid', ['missing_user', 'missing_assistant', 'foreign_user', 'foreign_assistant',
                                     'user_role', 'assistant_role', 'tool_call', 'later_turn', 'reversed'])
def test_source_validation_exact_successful_turn(qtbot, env, repo, invalid):
    enable(env)
    conn, path, sessions, session, user, assistant, _ = env
    uid, aid = user['id'], assistant['id']
    if invalid == 'missing_user': uid = 99999
    elif invalid == 'missing_assistant': aid = 99999
    elif invalid.startswith('foreign'):
        task = repo.create(title='Other', scheduled_date='2026-01-01')
        other = sessions.start_or_resume(task.id)
        foreign = (sessions.append_user_message(other['id'], SOURCE) if invalid == 'foreign_user'
                   else sessions.append_assistant_message(other['id'], 'Answer'))
        if invalid == 'foreign_user': uid = foreign['id']
        else: aid = foreign['id']
    elif invalid == 'user_role': uid = aid
    elif invalid == 'assistant_role': aid = uid
    elif invalid == 'tool_call':
        conn.execute("UPDATE agent_messages SET tool_calls_json='[{\"name\":\"tool\"}]' WHERE id=?", (aid,))
        conn.commit()
    elif invalid == 'later_turn':
        sessions.append_user_message(session['id'], 'Later question')
        aid = sessions.append_assistant_message(session['id'], 'Later answer')['id']
    elif invalid == 'reversed': uid, aid = aid, uid
    model = Model()
    worker = MemoryExtractionWorker(path, factory(model), CompletedMemoryExtractionTurn(session['id'], uid, aid, True))
    assert run_worker(qtbot, worker) == []
    assert model.requests == []


@pytest.mark.parametrize('outcome', ['model_error', 'timeout', 'malformed', 'empty', 'unconfigured', 'factory_error', 'settings_error', 'source_error'])
def test_optional_failures_silent_and_content_free(qtbot, env, outcome, caplog):
    enable(env)
    model = Model(raw='not JSON' if outcome == 'malformed' else ('[]' if outcome == 'empty' else None),
                  error=TimeoutError('SECRET DB /path') if outcome == 'timeout' else (
                      RuntimeError('SECRET DB /path') if outcome == 'model_error' else None),
                  configured=outcome != 'unconfigured')
    base = factory(model)
    captures = []

    def fail(*args):
        raise RuntimeError('SECRET DB /path')

    def build(fresh):
        captures.append(fresh)
        if outcome == 'factory_error': fail()
        service = base(fresh)
        if outcome == 'settings_error': service.personalization_service.get_settings = fail
        if outcome == 'source_error': service.session_service.completed_turn_user_message = fail
        return service

    with caplog.at_level(logging.WARNING):
        worker = MemoryExtractionWorker(env[1], build, event(env))
        assert run_worker(qtbot, worker) == []
    assert 'SECRET' not in caplog.text and '/path' not in caplog.text
    with pytest.raises(sqlite3.ProgrammingError):
        captures[0].execute('SELECT 1')


@pytest.mark.parametrize('outcome', ['success', 'model_error', 'factory_error'])
def test_connection_close_called_in_owning_thread_on_all_paths(qtbot, env, monkeypatch, outcome):
    import app.ui.memory_extraction_worker as mod
    enable(env)
    opened, closed = [], []

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed.append((self, threading.get_ident()))
            super().close()

    def open_read_only(path):
        fresh = sqlite3.connect(f'file:{path}?mode=ro', uri=True, factory=TrackedConnection)
        fresh.row_factory = sqlite3.Row
        fresh.execute('PRAGMA query_only=ON')
        opened.append((fresh, threading.get_ident()))
        return fresh

    model = Model(error=RuntimeError('SECRET') if outcome == 'model_error' else None)
    base = factory(model)

    def build(fresh):
        assert fresh.execute('PRAGMA query_only').fetchone()[0] == 1
        if outcome == 'factory_error':
            raise RuntimeError('SECRET')
        return base(fresh)

    monkeypatch.setattr(mod, 'open_extraction_connection', open_read_only)
    batches = run_worker(qtbot, MemoryExtractionWorker(env[1], build, event(env)))
    assert bool(batches) is (outcome == 'success')
    assert opened == closed and len(closed) == 1
    assert closed[0][1] != threading.get_ident()


def test_connection_failure_is_isolated(qtbot, env, monkeypatch, caplog):
    import app.ui.memory_extraction_worker as mod

    def fail(path):
        raise RuntimeError('SECRET DB /private/path')

    monkeypatch.setattr(mod, 'open_extraction_connection', fail)
    with caplog.at_level(logging.WARNING):
        assert run_worker(qtbot, MemoryExtractionWorker(env[1], factory(Model()), event(env))) == []
    assert 'worker_unavailable' in caplog.text and 'SECRET' not in caplog.text


def test_bounded_queue_duplicate_drop_no_retries(qtbot, env):
    enable(env)
    model = Model(block=True)
    c = MemoryExtractionCoordinator(env[1], factory(model), max_pending=2)
    sessions, sid = env[2], env[3]['id']
    turns = [event(env)]
    for _ in range(3):
        u = sessions.append_user_message(sid, SOURCE)
        a = sessions.append_assistant_message(sid, 'Answer')
        turns.append(CompletedMemoryExtractionTurn(sid, u['id'], a['id'], True))
    try:
        assert c.enqueue(turns[0])
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        assert not c.enqueue(turns[0])
        assert c.enqueue(turns[1]) and c.enqueue(turns[2])
        assert not c.enqueue(turns[3]) and c.pending_count == 2
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert len(model.requests) == 3
    assert not c.enqueue(turns[3])  # dropped events are not retried
    assert not c.enqueue(turns[0])
    c.shutdown()


def test_pending_job_consent_revoked_before_its_start(qtbot, env):
    enable(env)
    model = Model(block=True)
    c = MemoryExtractionCoordinator(env[1], factory(model))
    u = env[2].append_user_message(env[3]['id'], SOURCE)
    a = env[2].append_assistant_message(env[3]['id'], 'Answer')
    try:
        c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        c.enqueue(CompletedMemoryExtractionTurn(env[3]['id'], u['id'], a['id'], True))
        env[-1].set_memory_enabled(False)
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert len(model.requests) == 1 and not c.candidate_batches
    c.shutdown()


def test_shutdown_cancels_pending_and_running_without_waiting_gui(qtbot, env):
    enable(env)
    model = Model(block=True)
    c = MemoryExtractionCoordinator(env[1], factory(model))
    u = env[2].append_user_message(env[3]['id'], SOURCE)
    a = env[2].append_assistant_message(env[3]['id'], 'Answer')
    try:
        c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        c.enqueue(CompletedMemoryExtractionTurn(env[3]['id'], u['id'], a['id'], True))
        started = time.monotonic()
        assert c.shutdown() is False
        assert time.monotonic() - started < .2
        assert c.pending_count == 0 and c.is_running
        assert not c.enqueue(event(env))
        assert c._active.isInterruptionRequested()
    finally:
        model.release.set()
    with qtbot.waitSignal(c.drained, timeout=5000):
        pass
    assert not c.is_running and len(model.requests) == 1 and not c.candidate_batches


@pytest.mark.parametrize('start_allowed', [False, True])
def test_agent_turn_snapshot_and_answer_delivered_before_event(qtbot, env, start_allowed):
    enable(env, start_allowed, start_allowed)
    signals = []
    path, sid = env[1], env[3]['id']

    def runtime_factory(fresh):
        # Consent is captured before this factory/turn. Enable while the turn runs.
        if not start_allowed:
            svc = PersonalizationService(PersonalizationRepository(fresh))
            svc.set_memory_enabled(True)
            svc.set_auto_memory_enabled(True)
        return AgentRuntime(factory(Model())(fresh).session_service, Model(raw='Normal answer'))

    worker = AgentTurnWorker(path, runtime_factory, sid, SOURCE, capture_memory_consent=True)
    worker.succeeded.connect(lambda r: signals.append(('answer', r)))
    worker.extraction_requested.connect(lambda e: signals.append(('event', e)))
    with qtbot.waitSignal(worker.finished, timeout=5000): worker.start()
    assert worker.wait(1000)
    assert signals[0][0] == 'answer'
    assert [s[0] for s in signals] == (['answer', 'event'] if start_allowed else ['answer'])
    if start_allowed:
        turn = signals[1][1]
        assert turn.user_message_id == signals[0][1].user_message['id']
        assert turn.assistant_message_id == signals[0][1].assistant_message['id']


def test_failed_agent_turn_never_requests_extraction(qtbot, env):
    enable(env)
    requests = []

    def build(fresh):
        return AgentRuntime(factory(Model())(fresh).session_service, Model(error=RuntimeError('SECRET')))

    worker = AgentTurnWorker(env[1], build, env[3]['id'], SOURCE, capture_memory_consent=True)
    worker.extraction_requested.connect(requests.append)
    with qtbot.waitSignal(worker.failed, timeout=5000): worker.start()
    assert worker.wait(1000)
    assert requests == []


def test_start_settings_failure_does_not_fail_agent_turn(qtbot, env, monkeypatch):
    def fail(self): raise RuntimeError('SECRET')
    monkeypatch.setattr(PersonalizationService, 'get_settings', fail)
    worker = AgentTurnWorker(env[1], lambda fresh: AgentRuntime(
        factory(Model())(fresh).session_service, Model(raw='Answer'),
    ), env[3]['id'], SOURCE, capture_memory_consent=True)
    requests = []
    worker.extraction_requested.connect(requests.append)
    with qtbot.waitSignal(worker.succeeded, timeout=5000): worker.start()
    assert worker.wait(1000)
    assert not worker.consent_at_turn_start and requests == []


def test_production_model_and_service_own_fresh_dependencies(env):
    from app.main import build_memory_extraction_service
    from app.ai.agent_client import AdaptiveAgentModelClient
    from app.database.connection import get_readonly_connection
    from app.agent.memory_extraction import EXTRACTION_NETWORK_TIMEOUT
    fresh = get_readonly_connection(env[1])
    try:
        service = build_memory_extraction_service(fresh, env[1])
        assert service.personalization_service is not env[-1]
        assert service.personalization_service.repository.conn is fresh
        assert service.session_service.repo.conn is fresh
        assert isinstance(service.extractor.model_client, AdaptiveAgentModelClient)
        assert service.extractor.model_client.timeout == EXTRACTION_NETWORK_TIMEOUT == 5.0
    finally:
        fresh.close()


@pytest.mark.parametrize('outcome', ['valid', 'model_error', 'malformed', 'queue_full'])
def test_mainwindow_success_answer_before_extraction_and_failure_isolation(
    qtbot, env, task_service, date_service, outcome,
):
    from app.ui.main_window import MainWindow
    enable(env)
    conn, path, sessions, session, _, _, _ = env
    AgentMemoryRepository(conn).upsert(session['id'], env[4]['id'], 1, 'SUMMARY_SENTINEL')
    unchanged = ['tasks', 'agent_personalization_settings', 'agent_personal_memories',
                 'agent_session_memory', 'prompt_overrides']
    snapshots = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in unchanged}
    normal = Model(raw='Normal answer delivered')
    background = Model(block=True, raw='bad JSON' if outcome == 'malformed' else None,
                       error=RuntimeError('SECRET') if outcome == 'model_error' else None)
    runtime_connections, extraction_connections = [], []

    def runtime_factory(fresh):
        runtime_connections.append(fresh)
        return AgentRuntime(factory(Model())(fresh).session_service, normal)

    w = MainWindow(task_service, date_service, db_path=path,
                   agent_session_service=sessions, agent_runtime_factory=runtime_factory,
                   memory_extraction_service_factory=factory(background, extraction_connections))
    qtbot.addWidget(w)
    w._on_open_agent_session(session['id'])
    c = w.memory_extraction_coordinator
    pending_turn = None
    try:
        if outcome == 'queue_full':
            c.max_pending = 1
            assert c.enqueue(event(env))
            qtbot.waitUntil(background.entered.is_set, timeout=3000)
            u = sessions.append_user_message(session['id'], SOURCE)
            a = sessions.append_assistant_message(session['id'], 'Earlier answer')
            pending_turn = CompletedMemoryExtractionTurn(session['id'], u['id'], a['id'], True)
            assert c.enqueue(pending_turn)
        w._on_agent_send(session['id'], SOURCE)
        qtbot.waitUntil(lambda: bool(normal.requests) and not w._agent_inflight_sessions, timeout=5000)
        qtbot.waitUntil(background.entered.is_set, timeout=3000)
        latest = sessions.messages(session['id'])
        assert latest[-1]['content'] == 'Normal answer delivered'
        assert w.agent_workspace_page.error_label.text() == ''
        assert background.block and not background.release.is_set()  # answer already delivered
        assert extraction_connections[0][0] is not runtime_connections[0]
        assert runtime_connections[0] is not conn
    finally:
        background.release.set()
    wait_idle(qtbot, c)
    qtbot.waitUntil(lambda: not w._ai_workers, timeout=3000)
    if outcome in ('model_error', 'malformed'):
        assert not c.candidate_batches
    elif outcome == 'queue_full':
        assert len(c.candidate_batches) == 2
        assert latest[-2]['id'] not in {batch.source_message_id for batch in c.candidate_batches}
    else:
        assert len(c.candidate_batches) == 1
        assert c.candidate_batches[0].source_message_id == latest[-2]['id']
    assert sessions.messages(session['id']) == latest  # no extraction-created messages
    for t in unchanged:
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] == snapshots[t]
    w._shutdown()


def test_coordinator_settings_read_failure_isolated(qtbot, env, caplog):
    enable(env)

    def fail():
        raise RuntimeError('SECRET /db/path')

    c = MemoryExtractionCoordinator(env[1], factory(Model()), consent_check=fail)
    with caplog.at_level(logging.WARNING):
        assert not c.enqueue(event(env))
    assert not c.is_running and 'SECRET' not in caplog.text
    c.shutdown()


def test_mainwindow_defers_close_until_extraction_drains(qtbot, env, task_service, date_service):
    from app.ui.main_window import MainWindow
    enable(env)
    model = Model(block=True)
    w = MainWindow(task_service, date_service, db_path=env[1],
                   memory_extraction_service_factory=factory(model))
    qtbot.addWidget(w)
    w._tray = None
    c = w.memory_extraction_coordinator
    try:
        c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        close = QCloseEvent()
        started = time.monotonic()
        w.closeEvent(close)
        assert time.monotonic() - started < .2
        assert not close.isAccepted() and c.is_running
        assert c._active.parent() is c  # owner kept alive, not detached/destroyed
    finally:
        model.release.set()
    wait_idle(qtbot, c)
    assert not w._memory_close_deferred and not c.candidate_batches


def test_mainwindow_tray_quit_waits_asynchronously(qtbot, env, task_service, date_service, monkeypatch):
    from app.ui.main_window import MainWindow
    enable(env)
    model = Model(block=True)
    w = MainWindow(task_service, date_service, db_path=env[1], memory_extraction_service_factory=factory(model))
    qtbot.addWidget(w)
    quits = []
    monkeypatch.setattr(QApplication, 'quit', lambda *args: quits.append(True))
    c = w.memory_extraction_coordinator
    try:
        c.enqueue(event(env))
        qtbot.waitUntil(model.entered.is_set, timeout=3000)
        w.quit_app()
        assert quits == [] and c.is_running
    finally:
        model.release.set()
    qtbot.waitUntil(lambda: bool(quits), timeout=5000)
    assert not c.is_running and not c.candidate_batches
