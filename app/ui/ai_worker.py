"""AI 异步调用 Worker。

用 QThread 在后台调用 AI，避免阻塞 GUI 主线程。
- 成功：发出 result_ready(TaskReview)
- 失败：发出 review_failed(str 错误信息)

无论成功失败，都不会让 GUI 崩溃。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from ..database.connection import get_connection
from ..database.repository import Task
from ..services.task_review_service import TaskReviewService


class OAuthLoginWorker(QThread):
    """Run pi-ai OAuth interactively without blocking the GUI thread."""

    succeeded = Signal(object)
    failed = Signal(str)
    auth_event = Signal(object)
    prompt_requested = Signal(object)

    def __init__(self, provider_id: str, parent=None):
        super().__init__(parent)
        self.provider_id = provider_id
        self._prompt_lock = threading.Lock()
        self._prompt_event = threading.Event()
        self._prompt_answer = None
        self._prompt_cancelled = False
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self.requestInterruption()
        self._cancel_event.set()
        self.answer_prompt(None)

    def answer_prompt(self, value: str | None) -> None:
        with self._prompt_lock:
            self._prompt_answer = value or ""
            self._prompt_cancelled = value is None
            self._prompt_event.set()

    def _request_prompt(self, event: dict) -> str:
        self._prompt_event.clear()
        self._prompt_answer = None
        self._prompt_cancelled = False
        self.prompt_requested.emit(event)
        while not self._prompt_event.wait(0.2):
            if self.isInterruptionRequested():
                raise RuntimeError("登录已取消")
        with self._prompt_lock:
            if self._prompt_cancelled:
                raise RuntimeError("登录已取消")
            return self._prompt_answer or ""

    def run(self) -> None:
        try:
            from ..ai.pi_ai_bridge import PiAIBridge
            result = PiAIBridge().login(
                self.provider_id,
                on_event=self.auth_event.emit,
                on_prompt=self._request_prompt,
                timeout=600,
                cancel_event=self._cancel_event,
            )
            self.succeeded.emit(result["credential"])
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class AIConnectionTestWorker(QThread):
    """后台测试 AI 连接（只接收普通 base_url / model / api_key）。

    线程安全铁律：**不接收 SQLite repository / connection / service**，
    因此不会出现 “SQLite objects created in a thread can only be used in
    that same thread”。API Key 只存在于内存，绝不打印 / 回显。
    """

    succeeded = Signal(object)  # ConnectionTestResult

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 10.0,
        parent=None,
        *,
        oauth_provider: str = "",
        oauth_credential: dict | None = None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._oauth_provider = oauth_provider
        self._oauth_credential = oauth_credential

    def run(self) -> None:  # noqa: D102
        from ..ai.config_service import ConnectionTestResult, run_connection_test

        try:
            if self._oauth_provider and self._oauth_credential:
                import time
                from ..ai.pi_ai_bridge import PiAIBridge
                started = time.monotonic()
                response = PiAIBridge().complete(
                    provider=self._oauth_provider, model=self._model,
                    credential=self._oauth_credential,
                    messages=[{"role": "user", "content": "Reply with OK."}],
                    temperature=0, max_tokens=5,
                )
                result = ConnectionTestResult(
                    ok=bool((response.get("content") or "").strip()),
                    message="连接成功" if (response.get("content") or "").strip() else "模型未返回文本",
                    model=self._model,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    source=(response.get("content") or "").strip()[:50],
                    oauth_credential=response.get("credential"),
                )
            else:
                result = run_connection_test(
                    self._base_url,
                    self._model,
                    self._api_key,
                    timeout=self._timeout,
                )
        except Exception as e:  # noqa: BLE001 - 任何异常都转为一条结果
            from ..ai.client import sanitize_text
            from ..ai.config_service import ConnectionTestResult, classify_connection_error

            result = ConnectionTestResult(
                ok=False,
                message=classify_connection_error(
                    sanitize_text(str(e), self._api_key)
                ),
                model=self._model,
            )
        self.succeeded.emit(result)


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


class AgentTurnWorker(QThread):
    """Agent turn worker; creates and owns its SQLite connection in ``run``.

    Only serializable/session identifiers, a database path and a pure dependency
    factory cross the GUI-thread boundary. Runtime, Services, Repositories and
    SQLite connection are all constructed and used in this worker thread.
    """

    succeeded = Signal(object)
    failed = Signal(str)
    extraction_requested = Signal(object)

    def __init__(self, db_path, runtime_factory, session_id: int, user_text: str,
                 parent=None, *, capture_memory_consent=False):
        super().__init__(parent)
        self._db_path = db_path
        self._runtime_factory = runtime_factory
        self._session_id = int(session_id)
        self._user_text = user_text
        self._capture_memory_consent = capture_memory_consent
        self.consent_at_turn_start = False
        self.consent_revision_at_turn_start = ""

    def _request_extraction(self, result):
        from ..agent.memory_extraction import CompletedMemoryExtractionTurn, log_extraction_skip
        try:
            if self.consent_at_turn_start and not self.isInterruptionRequested():
                self.extraction_requested.emit(CompletedMemoryExtractionTurn.from_result(
                    result, self.consent_at_turn_start, self.consent_revision_at_turn_start,
                ))
        except Exception as error:
            log_extraction_skip("completed_event_unavailable", error)

    def run(self) -> None:  # noqa: D102
        from ..agent.errors import map_agent_error

        conn = runtime = None
        before_count = None
        try:
            conn = get_connection(self._db_path)
            if self._capture_memory_consent:
                from ..agent.memory_extraction import capture_consent_snapshot
                snapshot = capture_consent_snapshot(conn)
                self.consent_at_turn_start = snapshot.allowed
                self.consent_revision_at_turn_start = snapshot.revision
            runtime = self._runtime_factory(conn)
            service = getattr(runtime, "session_service", None)
            if service is not None:
                before_count = service.count_messages(self._session_id)
            result = runtime.send_message(self._session_id, self._user_text)
            self.succeeded.emit(result)  # normal answer delivery precedes scheduling
            self._request_extraction(result)
        except Exception as error:  # noqa: BLE001 - no raw exception/secrets to the UI
            persisted = False
            configured = None
            try:
                service = getattr(runtime, "session_service", None)
                if service is not None and before_count is not None:
                    persisted = service.count_messages(self._session_id) > before_count
                model = getattr(runtime, "model_client", None)
                if model is not None:
                    configured = bool(model.is_configured())
            except Exception:
                pass
            self.failed.emit(map_agent_error(
                error, model_configured=configured, user_message_persisted=persisted,
            ).message)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - cleanup must not crash worker
                    pass


class AIRouteBuilderWorker(QThread):
    """后台生成学习路线草稿（Phase F）。

    只持有 AIRouteBuilderService（AI client 只是 HTTP/config）+ 纯数据 context，
    **不使用任何 SQLite 连接 / repository**，因此线程安全。
    成功后由主线程打开 Preview，用户确认后才写库。
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, service, context: dict, route_skills=None,
                 market=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._context = context
        self._route_skills = route_skills or []
        self._market = market

    def run(self) -> None:  # noqa: D102
        try:
            draft = self._service.build_draft(
                self._context, route_skills=self._route_skills,
                market=self._market,
            )
            self.succeeded.emit(draft)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
