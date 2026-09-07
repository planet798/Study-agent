"""AI 异步调用 Worker。

用 QThread 在后台调用 AI，避免阻塞 GUI 主线程。
- 成功：发出 result_ready(TaskReview)
- 失败：发出 review_failed(str 错误信息)

无论成功失败，都不会让 GUI 崩溃。
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..database.repository import Task
from ..services.task_review_service import TaskReviewService


class AIReviewWorker(QThread):
    """在子线程中调用 TaskReviewService 的 QThread。"""

    result_ready = Signal(object)
    review_failed = Signal(str)

    def __init__(
        self,
        review_service: TaskReviewService,
        task: Task,
        reason: str,
        today: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._service = review_service
        self._task = task
        self._reason = reason
        self._today = today

    def run(self) -> None:  # noqa: D102
        try:
            review = self._service.review_task(
                self._task, self._reason, today=self._today
            )
            self.result_ready.emit(review)
        except Exception as e:  # noqa: BLE001 - 任何异常都转为消息信号
            self.review_failed.emit(str(e))


class AssessmentWorker(QThread):
    """通用 AI 任务 worker：在子线程执行任意可调用函数（验收出题/判题等）。

    成功：发 succeeded(object)；失败：发 failed(str 错误信息)。
    任何异常都不会让 GUI 崩溃。
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        func,
        args=(),
        kwargs=None,
        parent=None,
    ):
        super().__init__(parent)
        self._func = func
        self._args = args
        self._kwargs = kwargs or {}

    def run(self) -> None:  # noqa: D102
        try:
            result = self._func(*self._args, **self._kwargs)
            self.succeeded.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
