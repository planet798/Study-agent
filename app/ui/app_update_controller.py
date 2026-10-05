"""更新状态与线程生命周期；网络 worker 不持有 UI 或数据库。"""
from __future__ import annotations

from pathlib import Path
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from app.runtime_paths import hidden_process_options
from app.services.app_update_service import AppUpdateService
from app.updates.jobs import prepare_job, read_record, write_record
from app.updates.models import UpdateCancelled, UpdateError, UpdateState


class UpdateWorker(QThread):
    progress = Signal(object, object)
    verifying = Signal()

    def __init__(self, operation, service, payload=None, parent=None):
        super().__init__(parent)
        self.operation, self.service, self.payload = operation, service, payload
        self.cancelled = threading.Event()
        self.result = None
        self.error = None
        self.was_cancelled = False

    def run(self):
        try:
            if self.operation == "check":
                self.result = self.service.check(self.cancelled.is_set)
            elif self.operation == "download":
                self.result = self.service.download(
                    self.payload, cancel=self.cancelled.is_set,
                    progress=self.progress.emit, verifying=self.verifying.emit,
                )
            else:
                self.result = prepare_job(self.payload)
        except UpdateCancelled:
            self.was_cancelled = True
        except Exception as error:
            self.error = str(error) if isinstance(error, UpdateError) else "更新操作失败，请检查网络、磁盘空间和目录权限后重试。"


class AppUpdateController(QObject):
    changed = Signal()
    progress = Signal(object, object)
    ready_to_exit = Signal()
    preparation_failed = Signal()

    def __init__(self, service=None, parent=None, *, launch=subprocess.Popen):
        super().__init__(parent)
        self.service = service or AppUpdateService()
        self.launch = launch
        self.state = UpdateState.IDLE
        self.message = "点击检查更新以获取正式版本。"
        self.release = self.downloaded = None
        self.worker = None
        self.job = None
        self.process = None
        self._stopping = self._committed = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll_helper)

    def _set(self, state, message):
        self.state, self.message = state, message
        self.changed.emit()

    def _start(self, operation, payload=None):
        if self._stopping or self.worker is not None or self.state == UpdateState.PREPARING:
            return
        state = {"check": UpdateState.CHECKING, "download": UpdateState.DOWNLOADING,
                 "prepare": UpdateState.PREPARING}[operation]
        self._set(state, {"check": "正在检查正式版本…", "download": "正在下载更新…",
                          "prepare": "正在准备升级，应用即将退出…"}[operation])
        self.worker = UpdateWorker(operation, self.service, payload, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.verifying.connect(self._on_verifying)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def check(self):
        self._start("check")

    def download(self):
        if self.release is not None:
            self._start("download", self.release)

    def prepare(self):
        if self.downloaded is not None:
            self._start("prepare", self.downloaded)

    def cancel(self):
        if self.worker is not None:
            self.worker.cancelled.set()

    def _on_progress(self, current, total):
        if not self._stopping:
            self.progress.emit(current, total)

    def _on_verifying(self):
        if not self._stopping:
            self._set(UpdateState.VERIFYING, "正在校验安装包 SHA256…")

    def _finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        worker.deleteLater()
        if self._stopping:
            # 退出可能发生在 prepare_job 完成之前，必须撤回尚未启动的任务。
            if worker.operation == "prepare" and worker.result:
                self.job = worker.result
                self._abort()
            return
        if worker.error:
            self._set(UpdateState.FAILED, worker.error)
            if worker.operation == "prepare":
                self.preparation_failed.emit()
        elif worker.was_cancelled:
            self._set(UpdateState.AVAILABLE if self.release else UpdateState.IDLE, "下载已取消。")
        elif worker.operation == "check":
            self.release = worker.result
            self.downloaded = None
            self._set(UpdateState.AVAILABLE if self.release else UpdateState.CURRENT,
                      f"发现新版本 {self.release.version}。" if self.release else "当前已是最新正式版本。")
        elif worker.operation == "download":
            self.downloaded = worker.result
            self._set(UpdateState.READY, "安装包已校验。安装将退出应用并在完成后重新启动。")
        else:
            self.job = Path(worker.result)
            try:
                self.process = self.launch([str(self.job.parent / "StudyAgentUpdater.exe"), str(self.job)],
                                           cwd=str(self.job.parent), **hidden_process_options())
                self._deadline = time.monotonic() + 20
                self._timer.start()
            except OSError:
                self.fail_preparation("无法启动升级辅助程序，应用保持运行。")

    def _poll_helper(self):
        if self._stopping or self.job is None:
            return
        try:
            ready = self.job.parent / "ready.json"
            failure = self.job.parent / "result.json"
            # 辅助程序可能写过 ready 后失败，优先检查失败/退出。
            if failure.exists() or self.process.poll() is not None:
                self.fail_preparation("升级辅助程序准备失败，应用保持运行。请检查日志或手动安装。")
            elif ready.exists() and read_record(ready).get("ready") is True:
                self._timer.stop()
                self.ready_to_exit.emit()
            elif time.monotonic() >= self._deadline:
                self.fail_preparation("升级辅助程序准备超时，应用保持运行。")
        except (OSError, ValueError, UpdateError):
            self.fail_preparation("无法读取升级准备结果，应用保持运行。")

    def commit_exit(self):
        if self.job is None:
            raise UpdateError("升级任务尚未就绪。")
        write_record(self.job.parent / "commit.json", {"commit": True})
        self._committed = True

    def _abort(self):
        if self.job is not None and not self._committed:
            try:
                write_record(self.job.parent / "abort.json", {"abort": True})
            except OSError:
                pass

    def fail_preparation(self, message):
        self._timer.stop()
        self._abort()
        self._set(UpdateState.READY if self.downloaded else UpdateState.FAILED, message)
        self.preparation_failed.emit()

    def stop(self):
        self._stopping = True
        self._timer.stop()
        self.cancel()
        if self.worker is not None:
            worker = self.worker
            worker.wait()
            if worker.operation == "prepare" and worker.result:
                self.job = Path(worker.result)
            self.worker = None
            worker.deleteLater()
        self._abort()
