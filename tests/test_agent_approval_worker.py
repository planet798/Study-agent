"""Approval execution owns a worker-thread connection and sanitizes failures."""
import threading

import pytest

# Worker-thread connection ownership, not a historical-schema migration test.
pytestmark = [pytest.mark.ui, pytest.mark.threaded]
from app.ui.agent_approval_worker import AgentApprovalWorker
from app.database.connection import get_connection


def test_worker_opens_closes_fresh_connection(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "approval-worker.db"
    parent_conn = get_connection(path)
    opened = []
    real_open = get_connection
    def tracked(db_path):
        c = real_open(db_path)
        opened.append(c)
        return c
    monkeypatch.setattr("app.ui.agent_approval_worker.get_connection", tracked)
    called = []
    class Service:
        def approve_and_execute(self, approval_id):
            called.append((threading.get_ident(), approval_id))
            return {"status": "executed", "id": approval_id}
    def factory(conn):
        assert conn is not parent_conn
        conn.execute("SELECT 1 FROM agent_approval_requests")
        return Service()
    worker = AgentApprovalWorker(path, factory, 7)
    received = []
    worker.succeeded.connect(received.append)
    worker.start()
    qtbot.waitUntil(lambda: not worker.isRunning() and bool(received), timeout=5000)
    assert received == [{"status": "executed", "id": 7}]
    assert called[0][0] != threading.get_ident()
    assert called[0][1] == 7
    import sqlite3
    try:
        opened[0].execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("worker connection not closed")
    parent_conn.close()
