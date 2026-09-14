"""Assessment 跨线程 SQLite 修复测试。

背景：验收出题 / 判题在 QThread 中执行，若复用主线程 sqlite3.Connection
会报 “SQLite objects created in a thread can only be used in that same
thread”。修复：worker 线程内自建独立连接（db_path + service_factory），
结束即关闭。
"""

from __future__ import annotations

import json
import threading

import pytest
from PySide6.QtWidgets import QLabel

from app.database.assessment_repository import AssessmentRepository
from app.database.connection import get_connection
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.review_service import ReviewService
from app.ui.ai_worker import (
    AssessmentWorker,
    run_start_assessment,
    run_submit_answers,
)
from app.ui.main_window import MainWindow

QUESTIONS_CONTENT = json.dumps(
    {"questions": [
        {"question": "解释 autograd", "type": "concept", "expected_points": 3},
        {"question": "写一个最小训练闭环", "type": "coding", "expected_points": 3},
        {"question": "说明 loss 不下降的排查", "type": "debug",
         "expected_points": 3},
        {"question": "怎么用 DataLoader", "type": "scenario",
         "expected_points": 3},
    ]}, ensure_ascii=False,
)
JUDGMENT_CONTENT = json.dumps(
    {"questions": [{"question_index": i, "verdict": "correct", "reason": "ok"}
                    for i in range(4)],
     "weak_points": ["梯度裁剪"], "result_level": "good",
     "mastery_estimate": 0.8},
    ensure_ascii=False,
)


class FakeAI:
    def __init__(self, responses):
        self._responses = list(responses)

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return self._responses.pop(0)


class FailingAI:
    def is_configured(self):
        return True

    def chat(self, *a, **k):
        raise RuntimeError("AI 崩了")


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "thread.db")


@pytest.fixture()
def main_conn(db_path):
    c = get_connection(db_path)
    yield c
    c.close()


def _factory(ai):
    """构造 worker 线程专用 service（在 worker 内用其连接）。"""

    def build(conn):
        assessment_repo = AssessmentRepository(conn)
        review = ReviewService(
            TaskRepository(conn), assessment_repo,
            plan_repo=StudyPlanRepository(conn),
        )
        return AssessmentService(ai, assessment_repo=assessment_repo,
                                 review_service=review)

    return build


def _kp(conn, name="pytorch.autograd"):
    return AssessmentRepository(conn).create_knowledge_point(name)


class RecordingConn:
    """包裹真实 sqlite 连接，用于断言 worker 结束时确实 close 了连接。"""

    def __init__(self, real):
        self._real = real
        self.closed = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        self.closed = True
        self._real.close()


def _run(qtbot, worker, signal_name):
    result = {}
    worker.succeeded.connect(lambda x: result.setdefault("ok", x))
    worker.failed.connect(lambda m: result.setdefault("err", m))
    worker.start()
    qtbot.waitUntil(lambda: bool(result), timeout=10000)
    worker.wait(10000)
    if signal_name == "failed":
        assert "err" in result, "expected failure but worker succeeded"
        return result["err"]
    assert "err" not in result, f"worker failed: {result.get('err')}"
    return result["ok"]


# ================= 线程亲和性 =================

class TestThreadAffinity:
    def test_worker_builds_service_in_worker_thread(
        self, qtbot, main_conn, db_path
    ):
        kp = _kp(main_conn)
        seen = {}
        main_thread = threading.get_ident()

        def build(conn):
            seen["factory_thread"] = threading.get_ident()
            seen["conn_is_main"] = conn is main_conn
            seen["conn"] = conn
            return _factory(FakeAI([QUESTIONS_CONTENT]))(conn)

        worker = AssessmentWorker(
            run_start_assessment, build, db_path=db_path,
            args=(kp["id"],),
        )
        attempt = _run(qtbot, worker, "succeeded")
        assert attempt["judge_status"] == "pending"
        assert seen["factory_thread"] != main_thread
        assert seen["conn_is_main"] is False

    def test_worker_does_not_reuse_main_connection(self, qtbot, main_conn,
                                                   db_path):
        """反例：把主线程连接交给 worker 会触发 SQLite 线程错误。"""
        kp = _kp(main_conn)
        main_service = _factory(FakeAI([QUESTIONS_CONTENT]))(main_conn)
        worker = AssessmentWorker(
            run_start_assessment, lambda conn: main_service, db_path=db_path,
            args=(kp["id"],),
        )
        msg = _run(qtbot, worker, "failed")
        assert "thread" in msg.lower()

    def test_result_delivered_on_main_thread(self, qtbot, main_conn, db_path):
        kp = _kp(main_conn)
        seen = {}
        main_thread = threading.get_ident()
        worker = AssessmentWorker(
            run_start_assessment, _factory(FakeAI([QUESTIONS_CONTENT])),
            db_path=db_path, args=(kp["id"],),
        )
        worker.succeeded.connect(
            lambda attempt: seen.setdefault("thread", threading.get_ident())
        )
        _run(qtbot, worker, "succeeded")
        assert seen["thread"] == main_thread  # 信号在主线程投递

    def test_connection_closed_after_success(self, qtbot, main_conn, db_path,
                                             monkeypatch):
        kp = _kp(main_conn)
        captured = {}

        def build(conn):
            captured["conn"] = conn
            return _factory(FakeAI([QUESTIONS_CONTENT]))(conn)

        def conn_factory(path=None):
            wrapped = RecordingConn(get_connection(path))
            captured.setdefault("wrapped", wrapped)
            return wrapped

        monkeypatch.setattr("app.ui.ai_worker.get_connection", conn_factory)
        worker = AssessmentWorker(
            run_start_assessment, build, db_path=db_path, args=(kp["id"],),
        )
        _run(qtbot, worker, "succeeded")
        assert captured["wrapped"].closed is True

    def test_connection_closed_on_error(self, qtbot, main_conn, db_path,
                                        monkeypatch):
        kp = _kp(main_conn)
        captured = {}

        def build(conn):
            captured["conn"] = conn
            return _factory(FailingAI())(conn)

        def conn_factory(path=None):
            wrapped = RecordingConn(get_connection(path))
            captured.setdefault("wrapped", wrapped)
            return wrapped

        monkeypatch.setattr("app.ui.ai_worker.get_connection", conn_factory)
        worker = AssessmentWorker(
            run_start_assessment, build, db_path=db_path, args=(kp["id"],),
        )
        _run(qtbot, worker, "failed")
        assert captured["wrapped"].closed is True


