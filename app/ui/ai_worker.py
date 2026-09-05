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
