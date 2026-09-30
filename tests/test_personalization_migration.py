"""Real populated v26 -> v27 migration and mutable-state verifier contract."""

import pytest

from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.connection import get_raw_connection
from app.database.personalization_repository import PersonalizationRepository
from app.database.repository import TaskRepository
from app.database.schema import SCHEMA_VERSION, migrate_stepwise
from app.database.task_workspace_repository import TaskWorkspaceRepository
from app.diagnostics import release_migration as rm
from app.services.personalization_service import PersonalizationService

pytestmark = pytest.mark.migration


def populate(conn):
    task = TaskRepository(conn).create(title='Original', scheduled_date='2026-01-01')
    agent = AgentRepository(conn)
    session = agent.create_session(task.id, 'Immutable title')
    user = agent.add_message(session['id'], 'user', 'Question')
    assistant = agent.add_message(session['id'], 'assistant', 'Answer')
    AgentMemoryRepository(conn).upsert(session['id'], user['id'], 1, 'Session compaction')
    TaskWorkspaceRepository(conn).upsert(task.id, 'managed')
    approval = conn.execute(
        'INSERT INTO agent_approval_requests(session_id, task_id, assistant_message_id, '
        'tool_call_id, tool_name, requested_at) VALUES (?, ?, ?, ?, ?, ?)',
        (session['id'], task.id, assistant['id'], 'call-1', 'request_save_learning_note', 't'),
    ).lastrowid
    conn.execute("INSERT INTO agent_approval_events(approval_id,event_type,actor,created_at) "
                 "VALUES (?, 'requested', 'agent', 't')", (approval,))
    conn.execute("INSERT INTO prompt_overrides(prompt_key,content,updated_at) "
                 "VALUES ('planner', 'Original {{template}}', 't')")
    conn.commit()
    return session, user


def test_populated_real_v26_to_v27(tmp_path):
    conn = get_raw_connection(tmp_path / 'real-v26.db')
    try:
        assert migrate_stepwise(conn, target=26) == 26
        populate(conn)
        tables = ['tasks', 'agent_sessions', 'agent_messages', 'agent_session_memory',
                  'agent_approval_requests', 'agent_approval_events', 'task_workspaces',
                  'prompt_overrides']
        order = {t: ('session_id' if t == 'agent_session_memory' else 'id') for t in tables}
        rows = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY {order[t]}')]
                for t in tables}
        before = rm.inventory(conn)
        assert before['counts']['agent_personal_memories'] is None
        steps = []
        assert migrate_stepwise(conn, on_step=steps.append) == SCHEMA_VERSION == 27
        assert steps == [27]
        for table in tables:
            assert [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY {order[table]}')] == rows[table]
        repo = PersonalizationRepository(conn)
        assert repo.get_settings() == dict(id=1, instructions='', memory_enabled=0,
                                           auto_memory_enabled=0, updated_at='')
        assert repo.list_memories() == []
        assert rm.verify(conn, before)['ok']
        repo.set_instructions('Preserve on repeat')
        from app.database.schema import _migrate_v27
        _migrate_v27(conn)
        assert repo.get_settings()['instructions'] == 'Preserve on repeat'
        assert migrate_stepwise(conn) == 27
    finally:
        conn.close()


@pytest.mark.parametrize('tamper', ['session_title', 'message_content', 'delete_message'])
def test_personalization_mutations_are_legal_but_history_tampering_is_not(conn, tamper):
    session, message = populate(conn)
    deletable = AgentRepository(conn).add_message(session['id'], 'user', 'Follow-up')
    service = PersonalizationService(PersonalizationRepository(conn))
    service.set_instructions('Original instructions')
    memory = service.add_manual_memory('Original memory')
    before = rm.inventory(conn)
    service.set_instructions('Edited instructions')
    service.edit_memory(memory['id'], 'Edited memory')
    service.disable_memory(memory['id'])
    service.delete_memory(memory['id'])
    result = rm.verify(conn, before)
    assert result['ok'], result
    assert result['history_modified_rows'] == result['history_missing_ids'] == {}
    for table in ('agent_personalization_settings', 'agent_personal_memories'):
        assert table not in rm.HISTORY_TABLES
        assert table not in rm.FINGERPRINT_COLUMNS
        assert table not in rm.GROWTH_TABLES
    assert rm.FINGERPRINT_VERSION == 7
    if tamper == 'session_title':
        conn.execute("UPDATE agent_sessions SET title='tampered' WHERE id=?", (session['id'],))
    elif tamper == 'message_content':
        conn.execute("UPDATE agent_messages SET content='tampered' WHERE id=?", (message['id'],))
    else:
        # A history row without compaction/approval dependents still cannot be deleted.
        conn.execute("DELETE FROM agent_messages WHERE id=?", (deletable['id'],))
    conn.commit()
    assert not rm.verify(conn, before)['ok']
