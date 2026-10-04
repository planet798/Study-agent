import sys

from app import desktop_entry


def test_error_log_omits_exception_message_and_locals(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_entry, "logs_dir", lambda: tmp_path)
    try:
        raise RuntimeError("private-token-and-conversation")
    except RuntimeError:
        desktop_entry._record_error(*sys.exc_info(), notify=False)
    text = (tmp_path / "startup.log").read_text(encoding="utf-8")
    assert "RuntimeError" in text
    assert "test_desktop_entry.py" in text
    assert "private-token" not in text


def test_startup_failure_returns_nonzero_without_leaking_message(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(desktop_entry, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["StudyAgent.exe", "--self-test", str(tmp_path / "report.json")])
    def fail(report):
        raise ValueError("private-credential")
    monkeypatch.setattr(desktop_entry, "self_test", fail)
    assert desktop_entry.run() == 1
    assert "private-credential" not in (tmp_path / "startup.log").read_text()
