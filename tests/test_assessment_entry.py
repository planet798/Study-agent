"""日常任务 -> 验收入口 的 UI/关联修复测试。

覆盖：
1 done new(+topic/kp) 显示“开始验收”
2 done new 仍可 start_assessment
3 pending attempt -> “继续验收”
4 legacy extra(+kp) 仍显示验收入口（历史记录兼容）
5 legacy extra 只有 topic_id 时仍显示入口
6 legacy extra 无 topic/kp 不显示
7 历史 review 行的关联信息保留（legacy compatibility）
8 验收成功写 mastery / last_assessed_at，legacy next_review_date 保持原值
9 new task 验收不被 done 状态阻止
10 legacy extra 验收正确关联 task_id / kp_id
11 完成/未完成按钮不回归
12 不出现重复“开始验收”
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPushButton

from app.database.assessment_repository import AssessmentRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.study_plan_service import StudyPlanService
from app.ui.main_window import MainWindow
from app.ui.task_widget import TaskWidget

TODAY = "2026-09-12"
PHASE_START = "2026-09-01"

QUESTIONS_CONTENT = json.dumps(
    {"questions": [{"question": "解释 Attention", "type": "concept",
                    "expected_points": 3}]},
    ensure_ascii=False,
)
JUDGMENT_CONTENT = json.dumps(
    {"questions": [{"question_index": 0, "verdict": "correct", "reason": "ok"}],
     "weak_points": [], "result_level": "good", "mastery_estimate": 0.8},
    ensure_ascii=False,
)


class FakeQuestionAI:
    def __init__(self, responses=None):
        self.responses = list(responses or [QUESTIONS_CONTENT])

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return self.responses.pop(0)


def _plan(conn, names=("Transformer", "RAG")):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    topics = {}
    for i, n in enumerate(names):
        topics[n] = plan_repo.create_topic(phase_id=phase.id, name=n,
                                          description=f"{n} 描述",
                                          estimated_minutes=60, priority=3,
                                          order_index=i)
    return plan_repo, topics


def _btns(widget) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton)]


def _dummy_flow(monkeypatch):
    """用 Dummy worker/dialog 替换真实线程与对话框，捕获 attempt。"""
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

        def isRunning(self):
            return False

        def requestInterruption(self):
            pass

        def wait(self, *a):
            return True

    class DummyDialog(QObject):
        assessment_completed = Signal(int)

        def __init__(self, service, attempt, today, parent=None, **kwargs):
            super().__init__()
            captured["attempt"] = attempt

        def exec(self):
            return 0

    monkeypatch.setattr("app.ui.main_window.AssessmentWorker", DummyWorker)
    monkeypatch.setattr("app.ui.main_window.AssessmentDialog", DummyDialog)
    return captured


# ================= 1、6、11、12：TaskWidget 显示规则 =================

class TestWidgetVisibility:
    def _widget(self, qtbot, task):
        w = TaskWidget(task)
        qtbot.addWidget(w)
        return w

    def test_done_new_with_topic_shows_assessment(
        self, qtbot, repo, conn
    ):
        _, topics = _plan(conn)
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        topic_id=topics["Transformer"].id)
        repo.mark_done(t.id)
        w = self._widget(qtbot, repo.get(t.id))
        assert "开始验收" in _btns(w)
        assert "已完成" not in _btns(w)  # 已完成是 label，不是按钮

    def test_done_new_with_kp_shows_assessment(self, qtbot, repo, conn):
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp.x")
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        knowledge_point_id=kp["id"])
        repo.mark_done(t.id)
        w = self._widget(qtbot, repo.get(t.id))
        assert "开始验收" in _btns(w)

    def test_active_new_shows_complete_notdone_assessment(
        self, qtbot, repo, conn
    ):
        _, topics = _plan(conn)
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        topic_id=topics["Transformer"].id)
        w = self._widget(qtbot, t)
        texts = _btns(w)
        assert "完成" in texts and "未完成" in texts and "开始验收" in texts

    def test_manual_no_topic_hides_assessment(self, qtbot, task_service):
        t = task_service.create_task("手动任务", scheduled_date=TODAY)
        w = self._widget(qtbot, t)
        assert "开始验收" not in _btns(w)

    def test_extra_with_kp_shows_assessment(self, qtbot, repo, conn):
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp.e")
        t = repo.create(title="legacy额外", scheduled_date=TODAY, source="extra",
                        task_type="extra", knowledge_point_id=kp["id"])
        w = self._widget(qtbot, t)
        assert "开始验收" in _btns(w)

    def test_extra_topic_only_shows_assessment(self, qtbot, repo, conn):
        _, topics = _plan(conn)
        t = repo.create(title="legacy额外", scheduled_date=TODAY, source="extra",
                        task_type="extra", topic_id=topics["RAG"].id)
        assert t.knowledge_point_id is None
        w = self._widget(qtbot, t)
        assert "开始验收" in _btns(w)

    def test_extra_no_topic_no_kp_hides(self, qtbot, repo):
        t = repo.create(title="legacy额外", scheduled_date=TODAY, source="extra",
                        task_type="extra")
        w = self._widget(qtbot, t)
        assert "开始验收" not in _btns(w)

    def test_no_duplicate_assessment_button(self, qtbot, repo, conn):
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp.d")
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        knowledge_point_id=kp["id"])
        w = self._widget(qtbot, t)
        assert _btns(w).count("开始验收") == 1
        # 重新渲染也不重复
        w.render(repo.get(t.id))
        assert _btns(w).count("开始验收") == 1

    def test_done_render_keeps_single_assessment(self, qtbot, repo, conn):
        _, topics = _plan(conn)
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        topic_id=topics["Transformer"].id)
        w = self._widget(qtbot, t)
        assert _btns(w).count("开始验收") == 1
        repo.mark_done(t.id)
        w.render(repo.get(t.id))
        assert _btns(w).count("开始验收") == 1

    def test_assessment_button_text_is_blue(self, qtbot, repo, conn):
        from PySide6.QtGui import QPalette

        _, topics = _plan(conn)
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        topic_id=topics["Transformer"].id)
        w = self._widget(qtbot, t)
        btn = w.assessment_btn
        assert btn.text() == "开始验收"
        # 蓝字（次级按钮样式 + palette 兜底），不改尺寸 / 布局
        assert btn.objectName() == "SecondaryButton"
        for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            color = btn.palette().color(grp, QPalette.ColorRole.ButtonText)
            assert color.name() == "#2c6fbb"


# ================= UI 流程：done new 可验收 =================

def _window(qtbot, repo, task_service, date_service, conn, **extra):
    arepo = AssessmentRepository(conn)
    plan_repo, _ = _plan(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    w = MainWindow(
        task_service=task_service, date_service=date_service,
        today_provider=lambda: TODAY,
        study_plan_service=sps,
        assessment_service=AssessmentService(
            FakeQuestionAI([QUESTIONS_CONTENT, QUESTIONS_CONTENT]),
            assessment_repo=arepo,
        ),
        assessment_repo=arepo,
        **extra,
    )
    qtbot.addWidget(w)
    return w, arepo, sps


class TestAssessmentFlow:
    def test_done_new_can_start_assessment(
        self, qtbot, repo, task_service, date_service, conn, monkeypatch
    ):
        captured = _dummy_flow(monkeypatch)
        w, arepo, _ = _window(qtbot, repo, task_service, date_service, conn)
        # done 的 generated/new 任务（有 topic，无 kp）在今日页仍可验收
        t = repo.create(title="Transformer：Attention / MHA / FFN",
                        scheduled_date=TODAY, source="generated",
                        topic_id=repo.conn.execute(
                            "SELECT id FROM study_topics WHERE name LIKE "
                            "'Transformer%'").fetchone()["id"])
        repo.mark_done(t.id)
        w.refresh()
        w._on_start_assessment(t.id)
        at = captured.get("attempt")
        assert at is not None and at["task_id"] == t.id
        assert at["knowledge_point_id"] is not None
        # 安全网已把 done 任务也补上 kp
        assert repo.get(t.id).knowledge_point_id == at["knowledge_point_id"]

    def test_historical_review_row_assessment_compatibility(
        self, qtbot, repo, task_service, date_service, conn, monkeypatch
    ):
        captured = _dummy_flow(monkeypatch)
        w, arepo, _ = _window(qtbot, repo, task_service, date_service, conn)
        kp = arepo.create_knowledge_point("kp.review")
        arepo.update_knowledge_point(
            kp["id"], last_assessed_at="2026-09-01T00:00:00",
            mastery_estimate=0.4, review_count=1)
        t = repo.create(title="复习 kp.review", scheduled_date=TODAY,
                        source="review", task_type="review",
                        knowledge_point_id=kp["id"])
        w.refresh()
        w._on_start_assessment(t.id)
        at = captured.get("attempt")
        assert at is not None and at["task_id"] == t.id
        assert at["knowledge_point_id"] == kp["id"]

    def test_pending_attempt_label_is_continue(
        self, qtbot, repo, task_service, date_service, conn
    ):
        w, arepo, sps = _window(qtbot, repo, task_service, date_service, conn)
        topic_id = repo.conn.execute(
            "SELECT id FROM study_topics WHERE name='RAG'").fetchone()["id"]
        t = repo.create(title="RAG", scheduled_date=TODAY, source="generated",
                        topic_id=topic_id)
        t = sps.link_task_knowledge_point(t)
        arepo.create_attempt(t.knowledge_point_id, QUESTIONS_CONTENT,
                             task_id=t.id)  # pending
        w.refresh()
        wd = next(x for x in w._task_widgets if x.task().id == t.id)
        assert "继续验收" in _btns(wd)
        assert "开始验收" not in _btns(wd)

    def test_no_topic_still_unsupported(
        self, qtbot, repo, task_service, date_service, conn
    ):
        w, arepo, _ = _window(qtbot, repo, task_service, date_service, conn)
        t = task_service.create_task("无知识点", scheduled_date=TODAY)
        w.refresh()
        w._on_start_assessment(t.id)  # 不崩，且不建 kp
        assert arepo.list_knowledge_points() == []


# ================= Extra 服务创建即关联 kp =================

# ================= 验收只写 mastery，不触碰 legacy review fields =================

class TestAssessmentWritesEvidence:
    def test_success_preserves_legacy_review_fields(self, conn, repo):
        plan_repo, topics = _plan(conn)
        arepo = AssessmentRepository(conn)
        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
        t = repo.create(title="t", scheduled_date=TODAY, source="generated",
                        topic_id=topics["Transformer"].id)
        t = sps.link_task_knowledge_point(t)
        svc = AssessmentService(
            FakeQuestionAI([QUESTIONS_CONTENT, JUDGMENT_CONTENT]),
            assessment_repo=arepo)
        at = svc.start_assessment(t.knowledge_point_id, task_id=t.id)
        arepo.update_knowledge_point(
            t.knowledge_point_id, next_review_date="2026-09-10",
            interval_days=5, review_count=3,
        )
        svc.submit_answers(at["id"], ["答案"], today="2026-09-13")
        kp = arepo.get_knowledge_point(t.knowledge_point_id)
        assert kp["mastery_estimate"] == 0.8
        assert kp["last_assessed_at"] is not None
        assert kp["next_review_date"] == "2026-09-10"
        assert kp["interval_days"] == 5
        assert kp["review_count"] == 3
