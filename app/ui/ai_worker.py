"""AI 异步调用 Worker。

用 QThread 在后台调用 AI，避免阻塞 GUI 主线程。
- 成功：发出 result_ready(TaskReview)
- 失败：发出 review_failed(str 错误信息)

无论成功失败，都不会让 GUI 崩溃。
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..database.connection import get_connection
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
    """验收后台 worker：在子线程内自建 SQLite 连接执行验收出题 / 判题。

    线程安全铁律：
    - SQLite 连接在哪个线程创建，就只在哪个线程使用；
    - 因此本 worker **绝不**使用主线程传入的 connection / repository /
      service（否则会报 “SQLite objects created in a thread can only be used
      in that same thread”）；
    - 它只接收 db_path + service_factory：在 run() 内 get_connection() ->
      factory(conn) 构造本线程专用的 repository/service -> 执行 -> 关闭连接。
    - AI client 只是 HTTP/config（不持有连接 / Qt 对象），可安全共享。

    成功：发 succeeded(object)；失败：发 failed(str 错误信息)。
    任何异常都不会让 GUI 崩溃。
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        operation,
        service_factory,
        db_path=None,
        args=(),
        kwargs=None,
        parent=None,
    ):
        """:param operation: (service, *args, **kwargs) -> result 的可调用对象
        :param service_factory: (conn) -> AssessmentService，在线程内构造
        :param db_path: 数据库路径（仅传路径，不传连接）
        """
        super().__init__(parent)
        self._operation = operation
        self._service_factory = service_factory
        self._db_path = db_path
        self._args = args
        self._kwargs = kwargs or {}

    def run(self) -> None:  # noqa: D102
        conn = None
        try:
            conn = get_connection(self._db_path)
            service = self._service_factory(conn)
            result = self._operation(service, *self._args, **self._kwargs)
            self.succeeded.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响结果
                    pass


def run_start_assessment(
    service, knowledge_point_id, task_id=None, num_questions=4
):
    """AssessmentWorker 操作：生成验收题并落一条 pending attempt。"""
    return service.start_assessment(
        knowledge_point_id, task_id=task_id, num_questions=num_questions
    )


def run_submit_answers(service, attempt_id, answers, today=None):
    """AssessmentWorker 操作：提交答案 + AI 判题 + 回写知识点。"""
    return service.submit_answers(attempt_id, answers, today=today)
