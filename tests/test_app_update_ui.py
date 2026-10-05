import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QThread
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QDialog, QWidget
import pytest

from app.services.app_update_service import AppUpdateService
from app.ui.app_update_controller import AppUpdateController
from app.ui.app_update_exit import begin_update, commit_update, exit_block_reason
from app.ui.app_update_panel import AppUpdatePanel
from app.ui.settings_drafts import SettingsDrafts
from app.updates.jobs import write_record
from app.updates.models import UpdateState
from tests.test_app_update_service import Transport, release_fixture


@pytest.fixture(autouse=True)
def isolated_update_theme(qapp):
    # 主窗口按产品默认切到深色；测试不能把此全局状态留给其他模块。
    from app.ui.design.theme_manager import ThemeManager
    previous = ThemeManager._instance
    palette, stylesheet = QPalette(qapp.palette()), qapp.styleSheet()
    ThemeManager.reset_instance()
    yield
    ThemeManager.reset_instance()
    qapp.setPalette(palette)
    qapp.setStyleSheet(stylesheet)
    ThemeManager._instance = previous


def make_controller(tmp_path):
    _, data = release_fixture()
    return AppUpdateController(AppUpdateService(cache_root=tmp_path, transport=Transport(data)))


def test_source_mode_shows_release_without_allowing_install(qtbot, tmp_path):
    controller = make_controller(tmp_path)
    panel = AppUpdatePanel(controller=controller, can_install=False)
    qtbot.addWidget(panel)
    panel.show()
    panel.check_btn.click()
    qtbot.waitUntil(lambda: controller.state == UpdateState.AVAILABLE)
    assert not panel.download_btn.isEnabled()
    assert panel.source_hint.isVisible()
    panel.notes_btn.click()
    assert "<script>" in panel.notes.toPlainText()
    assert panel.notes.isVisible()
    controller.stop()


def test_check_download_survives_hiding_panel(qtbot, tmp_path):
    controller = make_controller(tmp_path)
    panel = AppUpdatePanel(controller=controller, can_install=True)
    qtbot.addWidget(panel)
    panel.check_btn.click()
    qtbot.waitUntil(lambda: controller.state == UpdateState.AVAILABLE)
    panel.download_btn.click()
    panel.hide()
    qtbot.waitUntil(lambda: controller.state == UpdateState.READY)
    assert controller.downloaded.installer.is_file()
    assert panel.install_btn.isEnabled()
    controller.stop()


def test_duplicate_checks_do_not_create_workers_and_stop_ignores_late_result(qtbot):
    started = threading.Event()
    class SlowService:
        def check(self, cancel):
            started.set()
            while not cancel():
                time.sleep(0.01)
            return None
    controller = AppUpdateController(SlowService())
    controller.check()
    qtbot.waitUntil(started.is_set)
    worker = controller.worker
    controller.check()
    assert controller.worker is worker
    controller.stop()
    qtbot.wait(20)
    assert controller.worker is None
    assert controller.state == UpdateState.CHECKING
    controller.check()
    assert controller.worker is None


def test_prepare_handshake_failure_keeps_application_running(qtbot, tmp_path, monkeypatch):
    job = tmp_path / "job.json"
    job.write_text("{}")
    monkeypatch.setattr("app.ui.app_update_controller.prepare_job", lambda _: job)
    controller = make_controller(tmp_path)
    controller.downloaded = object()
    controller.launch = lambda *a, **kw: SimpleNamespace(poll=lambda: 1)
    failures = []
    controller.preparation_failed.connect(lambda: failures.append(True))
    controller.prepare()
    qtbot.waitUntil(lambda: bool(failures))
    assert controller.state == UpdateState.READY
    assert (tmp_path / "abort.json").exists()
    assert not (tmp_path / "commit.json").exists()
    controller.stop()


def test_ready_handshake_requires_explicit_commit(qtbot, tmp_path, monkeypatch):
    job = tmp_path / "job.json"
    job.write_text("{}")
    write_record(tmp_path / "ready.json", {"ready": True})
    monkeypatch.setattr("app.ui.app_update_controller.prepare_job", lambda _: job)
    controller = make_controller(tmp_path)
    controller.downloaded = object()
    controller.launch = lambda *a, **kw: SimpleNamespace(poll=lambda: None)
    ready = []
    controller.ready_to_exit.connect(lambda: ready.append(True))
    controller.prepare()
    qtbot.waitUntil(lambda: bool(ready))
    assert not (tmp_path / "commit.json").exists()
    controller.commit_exit()
    controller.stop()
    assert (tmp_path / "commit.json").exists()
    assert not (tmp_path / "abort.json").exists()


def test_hidden_prompt_drafts_and_running_tasks_block_update(make_window, qtbot):
    window = make_window()
    qtbot.addWidget(window)
    assert exit_block_reason(window) is None
    drafts = window.ai_settings_page.prompt_panel._drafts
    drafts.load("hidden-prompt", "saved")
    drafts.edit("hidden-prompt", "unsaved")
    assert drafts.has_pending
    assert "未保存" in exit_block_reason(window)
    drafts.accept("hidden-prompt", "saved")
    window._approval_inflight.add(1)
    assert "后台任务" in exit_block_reason(window)
    window._approval_inflight.clear()
    window._quit_requested = True
    window.close()


def test_unsent_composer_is_protected(make_window, qtbot):
    window = make_window()
    qtbot.addWidget(window)
    window.agent_workspace_page = SimpleNamespace(composer=SimpleNamespace(text=lambda: "draft"))
    assert "未发送" in exit_block_reason(window)
    window.agent_workspace_page = None
    window._quit_requested = True
    window.close()


def test_installed_update_only_exits_after_commit(make_window, qtbot, tmp_path, monkeypatch):
    window = make_window()
    qtbot.addWidget(window)
    controller = window.ai_settings_page.update_panel.controller
    controller.downloaded = object()
    controller.job = tmp_path / "job.json"
    calls = []
    monkeypatch.setattr(window, "quit_app", lambda: calls.append("quit"))
    commit_update(window)
    assert (tmp_path / "commit.json").exists()
    assert calls == ["quit"]
    controller._committed = False
    controller.downloaded = None
    window._quit_requested = True
    window.close()


def test_prepare_failure_unlocks_main_window(make_window, qtbot, tmp_path, monkeypatch):
    window = make_window()
    qtbot.addWidget(window)
    controller = window.ai_settings_page.update_panel.controller
    controller.downloaded = object()
    def fail(_):
        raise OSError("private-value")
    monkeypatch.setattr("app.ui.app_update_controller.prepare_job", fail)
    begin_update(window)
    assert not window.centralWidget().isEnabled()
    qtbot.waitUntil(window.centralWidget().isEnabled)
    assert "private" not in controller.message
    window._quit_requested = True
    window.close()


def test_hidden_dialog_worker_blocks_update(make_window, qtbot):
    window = make_window()
    qtbot.addWidget(window)
    dialog = QDialog(window)
    dialog._worker = SimpleNamespace(isRunning=lambda: True)
    dialog.hide()
    assert "后台任务" in exit_block_reason(window)
    dialog._worker = None
    window._quit_requested = True
    window.close()
