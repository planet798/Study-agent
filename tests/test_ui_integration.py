"""今日页 UI 整合（Phase 7）测试。

覆盖：
- 今日三类任务（新知识 / 复习 / 额外）分区显示
- 复习/额外任务卡片出现“开始验收”入口
- 验收入口连接到 Phase 3C/3D（start_assessment -> 弹出验收对话框）
- 额外任务生成按钮调用 ExtraTaskService
- 课外探索展示 + 打开链接（URL 只能来自已验证集合）
- 空状态（暂无可复习内容 / 暂无匹配资源 / 全局空提示）
- 托盘行为不回归（X 隐藏、退出清理）
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent

from app.database.assessment_repository import AssessmentRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.exploration_service import ExplorationService
from app.services.extra_task_service import ExtraTaskService
from app.services.review_service import ReviewService
from app.services.study_plan_service import StudyPlanService
from app.ui.assessment_dialog import AssessmentDialog
from app.ui.main_window import MainWindow

TODAY = "2026-09-06"


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def sps(repo, conn):
    svc = StudyPlanService(repo, StudyPlanRepository(conn))
    svc.ensure_default_plan()
    return svc


class FakeQuestionAI:
    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return json.dumps(
            {"questions": [
                {"question": "解释 autograd", "type": "concept",
                 "expected_points": 3},
            ]},
            ensure_ascii=False,
        )


def _build_window(qtbot, repo, task_service, date_service, **services):
    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        today_provider=lambda: TODAY,
        **services,
    )
    qtbot.addWidget(w)
    return w


def _labels_in_list(window) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [w.text() for w in window.list_container.findChildren(QLabel) if w.text()]


def _buttons_in_list(window) -> list:
    return window.list_container.findChildren(
        pytest.importorskip("PySide6.QtWidgets").QPushButton
    )


def _find_button(window, text: str):
    from PySide6.QtWidgets import QPushButton

    for w in window.list_container.findChildren(QPushButton):
        if w.text() == text:
            return w
    return None


def _find_label(window, text: str) -> bool:
    return any(text == t or text in t for t in _labels_in_list(window))


class TestSections:
    def test_three_task_sections_and_assessment_buttons(
        self, qtbot, repo, task_service, date_service, conn
    ):
        assessment_repo = AssessmentRepository(conn)
        review_scheduler = ReviewService(repo, assessment_repo)
        extra_service = ExtraTaskService(
            repo, study_plan_service=None, assessment_repo=assessment_repo,
        )
        exploration = ExplorationService()

        # 造三类任务：新知识（无 kp）、复习（kp）、额外（kp）
        new_t = task_service.create_task("新知识任务", scheduled_date=TODAY)
        kp = assessment_repo.create_knowledge_point("pytorch.autograd")
        review_t = repo.create(
            title="复习 pytorch.autograd", scheduled_date=TODAY,
            source="review", task_type="review", knowledge_point_id=kp["id"],
        )
        extra_t = repo.create(
            title="【额外·实践】autograd", scheduled_date=TODAY,
            category="额外学习", source="extra", task_type="extra",
            knowledge_point_id=kp["id"], difficulty="practice",
        )

        w = _build_window(
            qtbot, repo, task_service, date_service,
            review_scheduler=review_scheduler,
            extra_service=extra_service,
            exploration_service=exploration,
            assessment_repo=assessment_repo,
        )

        # 四个区域标题都出现
        assert _find_label(w, "今日新知识")
        assert _find_label(w, "今日复习")
        assert _find_label(w, "额外学习")
        assert _find_label(w, "课外探索")

        # 三个任务都在 _task_widgets
        ids = {wd.task().id for wd in w._task_widgets}
        assert {new_t.id, review_t.id, extra_t.id} <= ids

        # 复习/额外任务卡片带“开始验收”，新知识不带
        for wd in w._task_widgets:
            if wd.task().id == review_t.id:
                assert hasattr(wd, "assessment_btn")
            elif wd.task().id == extra_t.id:
                assert hasattr(wd, "assessment_btn")
            elif wd.task().id == new_t.id:
                assert not hasattr(wd, "assessment_btn")


class TestExtraRow:
    def test_extra_serial_button_calls_service_and_refreshes(
        self, qtbot, repo, task_service, date_service, conn, monkeypatch
    ):
        assessment_repo = AssessmentRepository(conn)
        extra_service = ExtraTaskService(
            repo, study_plan_service=None, assessment_repo=assessment_repo,
        )
        w = _build_window(
            qtbot, repo, task_service, date_service, extra_service=extra_service,
        )
        # 配额提示展示
        assert "剩余额度" in " ".join(_labels_in_list(w))

        called = {}
        monkeypatch.setattr(
            extra_service, "generate_extra_tasks",
            lambda today=None, difficulty="practice": called.update(
                n=1) or {"created": [1], "skipped_duplicate": [],
                          "skipped_budget": []},
        )
        btn = _find_button(w, "继续学习 / 生成额外任务")
        assert btn is not None
        qtbot.mouseClick(btn, QtMouseButton())
        assert called.get("n") == 1
        assert "额外任务" in w.statusBar().currentMessage() or \
            "额外" in w.statusBar().currentMessage()


def QtMouseButton():
    from PySide6.QtCore import Qt

    return Qt.MouseButton.LeftButton


class TestExploration:
    def test_exploration_display_and_open_verified_url(
        self, qtbot, repo, task_service, date_service, conn, monkeypatch
    ):
        sps = StudyPlanService(repo, StudyPlanRepository(conn))
        sps.ensure_default_plan()
        assessment_repo = AssessmentRepository(conn)
        exploration = ExplorationService()
        w = _build_window(
            qtbot, repo, task_service, date_service,
            study_plan_service=sps,
            assessment_repo=assessment_repo,
            exploration_service=exploration,
        )

        opened = []
        monkeypatch.setattr(
            "app.ui.main_window.QDesktopServices.openUrl",
            lambda url: opened.append(url.toString()) or True,
        )
        # 课外探索区至少出现一条卡片
        btn = _find_button(w, "打开链接")
        assert btn is not None
        verified = {r["url"] for r in exploration.resources}
        qtbot.mouseClick(btn, QtMouseButton())
        assert opened
        assert opened[0] in verified


class TestEmptyStates:
    def test_no_review_shows_hint(self, qtbot, repo, task_service, date_service,
                                  conn):
        assessment_repo = AssessmentRepository(conn)
        review_scheduler = ReviewService(repo, assessment_repo)
        task_service.create_task("只有新知识", scheduled_date=TODAY)
        w = _build_window(
            qtbot, repo, task_service, date_service,
            review_scheduler=review_scheduler,
        )
        assert _find_label(w, "暂无可复习内容")

    def test_no_tasks_global_empty(self, qtbot, repo, task_service, date_service):
        w = _build_window(qtbot, repo, task_service, date_service)
        assert w.empty_hint.isHidden() is False  # 无任务时全局空提示
        assert w.scroll.isHidden() is True


class TestAssessmentEntry:
    def test_start_assessment_opens_dialog_with_attempt(
        self, qtbot, repo, task_service, date_service, conn, monkeypatch
    ):
        assessment_repo = AssessmentRepository(conn)
        kp = assessment_repo.create_knowledge_point("pytorch.autograd")
        assessment_repo.update_knowledge_point(kp["id"], last_assessed_at="2026-09-05T10:00:00", mastery_estimate=0.4, review_count=1)
        t = repo.create(
            title="复习 pytorch.autograd", scheduled_date=TODAY,
            source="review", task_type="review", knowledge_point_id=kp["id"],
        )
        review_scheduler = ReviewService(repo, assessment_repo)
        assessment_service = AssessmentService(
            FakeQuestionAI(), assessment_repo=assessment_repo,
            review_service=review_scheduler,
        )
        w = _build_window(
            qtbot, repo, task_service, date_service,
            assessment_service=assessment_service,
            assessment_repo=assessment_repo,
        )

        captured = {}

        class DummyWorker(QObject):
            succeeded = Signal(object)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, operation, service_factory, db_path=None,
                         args=(), kwargs=None, parent=None):
                super().__init__()
                self._op = operation
                self._factory = service_factory
                self._args = args
                self._kwargs = kwargs or {}

            def start(self):
                # 测试单线程：回退工厂忽略连接，返回主线程 service
                service = self._factory(None)
                self.succeeded.emit(
                    self._op(service, *self._args, **self._kwargs)
                )
                self.finished.emit()

            def isRunning(self):  # noqa: D102
                return False

            def requestInterruption(self):  # noqa: D102
                pass

            def wait(self, *a):  # noqa: D102
                return True

        class DummyDialog(QObject):
            assessment_completed = Signal(int)

            def __init__(self, service, attempt, today, parent=None, **kwargs):
                super().__init__()
                captured["attempt"] = attempt
                captured["today"] = today

            def exec(self):
                return 0

        monkeypatch.setattr("app.ui.main_window.AssessmentWorker", DummyWorker)
        monkeypatch.setattr("app.ui.main_window.AssessmentDialog", DummyDialog)

        w._on_start_assessment(t.id)

        assert captured.get("attempt") is not None
        assert captured["attempt"]["task_id"] == t.id
        assert captured["attempt"]["knowledge_point_id"] == kp["id"]
        assert captured["today"] == TODAY

    def test_start_assessment_without_kp_shows_message(
        self, qtbot, repo, task_service, date_service, conn
    ):
        assessment_repo = AssessmentRepository(conn)
        assessment_service = AssessmentService(
            FakeQuestionAI(), assessment_repo=assessment_repo,
        )
        t = task_service.create_task("无知识点任务", scheduled_date=TODAY)
        w = _build_window(
            qtbot, repo, task_service, date_service,
            assessment_service=assessment_service,
            assessment_repo=assessment_repo,
        )
        w._on_start_assessment(t.id)
        assert "暂不支持验收" in w.statusBar().currentMessage()


class TestAssessmentDialog:
    class _FakeService:
        def submit_answers(self, attempt_id, answers, today=None):
            return {
                "id": attempt_id,
                "task_id": 5,
                "knowledge_point_id": 1,
                "questions_json": "",
                "answers_json": json.dumps(answers, ensure_ascii=False),
                "judge_status": "judged",
                "result_level": "good",
                "mastery_estimate": 0.72,
                "ai_result_json": json.dumps({
                    "questions": [{"question_index": 0, "verdict": "correct",
                                   "reason": "正确"}],
                }),
                "weak_points_json": json.dumps(["zero_grad"]),
            }

    def test_submit_shows_ai_result(self, qtbot):
        attempt = {
            "id": 1,
            "task_id": 5,
            "questions_json": json.dumps(
                [{"question": "解释 autograd", "type": "concept"}],
                ensure_ascii=False,
            ),
        }
        dlg = AssessmentDialog(self._FakeService(), attempt, TODAY)
        qtbot.addWidget(dlg)
        dlg.show()

        assert len(dlg._answer_edits) == 1
        dlg._answer_edits[0].setPlainText("autograd 用于自动求导")
        qtbot.mouseClick(dlg.submit_btn, QtMouseButton())

        qtbot.waitUntil(lambda: "验收完成" in dlg.status_label.text(), timeout=4000)
        assert "72%" in dlg.result_mastery.text()
        assert "zero_grad" in dlg.result_weak.text()

    def test_submit_failure_shows_feedback(self, qtbot):
        class FailService:
            def submit_answers(self, attempt_id, answers, today=None):
                from app.ai.interface import AIServiceError

                raise AIServiceError("AI 挂了")

        attempt = {
            "id": 1,
            "questions_json": json.dumps(
                [{"question": "q", "type": "concept"}], ensure_ascii=False,
            ),
        }
        dlg = AssessmentDialog(FailService(), attempt, TODAY)
        qtbot.addWidget(dlg)
        dlg.show()
        dlg._answer_edits[0].setPlainText("answer")
        qtbot.mouseClick(dlg.submit_btn, QtMouseButton())
        qtbot.waitUntil(
            lambda: "判题失败" in dlg.status_label.text(), timeout=4000
        )


class TestTrayNoRegression:
    def test_x_hides_with_services(self, qtbot, repo, task_service, date_service,
                                   conn):
        assessment_repo = AssessmentRepository(conn)
        w = _build_window(
            qtbot, repo, task_service, date_service,
            review_scheduler=ReviewService(repo, assessment_repo),
            exploration_service=ExplorationService(),
        )
        from tests.test_main_window_exit import _TrayStub

        w._tray = _TrayStub()
        w.show()
        qtbot.waitExposed(w)
        ev = QCloseEvent()
        w.closeEvent(ev)
        assert ev.isAccepted() is False
        assert w._quit_requested is False
        assert w.isVisible() is False

    def test_quit_cleans(self, qtbot, repo, task_service, date_service, conn):
        assessment_repo = AssessmentRepository(conn)
        w = _build_window(
            qtbot, repo, task_service, date_service,
            review_scheduler=ReviewService(repo, assessment_repo),
        )
        w.quit_app()
        assert w._quit_requested is True