# ================= 出题 / pending =================

class TestStartAssessment:
    def test_questions_generated_and_pending_saved(self, qtbot, main_conn,
                                                   db_path):
        kp = _kp(main_conn)
        worker = AssessmentWorker(
            run_start_assessment, _factory(FakeAI([QUESTIONS_CONTENT])),
            db_path=db_path, args=(kp["id"],),
        )
        attempt = _run(qtbot, worker, "succeeded")
        assert len(attempt["questions"]) == 4
        # 落在独立连接上，主连接可见
        saved = AssessmentRepository(main_conn).get_attempt(attempt["id"])
        assert saved is not None and saved["judge_status"] == "pending"

    def test_missing_kp_reports_error(self, qtbot, main_conn, db_path):
        worker = AssessmentWorker(
            run_start_assessment, _factory(FakeAI([QUESTIONS_CONTENT])),
            db_path=db_path, args=(999999,),
        )
        msg = _run(qtbot, worker, "failed")
        assert "知识点不存在" in msg


# ================= 提交判题 / mastery / schedule =================

class TestSubmitAnswers:
    def _pending(self, qtbot, main_conn, db_path, ai=None):
        kp = _kp(main_conn)
        w = AssessmentWorker(
            run_start_assessment, _factory(ai or FakeAI([QUESTIONS_CONTENT])),
            db_path=db_path, args=(kp["id"],),
        )
        return _run(qtbot, w, "succeeded"), kp

    def test_judged_and_mastery_and_schedule(self, qtbot, main_conn, db_path):
        attempt, kp = self._pending(qtbot, main_conn, db_path)
        w = AssessmentWorker(
            run_submit_answers,
            _factory(FakeAI([JUDGMENT_CONTENT])),
            db_path=db_path,
            args=(attempt["id"], ["a", "b", "c", "d"]),
            kwargs={"today": "2026-09-14"},
        )
        judged = _run(qtbot, w, "succeeded")
        assert judged["judge_status"] == "judged"
        assert judged["result_level"] == "good"
        arepo = AssessmentRepository(main_conn)
        saved = arepo.get_attempt(attempt["id"])
        assert saved["ai_result_json"]
        kp_after = arepo.get_knowledge_point(kp["id"])
        assert kp_after["mastery_estimate"] == pytest.approx(0.8)
        assert kp_after["last_assessed_at"] is not None
        assert kp_after["next_review_date"] is not None

    def test_ai_failure_keeps_answers_no_mastery(self, qtbot, main_conn,
                                                 db_path):
        attempt, kp = self._pending(qtbot, main_conn, db_path)
        w = AssessmentWorker(
            run_submit_answers, _factory(FailingAI()), db_path=db_path,
            args=(attempt["id"], ["a", "b", "c", "d"]),
        )
        # submit_answers 内部捕获 AI 错误并标记 failed（不抛异常）
        result = _run(qtbot, w, "succeeded")
        assert result["judge_status"] == "failed"
        arepo = AssessmentRepository(main_conn)
        assert arepo.get_knowledge_point(kp["id"])["mastery_estimate"] == 0.0

    def test_two_sequential_assessments(self, qtbot, main_conn, db_path):
        for i in range(2):
            kp = _kp(main_conn, f"kp{i}")
            w = AssessmentWorker(
                run_start_assessment,
                _factory(FakeAI([QUESTIONS_CONTENT])),
                db_path=db_path, args=(kp["id"],),
            )
            attempt = _run(qtbot, w, "succeeded")
            w2 = AssessmentWorker(
                run_submit_answers,
                _factory(FakeAI([JUDGMENT_CONTENT])),
                db_path=db_path,
                args=(attempt["id"], ["a", "b", "c", "d"]),
            )
            assert _run(qtbot, w2, "succeeded")["judge_status"] == "judged"
        assert len(AssessmentRepository(main_conn).list_attempts()) == 2


