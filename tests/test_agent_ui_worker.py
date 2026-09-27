"""AgentTurnWorker thread affinity, signal and fresh connection lifecycle."""

from __future__ import annotations

import threading

from app.ui.ai_worker import AgentTurnWorker


class _Connection:
    def __init__(self):
        self.created_on = threading.get_ident()
        self.closed = False

    def close(self):
        self.closed = True


class _Runtime:
    def __init__(self, conn, outcome=None):
        self.conn = conn
        self.outcome = outcome
        self.called_on = threading.get_ident()
        self.args = None

    def send_message(self, session_id, user_text):
        self.args = (session_id, user_text)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or {"session_id": session_id, "ok": True}


def _run_worker(qtbot, worker, success=True):
    signal_object = worker.succeeded if success else worker.failed
    with qtbot.waitSignal(signal_object, timeout=5000) as signal:
        worker.start()
    worker.wait(5000)
    return signal.args


def test_worker_constructs_runtime_from_fresh_connection_in_worker_thread(
    qtbot, monkeypatch
):
    import app.ui.ai_worker as worker_module

    connections = []
    runtimes = []

    def get_connection(path):
        assert path == "/tmp/fresh-worker.db"
        conn = _Connection()
        connections.append(conn)
        return conn

    def runtime_factory(conn):
        runtime = _Runtime(conn)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(worker_module, "get_connection", get_connection)
    worker = AgentTurnWorker(
        "/tmp/fresh-worker.db", runtime_factory, 42, "hello"
    )
    result, = _run_worker(qtbot, worker, success=True)

    assert result == {"session_id": 42, "ok": True}
    assert connections[0].created_on == runtimes[0].called_on
    assert connections[0].created_on != threading.get_ident()
    assert runtimes[0].args == (42, "hello")
    assert connections[0].closed is True


def test_worker_failure_is_user_safe_and_connection_closes(qtbot, monkeypatch):
    import app.ui.ai_worker as worker_module

    conn = _Connection()
    monkeypatch.setattr(worker_module, "get_connection", lambda _path: conn)

    def factory(_conn):
        return _Runtime(_conn, RuntimeError("secret token / traceback"))

    worker = AgentTurnWorker("db", factory, 7, "failed turn")
    error, = _run_worker(qtbot, worker, success=False)
    assert error == "本轮暂未完成，请检查模型配置或网络后重试。"
    assert "secret" not in error and "traceback" not in error
    assert conn.closed is True


def test_runtime_factory_failure_still_closes_connection(qtbot, monkeypatch):
    import app.ui.ai_worker as worker_module

    conn = _Connection()
    monkeypatch.setattr(worker_module, "get_connection", lambda _path: conn)

    def factory(_conn):
        raise RuntimeError("internal details")

    worker = AgentTurnWorker("db", factory, 1, "text")
    error, = _run_worker(qtbot, worker, success=False)
    assert "internal details" not in error
    assert conn.closed is True
