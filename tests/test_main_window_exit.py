"""主窗口关闭行为的验收测试（Phase 2：托盘常驻）。

新语义：
- 点右上角 X = 最小化到系统托盘，进程继续运行，托盘与 AI 线程不清理；
- 托盘“打开” = 恢复窗口；
- 托盘“退出” = 真正退出：清理托盘、join AI worker、关闭 AI 对话框，
  并结束事件循环。

覆盖：
- X 不退出、隐藏窗口
- 托盘恢复窗口
- 托盘退出真正清理并退出
- 正在运行的 AI worker：X 不打断，退出时安全 join
- 打开的 AI 对话框：X 不关闭，退出时关闭
- 子进程实测：托盘退出后 conn.exec() 返回、进程可结束
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from PySide6.QtGui import QCloseEvent


class _SlowReviewService:
    """模拟仍在运行的 AI 复核：阻塞一小段时间再返回。"""

    def __init__(self, delay: float = 0.3):
        self._delay = delay

    def is_configured(self):  # noqa: D102
        return True

    def review_task(self, task, reason, today=None):
        import time

        time.sleep(self._delay)
        return {"ok": True}


class _TrayStub:
    """代替 QSystemTrayIcon 的最小桩，验证 hide/deleteLater 被调用。"""

    def __init__(self):
        self.hidden = False
        self.deleted = False

    def hide(self):
        self.hidden = True

    def deleteLater(self):
        self.deleted = True


class TestXCloseMinimizesToTray:
    def test_x_close_hides_and_does_not_quit(self, make_window, qtbot):
        w = make_window()
        w._tray = _TrayStub()  # 模拟托盘可用
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)

        ev = QCloseEvent()
        w.closeEvent(ev)

        assert ev.isAccepted() is False  # 关闭被忽略
        assert w._quit_requested is False  # 没有进入退出流程
        assert w.isVisible() is False  # 窗口已隐藏到托盘

    def test_widget_close_triggers_tray_minimize(self, make_window, qtbot):
        """直接调用 w.close()（模拟点 X）也应等价于最小化到托盘。"""
        w = make_window()
        w._tray = _TrayStub()  # 模拟托盘可用
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)

        assert w.close() is False  # closeEvent 忽略 -> close() 返回 False
        assert w._quit_requested is False
        assert w.isVisible() is False

    def test_x_close_without_tray_quits(self, make_window, qtbot):
        """无托盘可用时，点 X 回退为真正退出（不残留无明显入口的隐形进程）。"""
        w = make_window()
        w._tray = None  # 模拟托盘不可用（如部分 Linux）
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)

        ev = QCloseEvent()
        w.closeEvent(ev)

        assert ev.isAccepted() is True
        assert w._quit_requested is True
        assert w._tray is None


class TestTrayRestore:
    def test_tray_restore_shows_window(self, make_window, qtbot):
        w = make_window()
        w._tray = _TrayStub()  # 模拟托盘可用
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)

        w.closeEvent(QCloseEvent())  # X -> 隐藏
        assert w.isVisible() is False

        w._restore_from_tray()
        assert w.isVisible() is True


class TestQuitApp:
    def test_quit_app_sets_flag_and_cleans_tray(self, make_window):
        w = make_window()
        tray = _TrayStub()
        w._tray = tray

        w.quit_app()

        assert w._quit_requested is True
        assert w._tray is None
        assert tray.hidden is True
        assert tray.deleted is True

    def test_quit_app_joins_running_ai_worker(
        self, make_window, qtbot, task_service
    ):
        from app.ui.ai_worker import AIReviewWorker

        w = make_window()
        qtbot.addWidget(w)
        task = task_service.create_task("任务", scheduled_date=w.current_date)
        worker = AIReviewWorker(
            _SlowReviewService(delay=0.3), task, "原因", today=w.current_date, parent=w
        )
        w._ai_workers.append(worker)
        worker.start()
        qtbot.wait(20)
        assert worker.isRunning()

        w.quit_app()

        assert worker.isFinished() is True
        assert w._ai_workers == []

    def test_quit_app_closes_open_ai_dialog(
        self, make_window, qtbot, task_service
    ):
        from app.ui.dialogs import AIReviewDialog

        w = make_window()
        qtbot.addWidget(w)
        task = task_service.create_task("任务", scheduled_date=w.current_date)
        dlg = AIReviewDialog(task)
        dlg.show()
        qtbot.addWidget(dlg)
        assert dlg.isVisible() is True

        w.quit_app()

        assert dlg.isVisible() is False


class TestXCloseDoesNotCleanup:
    def test_x_close_does_not_stop_running_ai_worker(
        self, make_window, qtbot, task_service
    ):
        from app.ui.ai_worker import AIReviewWorker

        w = make_window()
        w._tray = _TrayStub()  # 模拟托盘可用
        qtbot.addWidget(w)
        task = task_service.create_task("任务", scheduled_date=w.current_date)
        worker = AIReviewWorker(
            _SlowReviewService(delay=0.3), task, "原因", today=w.current_date, parent=w
        )
        w._ai_workers.append(worker)
        worker.start()
        qtbot.wait(20)
        assert worker.isRunning()

        w.closeEvent(QCloseEvent())  # X

        # X 只隐藏窗口，不打断后台 AI 线程
        assert worker in w._ai_workers
        assert worker.isRunning() is True

        # 清理，避免残留线程
        worker.requestInterruption()
        worker.wait(2000)
        w._ai_workers.remove(worker)

    def test_x_close_keeps_open_ai_dialog(
        self, make_window, qtbot, task_service
    ):
        from app.ui.dialogs import AIReviewDialog

        w = make_window()
        w._tray = _TrayStub()  # 模拟托盘可用
        qtbot.addWidget(w)
        task = task_service.create_task("任务", scheduled_date=w.current_date)
        dlg = AIReviewDialog(task)
        dlg.show()
        qtbot.addWidget(dlg)
        assert dlg.isVisible() is True

        w.closeEvent(QCloseEvent())  # X

        # X 隐藏主窗口，但不关闭 AI 结果对话框
        assert dlg.isVisible() is True
        dlg.close()


@pytest.mark.parametrize("today", ["2026-09-05", None])
def test_subprocess_quit_app_exits_event_loop(tmp_path, today):
    """子进程实测：托盘退出（quit_app）后 app.exec() 正常返回，进程可结束。"""
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "exit.db"
    script = textwrap.dedent(
        f"""
        import os
        import sys
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        sys.path.insert(0, {str(project_root)!r})

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from app.database.connection import get_connection
        from app.database.repository import TaskRepository
        from app.services.date_service import DateService
        from app.services.task_service import TaskService
        from app.ui.main_window import MainWindow

        conn = get_connection({str(db_path)!r})
        repo = TaskRepository(conn)
        ts = TaskService(repo)
        ds = DateService(repo)

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        if {today!r} is not None:
            from app.utils import date_utils
            date_utils.set_today_provider({today!r})

        w = MainWindow(task_service=ts, date_service=ds)
        w.show()
        # 模拟用户点击托盘“退出”
        QTimer.singleShot(200, w.quit_app)

        code = app.exec()
        print("EXEC_RETURN", code)
        sys.exit(0 if code == 0 else 1)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "EXEC_RETURN 0" in result.stdout
