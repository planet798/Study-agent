"""主窗口关闭 = 退出程序的验收测试（问题二）。

覆盖：
- 点 X / 托盘“退出”触发的 closeEvent 接受关闭、_quit_requested 置位
- 关闭时托盘被清理（不驻留后台）
- 关闭时运行的 AI worker 被安全等待（join），不残留、不 “destroyed while running”
- 关闭时打开的 AI 对话框被一并关闭（不残留顶级窗口）
- QApplication 默认最后一个窗口关闭即退出
- 子进程实测：关闭主窗口后事件循环 exec() 正常返回，进程可结束
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture()
def window(make_window, qtbot):
    w = make_window()
    qtbot.addWidget(w)
    return w


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


class TestCloseWindowExits:
    def test_app_quits_on_last_window_closed_by_default(self):
        """退出策略依赖 QApplication 默认行为：最后一个窗口关闭即退出。"""
        app = QApplication.instance()
        assert app is not None
        assert app.quitOnLastWindowClosed() is True

    def test_close_event_accepts_and_sets_quit_flag(self, window):
        w = window
        ev = QCloseEvent()
        w.closeEvent(ev)
        assert ev.isAccepted() is True
        assert w._quit_requested is True

    def test_close_cleans_tray(self, make_window):
        w = make_window()

        class _TrayStub:
            def hide(self):
                self.hidden = True

            def deleteLater(self):
                self.deleted = True

        tray = _TrayStub()
        w._tray = tray
        w.close()
        assert tray.hidden is True
        assert tray.deleted is True
        assert w._tray is None

    def test_close_joins_running_ai_worker(self, window, task_service):
        from app.ui.ai_worker import AIReviewWorker

        w = window
        task = task_service.create_task("任务", scheduled_date=w.current_date)
        svc = _SlowReviewService(delay=0.3)
        worker = AIReviewWorker(svc, task, "原因", today=w.current_date, parent=w)
        w._ai_workers.append(worker)
        worker.start()
        assert worker.isRunning()

        w.close()

        # worker 线程已被安全 join、从列表移除，无残留
        assert worker.isFinished() is True
        assert w._ai_workers == []

    def test_close_closes_open_ai_dialog(self, window):
        from app.ui.dialogs import AIReviewDialog

        w = window
        task = w.task_service.create_task("任务", scheduled_date=w.current_date)
        dlg = AIReviewDialog(task)
        dlg.show()
        assert dlg.isVisible() is True

        w.close()

        assert dlg.isVisible() is False

    def test_quit_app_tray_path_closes(self, window):
        w = window
        w.quit_app()
        assert w.isVisible() is False
        assert w._quit_requested is True


@pytest.mark.parametrize("today", ["2026-09-05", None])
def test_subprocess_close_window_exits_event_loop(tmp_path, today):
    """子进程实测：关闭主窗口后 app.exec() 正常返回，进程可正常结束。"""
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
        if {today!r} is not None:
            from app.utils import date_utils
            date_utils.set_today_provider({today!r})

        w = MainWindow(task_service=ts, date_service=ds)
        w.show()
        # 模拟用户稍后点 X
        QTimer.singleShot(200, w.close)

        code = app.exec()
        # 若关闭窗口后事件循环退出，说明进程可以正常结束
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
