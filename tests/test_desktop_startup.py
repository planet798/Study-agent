from app.ui import desktop_startup as startup


def test_current_database_needs_no_confirmation(tmp_path):
    db = tmp_path / "study_agent.db"
    db.touch()
    assert startup.prepare_desktop_data(db, lambda path: {"blocked": False})


def test_background_failure_is_sanitized(qtbot):
    def fail():
        raise RuntimeError("secret token must never appear")
    worker = startup._Operation(fail, None)
    with qtbot.waitSignal(worker.failed, timeout=3000) as signal:
        worker.start()
    worker.wait()
    assert "secret token" not in signal.args[0]
    assert "RuntimeError" in signal.args[0]


def test_declining_upgrade_never_runs_operation(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    db = tmp_path / "study_agent.db"
    db.touch()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    monkeypatch.setattr(startup, "_run", lambda *a: (_ for _ in ()).throw(AssertionError("must not migrate")))
    assert not startup.prepare_desktop_data(db, lambda path: {"blocked": True, "reason": "old_schema"})