# ================= MainWindow 集成 =================

def _build_window(qtbot, main_conn, db_path, ai, monkeypatch, dialog_recorder):
    from app.ui import main_window as mw

    from PySide6.QtCore import QObject as _QObject, Signal as _Signal

    class DummyDialog(_QObject):
        assessment_completed = _Signal(int)

        def __init__(self, service, attempt, today, parent=None, **kwargs):
            super().__init__()
            dialog_recorder.append(attempt)

        def exec(self):
            return 0

    monkeypatch.setattr(mw, "AssessmentDialog", DummyDialog)
    repo = TaskRepository(main_conn)
    from app.services.task_service import TaskService
    from app.services.date_service import DateService

    return MainWindow(
        task_service=TaskService(repo),
        date_service=DateService(repo),
        today_provider=lambda: "2026-09-14",
        assessment_service=_factory(ai)(main_conn),
        assessment_repo=AssessmentRepository(main_conn),
        assessment_service_factory=_factory(ai),
        db_path=db_path,
    )


class TestMainWindowIntegration:
    def test_click_start_assessment_no_thread_error(
        self, qtbot, main_conn, db_path, monkeypatch
    ):
        kp = _kp(main_conn)
        repo = TaskRepository(main_conn)
        task = repo.create(title="t", scheduled_date="2026-09-14",
                           source="generated", knowledge_point_id=kp["id"])
        recorded = []
        w = _build_window(qtbot, main_conn, db_path,
                          FakeAI([QUESTIONS_CONTENT]), monkeypatch, recorded)
        qtbot.addWidget(w)
        w._on_start_assessment(task.id)
        qtbot.waitUntil(lambda: len(recorded) == 1, timeout=10000)
        assert "验收启动失败" not in (w.statusBar().currentMessage() or "")
        assert len(AssessmentRepository(main_conn).list_attempts()) == 1

    def test_double_click_creates_one_attempt(
        self, qtbot, main_conn, db_path, monkeypatch
    ):
        kp = _kp(main_conn)
        repo = TaskRepository(main_conn)
        task = repo.create(title="t", scheduled_date="2026-09-14",
                           source="generated", knowledge_point_id=kp["id"])
        recorded = []
        w = _build_window(qtbot, main_conn, db_path,
                          FakeAI([QUESTIONS_CONTENT]), monkeypatch, recorded)
        qtbot.addWidget(w)
        w._on_start_assessment(task.id)
        w._on_start_assessment(task.id)  # 连续双击
        qtbot.waitUntil(lambda: len(recorded) >= 1, timeout=10000)
        assert len(AssessmentRepository(main_conn).list_attempts()) == 1

    def test_factory_error_shows_message_no_crash(
        self, qtbot, main_conn, db_path, monkeypatch
    ):
        kp = _kp(main_conn)
        repo = TaskRepository(main_conn)
        task = repo.create(title="t", scheduled_date="2026-09-14",
                           source="generated", knowledge_point_id=kp["id"])

        def boom(conn):
            raise RuntimeError("DB 坏了")

        recorded = []
        w = _build_window(qtbot, main_conn, db_path,
                          FakeAI([QUESTIONS_CONTENT]), monkeypatch, recorded)
        w.assessment_service_factory = boom
        qtbot.addWidget(w)
        w._on_start_assessment(task.id)
        qtbot.waitUntil(
            lambda: "验收启动失败" in (w.statusBar().currentMessage() or ""),
            timeout=10000,
        )

    def test_done_new_and_extra_assessable(self, qtbot, main_conn, db_path,
                                           monkeypatch):
        repo = TaskRepository(main_conn)
        kp1 = _kp(main_conn, "done.new")
        done_task = repo.create(title="done", scheduled_date="2026-09-14",
                                source="generated",
                                knowledge_point_id=kp1["id"])
        repo.mark_done(done_task.id)
        kp2 = _kp(main_conn, "extra.one")
        extra = repo.create(title="extra", scheduled_date="2026-09-14",
                            source="extra", task_type="extra",
                            knowledge_point_id=kp2["id"])
        recorded = []
        w = _build_window(qtbot, main_conn, db_path,
                          FakeAI([QUESTIONS_CONTENT, QUESTIONS_CONTENT]),
                          monkeypatch, recorded)
        qtbot.addWidget(w)
        w._on_start_assessment(done_task.id)
        qtbot.waitUntil(lambda: len(recorded) >= 1, timeout=10000)
        w._on_start_assessment(extra.id)
        qtbot.waitUntil(lambda: len(recorded) >= 2, timeout=10000)
        assert len(recorded) == 2
