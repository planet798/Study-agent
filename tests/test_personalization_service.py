"""Personalization domain validation and connection-local persistence."""

import pytest

from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.personalization_repository import PersonalizationRepository
from app.database.repository import TaskRepository
from app.services.personalization_service import PersonalizationService


@pytest.fixture
def service(conn):
    return PersonalizationService(PersonalizationRepository(conn))


def test_settings_lifecycle(service, conn):
    assert service.get_settings() == dict(id=1, instructions='', memory_enabled=0,
                                        auto_memory_enabled=0, updated_at='')
    first = service.set_instructions('  First\nSecond\r\t{{literal}}  ')
    assert first['instructions'] == 'First\nSecond\r\t{{literal}}'
    assert service.set_instructions('x' * 8000)['instructions'] == 'x' * 8000
    cleared = service.set_instructions(' \n ')
    assert cleared['instructions'] == ''
    assert cleared['updated_at'] > first['updated_at']
    service.set_memory_enabled(True)
    service.set_auto_memory_enabled(True)
    new = PersonalizationService(PersonalizationRepository(conn))
    assert new.get_settings()['memory_enabled'] == 1
    assert new.get_settings()['auto_memory_enabled'] == 1
    assert new.set_memory_enabled(False)['memory_enabled'] == 0
    assert new.set_auto_memory_enabled(False)['auto_memory_enabled'] == 0


@pytest.mark.parametrize('text', [None, 1, b'text', 'x' * 8001, '\0', '\x01',
                                  '\x7f', '\x85', '\ud800', '\x1fok', '\u202e', '\u200b'])
def test_invalid_instructions(service, text):
    before = service.get_settings()
    with pytest.raises(ValueError):
        service.set_instructions(text)
    assert service.get_settings() == before


@pytest.mark.parametrize('text', ['', ' \n\t ', None, 1, 'x' * 1001, '\0', '\x02', '\udfff', '\u202e'])
def test_invalid_memory(service, text):
    with pytest.raises(ValueError):
        service.add_manual_memory(text)
    memory = service.add_manual_memory('valid')
    with pytest.raises(ValueError):
        service.edit_memory(memory['id'], text)
    assert service.repository.get_memory(memory['id'])['content'] == 'valid'


def test_manual_lifecycle(service):
    first = service.add_manual_memory('  One\n\t{{literal}}  ')
    second = service.add_manual_memory('x' * 1000)
    assert first['content'] == 'One\n\t{{literal}}'
    assert first['source_type'] == 'manual'
    assert first['source_session_id'] is first['source_message_id'] is None
    edited = service.edit_memory(first['id'], 'Changed')
    assert edited['created_at'] == first['created_at']
    assert edited['updated_at'] > first['updated_at']
    assert service.disable_memory(first['id'])['enabled'] == 0
    assert service.list_memories(False) == [second]
    assert [m['id'] for m in service.list_memories()] == [first['id'], second['id']]
    assert service.enable_memory(first['id'])['enabled'] == 1
    service.delete_memory(first['id'])
    assert service.repository.get_memory(first['id']) is None
    service.delete_memory(first['id'])  # idempotent physical delete
    with pytest.raises(ValueError):
        service.edit_memory(first['id'], 'missing')


def test_provenance_and_session_memory_separation(service, conn):
    tasks, agent = TaskRepository(conn), AgentRepository(conn)
    sessions = [agent.create_session(tasks.create(title=str(i), scheduled_date='2026-01-01').id)
                for i in range(2)]
    messages = [agent.add_message(s['id'], 'user', 'raw conversation') for s in sessions]
    compact = AgentMemoryRepository(conn)
    original = compact.upsert(sessions[0]['id'], messages[0]['id'], 1, 'Session summary')
    assert service.add_session_memory('Distilled', sessions[0]['id'])['source_message_id'] is None
    memory = service.add_session_memory('Distilled preference', sessions[0]['id'], messages[0]['id'])
    assert memory['source_message_id'] == messages[0]['id']
    for sid, mid in [(99999, None), (None, None), (sessions[0]['id'], 99999),
                     (sessions[0]['id'], messages[1]['id'])]:
        with pytest.raises(ValueError):
            service.add_session_memory('Preference', sid, mid)
    with pytest.raises(ValueError):
        service.repository.create_memory('manual', 'manual', sessions[0]['id'])
    with pytest.raises(ValueError):
        service.repository.create_memory('invalid', 'other')
    service.edit_memory(memory['id'], 'Edited preference')
    service.disable_memory(memory['id'])
    service.delete_memory(memory['id'])
    assert compact.get_for_session(sessions[0]['id']) == original
    assert len(agent.list_messages(sessions[0]['id'])) == 1


@pytest.mark.parametrize('method', ['set_memory_enabled', 'set_auto_memory_enabled'])
def test_toggle_requires_bool(service, method):
    with pytest.raises(ValueError):
        getattr(service, method)('false')
