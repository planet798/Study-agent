"""Learning Session controls, modal flows, and worker-busy regressions."""
import threading

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QMessageBox, QLabel

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository
from app.ui.app_shell import PageKey
from app.ui.archived_sessions_dialog import ArchivedSessionsDialog
from tests.test_learning_shell import _window

pytestmark = pytest.mark.ui


@pytest.fixture
def setup(qtbot, conn, repo, task_service, date_service):
    service = AgentSessionService(AgentRepository(conn), task_service)
    task = repo.create(title="Original", scheduled_date="2026-01-05")
    session = service.start_or_resume(task.id)
    other_task = repo.create(title="Other", scheduled_date="2026-01-05")
    other = service.start_or_resume(other_task.id)
    window = _window(qtbot, task_service, date_service, service)
    yield window, service, session["id"], other["id"], task
    window.close()


def action(window, sid, text):
    menu = window.sidebar.session_item(sid).management_menu
    return next(a for a in menu.actions() if a.text() == text)


def rename_input(monkeypatch, text, accepted=True, expected="Original"):
    def execute(dialog):
        assert dialog.textValue() == expected
        assert dialog.findChild(QLineEdit).maxLength() == 120
        dialog.setTextValue(text)
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(QInputDialog, "exec", execute)


def archive_confirmation(monkeypatch, accepted):
    def execute(box):
        assert box.text() == "归档后，会话将从侧栏收起，但聊天记录不会删除，可稍后恢复。"
        assert box.defaultButton().text() == "取消"
        next(b for b in box.buttons() if b.text() == ("归档" if accepted else "取消")).click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", execute)


def test_menu_navigation_is_independent_and_collapses(qtbot, setup):
    window, service, sid, _, _ = setup
    window.show()
    item = window.sidebar.session_item(sid)
    assert [a.text() for a in item.management_menu.actions()] == ["重命名", "固定", "归档"]
    navigation = []
    window.sidebar.session_requested.connect(navigation.append)
    qtbot.mouseClick(item.management_button, Qt.MouseButton.LeftButton)
    assert item.management_menu.isVisible()
    assert navigation == []
    item.management_menu.close()
    action(window, sid, "固定").trigger()
    assert navigation == []
    assert window.stack.currentWidget() is window.today_page
    item = window.sidebar.session_item(sid)
    item.click()
    assert navigation == [sid]
    assert window.sidebar.session_item(sid).isChecked()
    window.sidebar.set_collapsed(True)
    item = window.sidebar.session_item(sid)
    assert item.text() == "" and item.management_button.isHidden()
    assert window.sidebar.width() == 60
    assert item.toolTip() == "Original"
    window.sidebar.set_collapsed(False)
    assert not item.management_button.isHidden()


def test_sidebar_and_header_use_canonical_title_resolver(setup, monkeypatch):
    window, service, sid, _, _ = setup
    monkeypatch.setattr(service, "effective_title", lambda session: "Canonical visible title")
    window._refresh_learning_sessions()
    assert window.sidebar.session_item(sid).label() == "Canonical visible title"
    window._on_open_agent_session(sid)
    assert window.page_header.title() == "Canonical visible title"


def test_rename_cancel_invalid_reset_and_header(setup, monkeypatch):
    window, service, sid, _, task = setup
    service.append_user_message(sid, "History")
    window._on_open_agent_session(sid)
    page = window.agent_workspace_page
    page.input_edit.setPlainText("Unsent draft")
    before = service.get(sid)
    messages = service.messages(sid)
    rename_input(monkeypatch, "Cancelled", accepted=False)
    action(window, sid, "重命名").trigger()
    assert service.get(sid) == before
    assert window.page_header.title() == "Original"
    rename_input(monkeypatch, "   ")
    action(window, sid, "重命名").trigger()
    assert service.get(sid) == before
    assert window.statusBar().currentMessage() == "会话名称无效，请输入 1–120 个字符。"
    rename_input(monkeypatch, "  New title  ")
    action(window, sid, "重命名").trigger()
    assert window.sidebar.session_item(sid).label() == "New title"
    assert window.page_header.title() == "New title"
    assert service.get(sid)["title"] == "Original"
    assert window.task_service.get_task(task.id).title == "Original"
    assert service.get(sid)["updated_at"] == before["updated_at"]
    assert service.messages(sid) == messages
    assert page.input_edit.toPlainText() == "Unsent draft"
    assert [a.text() for a in window.sidebar.session_item(sid).management_menu.actions()] == [
        "重命名", "恢复原始名称", "固定", "归档"]
    action(window, sid, "恢复原始名称").trigger()
    assert service.get(sid)["display_title"] == ""
    assert window.sidebar.session_item(sid).label() == window.page_header.title() == "Original"


