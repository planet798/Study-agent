"""Visual archived list emits IDs only and keeps no stale rows."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from app.ui.archived_sessions_dialog import ArchivedSessionsDialog

pytestmark = pytest.mark.ui


def test_restore_signal_busy_and_empty_state(qtbot):
    dialog = ArchivedSessionsDialog()
    qtbot.addWidget(dialog)
    rows = [{"id": 2, "visible_title": "<b>User title</b>", "archived_at": "2026-01-02"},
            {"id": 1, "visible_title": "Original", "archived_at": "2026-01-01"}]
    dialog.set_sessions(rows)
    ids = []
    dialog.restore_requested.connect(ids.append)
    dialog.restore_buttons[2].click()
    assert ids == [2]
    labels = dialog.findChildren(QLabel)
    title = next(label for label in labels if label.text() == "<b>User title</b>")
    assert title.textFormat() == Qt.TextFormat.PlainText
    dialog.set_busy(True)
    assert all(not b.isEnabled() for b in dialog.restore_buttons.values())
    dialog.restore_buttons[1].click()
    assert ids == [2]
    dialog.set_sessions(rows[:1])
    assert not dialog.restore_buttons[2].isEnabled()
    dialog.set_busy(False)
    assert dialog.restore_buttons[2].isEnabled()
    dialog.set_sessions([])
    assert dialog.restore_buttons == {}
    assert any(label.text() == "暂无已归档会话" for label in dialog.findChildren(QLabel))
