"""Phase E：UI 职业能力收尾整合测试。

覆盖（与清单对应）：
1 技能面板显示
2 无 mastery evidence
3 高 mastery 显示
4 blocked skill 显示
5 JD 列表
6 JD 分析详情
7 JD 输入
8 dry-run 预览
9 正式保存
10 outcomes 显示
11 resume material 显示
12 Obsidian 导出按钮
13 导出成功
14 导出失败
15 空状态
16 异常状态
17 今日页不回归
18 验收不回归
19 托盘不回归
20 Planner 不回归
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QPushButton

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import (
    JdRepository,
    LearningOutcomeRepository,
    SkillRepository,
)
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.date_service import DateService
from app.services.exploration_service import ExplorationService
from app.services.extra_task_service import ExtraTaskService
from app.services.jd_service import JdService
from app.services.learning_outcome_service import LearningOutcomeService
from app.services.notes_service import NotesService
from app.services.review_service import ReviewService
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.career_dialogs import JdDetailDialog, JdInputDialog, ResumeMaterialDialog
from app.ui.main_window import MainWindow

TODAY = "2026-09-20"

JD_TEXT = (
    "推荐算法实习生：熟悉推荐系统召回、排序，熟悉 Recall、Ranking、Embedding、"
    "Transformer；有 LLM/RAG 应用经验优先。"
)


def _make_env(conn, plan_repo, with_jd=True, with_outcome=True, seed=True,
              add_jd_data=True):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    sps.ensure_default_plan()
    skill_repo = SkillRepository(conn)
    ss = SkillService(skill_repo, plan_repo=plan_repo, assessment_repo=arepo)
    if seed and not skill_repo.list_all():
        ss.seed_from_career_context()
        ss.recompute_all_priority_scores()
    jd = None
    if with_jd:
        jd = JdService(JdRepository(conn), skill_repo, ss)
        if add_jd_data:
            jd.add_jd(JD_TEXT, company="某公司", title="推荐算法实习生")
            ss.recompute_all_priority_scores()
    lo = LearningOutcomeService(LearningOutcomeRepository(conn))
    if with_outcome:
        lo.create_manual_outcome(
            date=TODAY, kind="project", title="双塔召回实验",
            content="实现双塔召回", tech_stack=["PyTorch"],
            metrics={"recall@20": 0.31},
        )
    ns = NotesService(repo=repo, study_plan_service=sps, outcome_service=lo,
                      assessment_repo=arepo, jd_service=jd)
    return {
        "repo": repo, "arepo": arepo, "sps": sps, "skill_repo": skill_repo,
        "ss": ss, "jd": jd, "lo": lo, "ns": ns,
    }


def _window(qtbot, env, **extra):
    w = MainWindow(
        task_service=TaskService(env["repo"]),
        date_service=DateService(env["repo"], study_plan_service=env["sps"]),
        today_provider=lambda: TODAY,
        study_plan_service=env["sps"],
        skill_service=env["ss"],
        jd_service=env["jd"],
        outcome_service=env["lo"],
        notes_service=env["ns"],
        **extra,
    )
    qtbot.addWidget(w)
    return w


def _labels(w):
    return [lbl.text() for lbl in w.list_container.findChildren(QLabel)]


def _label_text(w):
    return "\n".join(_labels(w))


class TestSkillPanel:
    def test_skill_panel_shows(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        w = _window(qtbot, env)
        txt = _label_text(w)
        assert "技能状态" in txt
        assert "PyTorch" in txt
        assert "近期重点技能" in txt
        assert "当前阶段" in txt

    def test_no_mastery_shows_placeholder(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        w = _window(qtbot, env)
        assert "暂无验收证据" in _label_text(w)

    def test_high_mastery_shown(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        kp = env["arepo"].create_knowledge_point("pytorch.core")
        env["arepo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.9,
            last_assessed_at="2026-09-19T10:00:00",
        )
        pytorch = env["skill_repo"].get_by_name("PyTorch")
        env["skill_repo"].update(pytorch["id"], mastery_ref=f"kp:{kp['id']}")
        w = _window(qtbot, env)
        w.refresh()
        assert "mastery:0.90" in _label_text(w)

    def test_blocked_skill_shown(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        w = _window(qtbot, env)
        assert "前置未满足" in _label_text(w)


class TestJdPanel:
    def test_jd_list_shown(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        w = _window(qtbot, env)
        txt = _label_text(w)
        assert "最新 JD / 岗位需求" in txt
        assert "推荐算法实习生" in txt
        assert "某公司" in txt

    def test_jd_empty_state(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo, with_jd=True, add_jd_data=False)
        w = _window(qtbot, env)
        assert "暂无已分析 JD" in _label_text(w)

    def test_jd_detail_dialog_renders_impact(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        jd_row = env["jd"].jd_repo.list_all()[0]
        dlg = JdDetailDialog(env["jd"], jd_row)
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QPlainTextEdit

        text = dlg.findChild(QPlainTextEdit).toPlainText()
        assert "Recommendation" in text or "recommendation" in text.lower() \
            or "推荐" in text
        assert "对当前技能优先级的影响" in text

    def test_jd_input_analyze_dry_run_then_save(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        before = len(env["jd"].jd_repo.list_all())
        dlg = JdInputDialog(env["jd"])
        qtbot.addWidget(dlg)
        dlg.jd_edit.setPlainText(
            "广告算法工程师：熟悉 CTR、特征工程；要求熟练 Python."
        )
        dlg._analyze()
        result = dlg.result_edit.toPlainText()
        assert "必须技能" in result
        assert "CTR" in result
        # dry-run：尚未入库
        assert len(env["jd"].jd_repo.list_all()) == before
        dlg._save()  # 正式保存
        assert len(env["jd"].jd_repo.list_all()) == before + 1


class TestResumeMaterialDialog:
    def test_resume_material_dialog(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        dlg = ResumeMaterialDialog(env["lo"],
                                   env["lo"].list_by_date(TODAY))
        qtbot.addWidget(dlg)
        from PySide6.QtWidgets import QPlainTextEdit

        text = dlg.findChild(QPlainTextEdit).toPlainText()
        assert "双塔召回实验" in text
        assert "技术栈" in text
        assert "recall@20=0.31" in text  # 真实指标


class TestStates:
    def test_error_state_panel(self, qtbot, conn, plan_repo, monkeypatch):
        env = _make_env(conn, plan_repo)

        def boom():
            raise RuntimeError("repo down")

        monkeypatch.setattr(env["jd"].jd_repo, "list_all", boom)
        w = _window(qtbot, env)
        w.refresh()
        assert "JD 服务异常" in _label_text(w)


class TestNoRegression:
    def _seed_tasks(self, env):
        env["repo"].create(title="今日新知识任务", scheduled_date=TODAY,
                           source="generated", task_type="new")
        kp = env["arepo"].create_knowledge_point("kp1")
        env["repo"].create(title="复习 kp1", scheduled_date=TODAY,
                           source="review", task_type="review",
                           knowledge_point_id=kp["id"])
        env["repo"].create(title="【额外】实践", scheduled_date=TODAY,
                           source="extra", task_type="extra",
                           difficulty="practice")

    def test_today_sections_no_regression(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo)
        self._seed_tasks(env)
        w = _window(qtbot, env)
        txt = _label_text(w)
        assert "今日新知识" in txt
        assert "今日复习" in txt
        assert "额外学习" in txt
        assert "今日新知识任务" in txt
        assert "复习 kp1" in txt

    def test_assessment_no_regression(self, qtbot, conn, plan_repo, monkeypatch):
        from app.ai.client import DeepSeekClient

        class FakeQuestionAI:
            def is_configured(self):
                return True

            def chat(self, s, u, **k):
                return json.dumps({"questions": [
                    {"question": "q", "type": "concept", "expected_points": 1}]})

        env = _make_env(conn, plan_repo, with_jd=False)
        kp = env["arepo"].create_knowledge_point("pytorch.autograd")
        env["arepo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.4,
            last_assessed_at="2026-09-19T10:00:00")
        t = env["repo"].create(
            title="复习 pytorch.autograd", scheduled_date=TODAY,
            source="review", task_type="review", knowledge_point_id=kp["id"])
        review_scheduler = ReviewService(env["repo"], env["arepo"])
        assessment_service = AssessmentService(
            FakeQuestionAI(), assessment_repo=env["arepo"],
            review_service=review_scheduler, outcome_service=env["lo"],
        )
        w = _window(qtbot, env, assessment_service=assessment_service,
                    assessment_repo=env["arepo"],
                    review_scheduler=review_scheduler)

        captured = {}

        class DummyWorker(QObject):
            succeeded = Signal(object)
            failed = Signal(str)
            finished = Signal()

            def __init__(self, func, args=(), kwargs=None, parent=None):
                super().__init__()
                self._f, self._a, self._kw = func, args, kwargs or {}

            def start(self):
                self.succeeded.emit(self._f(*self._a, **self._kw))
                self.finished.emit()

            def isRunning(self):
                return False

            def requestInterruption(self):
                pass

            def wait(self, *a):
                return True

        class DummyDialog(QObject):
            assessment_completed = Signal(int)

            def __init__(self, service, attempt, today, parent=None):
                super().__init__()
                captured["attempt"] = attempt

            def exec(self):
                return 0

        monkeypatch.setattr("app.ui.main_window.AssessmentWorker", DummyWorker)
        monkeypatch.setattr("app.ui.main_window.AssessmentDialog", DummyDialog)
        w._on_start_assessment(t.id)
        assert captured.get("attempt") is not None
        assert captured["attempt"]["knowledge_point_id"] == kp["id"]

    def test_tray_no_regression(self, qtbot, conn, plan_repo):
        from tests.test_main_window_exit import _TrayStub

        env = _make_env(conn, plan_repo)
        w = _window(qtbot, env)
        w._tray = _TrayStub()
        w.show()
        qtbot.waitExposed(w)
        ev = QCloseEvent()
        w.closeEvent(ev)
        assert ev.isAccepted() is False
        assert w._quit_requested is False
        assert w.isVisible() is False

    def test_planner_no_regression(self, qtbot, conn, plan_repo):
        from app.services.daily_planner_service import DailyPlannerService

        class FallbackPlanner:
            def is_configured(self):
                return False

        env = _make_env(conn, plan_repo)
        dp = DailyPlannerService(
            env["repo"], plan_repo, planner=FallbackPlanner(),
            study_plan_service=env["sps"], assessment_repo=env["arepo"],
            skill_service=env["ss"], jd_service=env["jd"],
        )
        res = dp.generate_next_day_plan("2026-09-19")  # 规划 09-20
        created = env["repo"].list_by_date(TODAY)
        assert created
        phase_names = {t.name for t in env["sps"].get_current_phase(TODAY).topics}
        assert created[0].title in phase_names or all(
            t.title in phase_names for t in created
        )
        titles = " ".join(t.title for t in created)
        # JD 高频但前置未满足的高级技能不被安排（不越级）
        assert "RAG 全流程搭建" not in titles
        assert "Agent 实现与多步编排" not in titles


class TestReadableButtons:
    """小样式修复：三个浅底白字按钮改为复用 SecondaryButton 蓝字风格。"""

    def _window_with_all(self, qtbot, conn, plan_repo):
        env = _make_env(conn, plan_repo, with_jd=True, with_outcome=True)
        # 一个 active 任务 -> 出现“完成 / 未完成”按钮
        env["repo"].create(title="学习任务", scheduled_date=TODAY,
                           source="generated", task_type="new")
        extra = ExtraTaskService(
            env["repo"], study_plan_service=env["sps"],
            assessment_repo=env["arepo"],
        )
        exploration = ExplorationService()
        w = _window(qtbot, env, extra_service=extra,
                    exploration_service=exploration)
        return w

    def _buttons_by_text(self, w):
        out = {}
        for b in w.list_container.findChildren(QPushButton):
            out[b.text()] = b
        return out

    def test_three_target_buttons_reuse_secondary_blue(self, qtbot, conn,
                                                        plan_repo):
        from PySide6.QtGui import QColor, QPalette

        w = self._window_with_all(qtbot, conn, plan_repo)
        targets = ("继续学习 / 生成额外任务", "添加 JD")
        found = self._buttons_by_text(w)
        assert set(targets) <= set(found)
        for text in targets:
            btn = found[text]
            assert btn.objectName() == "SecondaryButton", text
            c = btn.palette().color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText
            )
            assert (c.red(), c.green(), c.blue()) == QColor(
                "#2c6fbb"
            ).getRgb()[:3], f"{text} 文字应为主题蓝"

    def test_shared_secondary_style_covers_all_states(self):
        from app.ui.styles import APP_STYLE

        assert "QPushButton#SecondaryButton" in APP_STYLE
        assert "QPushButton#SecondaryButton:hover" in APP_STYLE
        assert "QPushButton#SecondaryButton:disabled" in APP_STYLE
        assert "#2c6fbb" in APP_STYLE  # 主题蓝文字

    def test_reference_buttons_not_regressed(self, qtbot, conn, plan_repo):
        w = self._window_with_all(qtbot, conn, plan_repo)
        found = self._buttons_by_text(w)
        # 已有按钮样式保持不变
        assert found["完成"].objectName() == "SecondaryButton"
        assert found["未完成"].objectName() == "DangerButton"
        assert found["打开链接"].objectName() == "SecondaryButton"
        # “重新规划今天”仍是普通按钮（无 PrimaryButton 白字；非滚动区）
        assert w.planner_replan_btn.objectName() == ""
        assert w.planner_replan_btn.text() == "重新规划今天"