def test_pin_unpin_retains_current_and_all_pins_plus_ten(setup, conn, repo):
    window, service, sid, other, _ = setup
    for n in range(15):
        task = repo.create(title=f"Recent {n}", scheduled_date="2026-01-05")
        service.start_or_resume(task.id)
    conn.execute("UPDATE agent_sessions SET updated_at='2000' WHERE id=?", (sid,))
    conn.commit()
    window._on_open_agent_session(sid)
    before = service.get(sid)
    action(window, sid, "固定").trigger()
    assert next(iter(window.sidebar._session_items)) == sid
    assert window.agent_workspace_page.current_session_id == sid
    assert window.stack.currentWidget() is window.agent_workspace_page
    assert [a.text() for a in window.sidebar.session_item(sid).management_menu.actions()] == [
        "重命名", "取消固定", "归档"]
    service.pin(other)
    window._refresh_learning_sessions()
    expected = [s["id"] for s in service.list_sidebar_sessions(10)]
    assert list(window.sidebar._session_items) == expected and len(expected) == 12
    action(window, sid, "取消固定").trigger()
    assert list(window.sidebar._session_items)[-1] == sid
    assert window.sidebar.session_item(sid).isChecked()
    assert service.get(sid)["updated_at"] == before["updated_at"]
    assert window.stack.currentWidget() is window.agent_workspace_page
    window._switch_to_today()
    window._refresh_learning_sessions()
    assert window.sidebar.session_item(sid) is None


@pytest.mark.parametrize("current", [True, False])
def test_archive_cancel_then_confirm_preserves_history(setup, monkeypatch, current):
    window, service, sid, other, task = setup
    service.append_user_message(sid, "History")
    service.pin(sid)
    window._on_open_agent_session(sid if current else other)
    before = service.get(sid)
    messages = service.messages(sid)
    archive_confirmation(monkeypatch, False)
    action(window, sid, "归档").trigger()
    assert service.get(sid) == before
    assert window.sidebar.session_item(sid) is not None
    archive_confirmation(monkeypatch, True)
    action(window, sid, "归档").trigger()
    archived = service.get(sid)
    assert archived["status"] == "active" and archived["closed_at"] is None
    assert archived["archived_at"] and archived["pinned_at"] is None
    assert archived["updated_at"] == before["updated_at"]
    assert service.messages(sid) == messages
    assert window.task_service.get_task(task.id).status == "active"
    assert window.sidebar.session_item(sid) is None
    assert not window.sidebar.archived_sessions_btn.isHidden()
    assert window.stack.currentWidget() is (window.today_page if current else window.agent_workspace_page)
    assert window.sidebar.current_key() == (PageKey.TODAY.value if current else f"session:{other}")
    window._on_open_agent_session(sid)
    assert window.sidebar.session_item(sid) is None


def test_archived_dialog_restore_effective_titles_empty_no_autoopen(setup, monkeypatch):
    window, service, sid, other, _ = setup
    service.rename(sid, "Renamed archived")
    service.pin(sid)
    service.archive(sid)
    service.archive(other)
    window._refresh_learning_sessions()
    window._switch_to_today()
    def execute(dialog):
        labels = [label.text() for label in dialog.findChildren(QLabel) if not label.isHidden()]
        assert "Renamed archived" in labels and "Other" in labels
        assert set(dialog.restore_buttons) == {sid, other}
        dialog.restore_buttons[sid].click()
        assert service.get(sid)["archived_at"] is None
        assert service.get(sid)["pinned_at"] is None
        assert sid not in dialog.restore_buttons
        assert window.sidebar.session_item(sid).label() == "Renamed archived"
        assert window.stack.currentWidget() is window.today_page
        dialog.restore_buttons[other].click()
        assert not dialog.restore_buttons
        assert any(label.text() == "暂无已归档会话" for label in dialog.findChildren(QLabel))
        assert window.sidebar.archived_sessions_btn.isHidden()
        assert window.stack.currentWidget() is window.today_page
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(ArchivedSessionsDialog, "exec", execute)
    window.sidebar.archived_sessions_btn.click()
    assert window._archived_sessions_dialog is None


def test_busy_rechecked_after_rename_and_archive_modals(setup, monkeypatch):
    window, service, sid, _, _ = setup
    before = service.get(sid)
    def rename_exec(dialog):
        dialog.setTextValue("Must not save")
        window._agent_inflight_sessions.add(sid)
        window._refresh_learning_sessions()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QInputDialog, "exec", rename_exec)
    window._on_session_rename(sid)
    assert service.get(sid) == before
    window._agent_inflight_sessions.clear()
    window._refresh_learning_sessions()
    def archive_exec(box):
        window._approval_inflight.add(123)
        window._refresh_learning_sessions()
        next(b for b in box.buttons() if b.text() == "归档").click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", archive_exec)
    window._on_session_archive(sid)
    assert service.get(sid) == before
    window._approval_inflight.clear()
    window._refresh_learning_sessions()


