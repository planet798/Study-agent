"""Personalization SQL constraints and fresh worker connection compatibility."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database.connection import get_fresh_connection, get_raw_connection
from app.database.personalization_repository import PersonalizationRepository
from app.services.personalization_service import PersonalizationService


@pytest.mark.parametrize('sql', [
    'INSERT INTO agent_personalization_settings(id) VALUES (2)',
    'UPDATE agent_personalization_settings SET memory_enabled=2',
    'UPDATE agent_personalization_settings SET auto_memory_enabled=-1',
    "INSERT INTO agent_personal_memories(content,source_type,created_at,updated_at) "
    "VALUES ('x','session','t','t')",
    "INSERT INTO agent_personal_memories(content,source_type,source_session_id,created_at,updated_at) "
    "VALUES ('x','manual',1,'t','t')",
    "INSERT INTO agent_personal_memories(content,source_type,enabled,created_at,updated_at) "
    "VALUES ('x','manual',2,'t','t')",
])
def test_sql_constraints(conn, sql):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    conn.rollback()


@pytest.mark.threaded
def test_worker_owned_fresh_connection(tmp_path):
    path = tmp_path / 'personalization.db'
    conn = get_fresh_connection(path)
    service = PersonalizationService(PersonalizationRepository(conn))
    service.set_instructions('Persistent instructions')
    service.set_memory_enabled(True)
    service.set_auto_memory_enabled(True)
    memory = service.add_manual_memory('Persistent distilled preference')
    conn.close()

    def worker():
        fresh = get_raw_connection(path)
        try:
            local = PersonalizationService(PersonalizationRepository(fresh))
            assert local.get_settings()['instructions'] == 'Persistent instructions'
            assert local.get_settings()['memory_enabled'] == 1
            assert local.get_settings()['auto_memory_enabled'] == 1
            assert local.list_memories() == [memory]
            return local.edit_memory(memory['id'], 'Worker edit')
        finally:
            fresh.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        edited = executor.submit(worker).result(timeout=10)
    conn = get_raw_connection(path)
    try:
        assert PersonalizationRepository(conn).get_memory(memory['id']) == edited
    finally:
        conn.close()