def test_offscreen_real_modal_roundtrip(setup):
    """Exercise actual nested Qt event loops, without mocked exec or sleeps."""
    window, service, sid, _, _ = setup
    window.show()
    window._on_open_agent_session(sid)
    def rename():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        dialog.setTextValue("Offscreen rename")
        dialog.accept()
    QTimer.singleShot(0, rename)
    action(window, sid, "重命名").trigger()
    assert window.page_header.title() == "Offscreen rename"
    action(window, sid, "固定").trigger()
    assert service.get(sid)["pinned_at"]
    action(window, sid, "取消固定").trigger()
    assert service.get(sid)["pinned_at"] is None
    def archive():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        next(b for b in box.buttons() if b.text() == "归档").click()
    QTimer.singleShot(0, archive)
    action(window, sid, "归档").trigger()
    assert window.stack.currentWidget() is window.today_page
    def restore():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ArchivedSessionsDialog)
        dialog.restore_buttons[sid].click()
        dialog.reject()
    QTimer.singleShot(0, restore)
    window.sidebar.archived_sessions_btn.click()
    assert service.get(sid)["archived_at"] is None
    assert window.sidebar.session_item(sid).label() == "Offscreen rename"
    assert window.stack.currentWidget() is window.today_page


def test_controlled_errors_do_not_leak_exception_text(setup, monkeypatch):
    window, service, sid, _, _ = setup
    def fail(*args):
        raise RuntimeError("private SQL / host path")
    monkeypatch.setattr(service, "pin", fail)
    action(window, sid, "固定").trigger()
    assert window.statusBar().currentMessage() == "无法更新学习会话，请重试。"
    assert service.get(sid)["pinned_at"] is None
    service.close(sid)
    assert not window._mutate_session_metadata("archive", sid)
    assert window.statusBar().currentMessage() == "无法更新学习会话，请重试。"


def assert_busy_protection(window, service, sid, other, monkeypatch):
    before = {s: service.get(s) for s in (sid, other)}
    def forbidden(*args):
        pytest.fail("Busy management must not open a modal dialog")
    monkeypatch.setattr(QInputDialog, "exec", forbidden)
    monkeypatch.setattr(QMessageBox, "exec", forbidden)
    monkeypatch.setattr(ArchivedSessionsDialog, "exec", forbidden)
    dialog = ArchivedSessionsDialog(window)
    window._archived_sessions_dialog = dialog
    window._refresh_learning_sessions()
    for s in (sid, other):
        item = window.sidebar.session_item(s)
        assert not item.management_button.isEnabled()
        assert all(not a.isEnabled() for a in item.management_menu.actions())
        window._on_session_rename(s)
        window._on_session_archive(s)
        for operation, args in [("rename", ("No",)), ("reset_title", ()),
                                ("pin", ()), ("unpin", ()), ("archive", ()), ("restore", ())]:
            assert not window._mutate_session_metadata(operation, s, *args)
    window._on_archived_sessions()
    assert not window.sidebar.archived_sessions_btn.isEnabled()
    assert all(not b.isEnabled() for b in dialog.restore_buttons.values())
    assert {s: service.get(s) for s in (sid, other)} == before
    assert window.agent_workspace_page.current_session_id == sid
    assert window.sidebar.session_item(sid).isChecked()
    window._archived_sessions_dialog = None
    dialog.deleteLater()


@pytest.mark.threaded
@pytest.mark.integration
def test_running_agent_turn_blocks_all_management(qtbot, conn, repo, task_service, date_service, monkeypatch):
    from tests.test_agent_workspace_integration import _make_window, _worker_factory_with_behavior
    service = AgentSessionService(AgentRepository(conn), task_service)
    task = repo.create(title="Busy", scheduled_date="2026-01-05")
    other_task = repo.create(title="Other", scheduled_date="2026-01-05")
    other = service.start_or_resume(other_task.id)["id"]
    entered, release = threading.Event(), threading.Event()
    window = _make_window(conn, task_service, date_service, service,
                          _worker_factory_with_behavior("Answer", entered, release))
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    sid = window.agent_workspace_page.current_session_id
    window._on_agent_send(sid, "Question")
    try:
        assert entered.wait(3)
        assert_busy_protection(window, service, sid, other, monkeypatch)
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window._agent_inflight_sessions, timeout=7000)
        window.close()


@pytest.mark.threaded
@pytest.mark.integration
def test_running_approval_blocks_all_management(qtbot, conn, repo, task_service, date_service, monkeypatch):
    from app.main import build_agent_approval_executor
    from tests.test_agent_approval_integration import CompletionModel, build_window, request
    service = AgentSessionService(AgentRepository(conn), task_service)
    task = repo.create(title="Busy approval", scheduled_date="2026-01-05", source="generated")
    other_task = repo.create(title="Other", scheduled_date="2026-01-05")
    other = service.start_or_resume(other_task.id)["id"]
    window, approvals = build_window(conn, task_service, date_service, service, CompletionModel())
    qtbot.addWidget(window)
    page, sid = request(qtbot, window, service, task)
    entered, release = threading.Event(), threading.Event()
    def factory(fresh):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Controlled worker timed out")
        return build_agent_approval_executor(fresh)
    window.agent_approval_service_factory = factory
    page.findChild(type(page.send_button), "AgentApprovalApprove").click()
    try:
        assert entered.wait(3)
        assert_busy_protection(window, service, sid, other, monkeypatch)
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window._approval_inflight, timeout=7000)
        window.close()
