"""Step 5：JD Daily Summary UI + 近期趋势 UI 测试。

覆盖：空态/按钮、对话框默认值/输入、preview 复用 Service、matched/unmatched、
非法数量错误、保存/刷新、同日预加载与更新、14/30 天切换、样本数与百分比、
Top 8、缺口/暂不提前、历史 individual JD、保存不触发 Planner，
以及 UI 不自己计算 frequency。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton

from app.database.assessment_repository import AssessmentRepository
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.date_service import DateService
from app.services.jd_service import JdService
from app.services.jd_summary_service import JdSummaryService
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.career_dialogs import JdHistoryDialog, JdSummaryInputDialog
from app.ui.main_window import MainWindow

TODAY = "2026-09-14"
TARGET = "internship"

D1, D2, D3 = "2026-09-12", "2026-09-13", "2026-09-14"
T1 = "Python 8\nPyTorch 7\nEmbedding 4\nRecall 3"
T2 = "Python 13\nPyTorch 11\nEmbedding 9\nRecall 8\nRanking 7"
T3 = "Python 10\nPyTorch 9\nEmbedding 8\nRecall 7\nRanking 6\nRAG 4"

ALL_SKILLS = ("Python", "PyTorch", "Embedding", "Recall", "Ranking", "RAG",
              "LLM 基础", "Transformer", "SFT", "Agent", "Hugging Face",
              "推荐系统基础", "SQL", "C++")


def _env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    prepo = StudyPlanRepository(conn)
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo)
    sps.ensure_default_plan()
    sr = SkillRepository(conn)
    for name in ALL_SKILLS:
        sr.upsert_by_name(name)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo)
    jd = JdService(JdRepository(conn), sr, ss)
    js = JdSummaryService(JdDailySummaryRepository(conn), sr)
    return {"repo": repo, "arepo": arepo, "sps": sps, "sr": sr, "ss": ss,
            "jd": jd, "js": js}


def _window(qtbot, env, **extra):
    w = MainWindow(
        task_service=TaskService(env["repo"]),
        date_service=DateService(env["repo"], study_plan_service=env["sps"]),
        today_provider=lambda: TODAY,
        study_plan_service=env["sps"],
        skill_service=env["ss"],
        jd_service=env["jd"],
        jd_summary_service=env["js"],
        assessment_repo=env["arepo"],
        **extra,
    )
    qtbot.addWidget(w)
    return w


def _panel_labels(w):
    labels = [l.text() for l in w.list_container.findChildren(QLabel)]
    if "近期 JD 技术趋势" not in labels:
        return []
    i = labels.index("近期 JD 技术趋势")
    return labels[i:]


def _btns(w):
    return {b.text(): b for b in w.list_container.findChildren(QPushButton)}


class TestTrendPanel:
    def test_empty_state_and_add_button(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        txt = "\n".join(_panel_labels(w))
        assert "近期 JD 技术趋势" in txt
        assert "暂无近期 JD 技术汇总" in txt
        assert "0%" not in txt
        assert "添加今日 JD 技术汇总" in _btns(w)
        assert "查看历史 JD" in _btns(w)

    def test_14day_trend_display(self, qtbot, conn):
        env = _env(conn)
        env["js"].save_summary(D1, T1, 10, TARGET)
        env["js"].save_summary(D2, T2, 15, TARGET)
        env["js"].save_summary(D3, T3, 12, TARGET)
        w = _window(qtbot, env)
        txt = "\n".join(_panel_labels(w))
        assert "近14天样本：37 个实习岗位" in txt
        assert "Python    83.8%" in txt
        assert "PyTorch    73.0%" in txt
        assert "RAG    10.8%" in txt
        # 频率说明
        assert "频率 = " in txt

    def test_30day_toggle_calls_service(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        calls = []
        real = env["js"].compute_skill_trends

        def spy(end_date, window_days=14, target_type=TARGET):
            calls.append((end_date, window_days, target_type))
            return real(end_date, window_days, target_type)

        monkeypatch.setattr(env["js"], "compute_skill_trends", spy)
        env["js"].save_summary(D3, T3, 12, TARGET)
        w = _window(qtbot, env)
        assert calls[-1][1] == 14
        _btns(w)["近30天"].click()
        assert calls[-1][1] == 30
        assert "近30天样本" in "\n".join(_panel_labels(w))

    def test_top8_limit(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        trend = {
            "window_days": 14, "start_date": D1, "end_date": D3,
            "target_type": TARGET, "sample_count": 100,
            "skills": [
                {"skill_id": i, "name": f"S{i}", "mention_count": 10 - i,
                 "frequency": (10 - i) / 100} for i in range(12)
            ],
            "unmatched": [],
        }
        monkeypatch.setattr(env["js"], "compute_skill_trends",
                            lambda *a, **k: trend)
        w = _window(qtbot, env)
        rows = [x for x in _panel_labels(w) if x.endswith("%")]
        assert len(rows) == 8

    def test_ui_does_not_compute_frequency(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        trend = {
            "window_days": 14, "start_date": D1, "end_date": D3,
            "target_type": TARGET, "sample_count": 50,
            "skills": [{"skill_id": 1, "name": "Python", "mention_count": 7,
                        "frequency": 0.1234}],
            "unmatched": [],
        }
        monkeypatch.setattr(env["js"], "compute_skill_trends",
                            lambda *a, **k: trend)
        w = _window(qtbot, env)
        # 直接使用 Service 返回的 12.3%，而不是 7/50=14.0%
        assert any("Python    12.3%" in x for x in _panel_labels(w))

    def test_unmatched_displayed(self, qtbot, conn):
        env = _env(conn)
        env["js"].save_summary(D3, "Two-Tower 6\nDIN 4\nPython 5", 15, TARGET)
        w = _window(qtbot, env)
        txt = "\n".join(_panel_labels(w))
        assert "未匹配技能" in txt
        assert "Two-Tower    6 次" in txt
        assert "DIN    4 次" in txt
        assert "暂未映射到 Study Agent 技能体系" in txt

    def test_skill_gap_and_blocked_sections(self, qtbot, conn):
        env = _env(conn)
        # RAG 前置未满足；Embedding 未掌握且未被阻塞
        rag = env["sr"].get_by_name("RAG")
        env["sr"].update(rag["id"], prerequisites=["LLM 基础"])
        env["js"].save_summary(D3, "Embedding 9\nRAG 4\nPython 3", 12, TARGET)
        w = _window(qtbot, env)
        txt = "\n".join(_panel_labels(w))
        assert "当前主要技能缺口" in txt
        assert "Embedding    高频 · 尚未掌握" in txt
        assert "暂不提前" in txt
        assert "RAG    缺：LLM 基础" in txt

    def test_service_error_state(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        monkeypatch.setattr(env["js"], "compute_skill_trends",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        w = _window(qtbot, env)
        assert "JD 趋势服务异常" in "\n".join(_panel_labels(w))


class TestInputDialog:
    def test_defaults(self, qtbot, conn):
        env = _env(conn)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        assert dlg.date_edit.text() == TODAY
        assert dlg._target() == "internship"
        assert dlg.sample_spin.value() >= 1
        assert dlg.text_edit.toPlainText() == ""

    def test_preview_calls_service(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        seen = {}
        real = env["js"].preview_summary

        def spy(text, sample, target, date):
            seen["args"] = (text, sample, target, date)
            return real(text, sample, target, date)

        monkeypatch.setattr(env["js"], "preview_summary", spy)
        dlg.text_edit.setPlainText("Python 13")
        dlg.sample_spin.setValue(15)
        dlg.preview_btn.click()
        assert seen["args"] == ("Python 13", 15, "internship", TODAY)

    def test_preview_matched_and_unmatched(self, qtbot, conn):
        env = _env(conn)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        dlg.text_edit.setPlainText("Python 13\nTwo-Tower 6")
        dlg.sample_spin.setValue(15)
        dlg.preview_btn.click()
        out = dlg.preview_edit.toPlainText()
        assert "Python    13 / 15    86.7%" in out
        assert "Two-Tower    6 / 15" in out
        assert dlg.save_btn.isEnabled() is True

    def test_invalid_mention_shows_error_and_blocks_save(self, qtbot, conn):
        env = _env(conn)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        dlg.text_edit.setPlainText("Python 18")
        dlg.sample_spin.setValue(15)
        dlg.preview_btn.click()
        out = dlg.preview_edit.toPlainText()
        assert "数据错误" in out
        assert "超过 sample_count" in out
        assert dlg.save_btn.isEnabled() is False
        assert env["js"].get_summary(TODAY, TARGET) is None

    def test_save_success(self, qtbot, conn):
        env = _env(conn)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        dlg.text_edit.setPlainText("Python 13\nPyTorch 11")
        dlg.sample_spin.setValue(15)
        dlg.preview_btn.click()
        dlg.save_btn.click()
        assert dlg.saved is not None
        assert dlg.saved["sample_count"] == 15
        assert dlg.saved["summary_date"] == TODAY
        assert env["js"].get_summary(TODAY, TARGET) is not None

    def test_same_day_preload_and_update(self, qtbot, conn):
        env = _env(conn)
        env["js"].save_summary(TODAY, "Python 10", 15, TARGET)
        dlg = JdSummaryInputDialog(env["js"], TODAY)
        qtbot.addWidget(dlg)
        assert dlg.sample_spin.value() == 15  # 预加载已有
        assert "Python 10" in dlg.text_edit.toPlainText()
        assert "已有一份 JD 汇总" in dlg.notice.text()
        # 更新而非新增
        dlg.text_edit.setPlainText("Python 12")
        dlg.preview_btn.click()
        dlg.save_btn.click()
        rows = env["js"].summary_repo.list_summaries(target_type=TARGET)
        assert len(rows) == 1
        assert rows[0]["stats"][0]["mention_count"] == 12


class TestHistoryAndBoundaries:
    def test_history_dialog_lists_individual_jd(self, qtbot, conn):
        env = _env(conn)
        env["jd"].add_jd("推荐算法实习：熟悉 Recall、Ranking、Embedding",
                         company="某公司", title="推荐算法实习生")
        dlg = JdHistoryDialog(env["jd"])
        qtbot.addWidget(dlg)
        texts = "\n".join(l.text() for l in dlg.findChildren(QLabel))
        assert "推荐算法实习生" in texts and "某公司" in texts
        btns = {b.text() for b in dlg.findChildren(QPushButton)}
        assert "查看详情" in btns
        assert "添加 JD（兼容入口）" in btns

    def test_save_summary_does_not_touch_planner_or_tasks(
        self, qtbot, conn, monkeypatch
    ):
        env = _env(conn)
        w = _window(qtbot, env)
        tasks_before = env["repo"].list_by_date(TODAY)
        plans_before = env["repo"].conn.execute(
            "SELECT COUNT(*) FROM planner_decisions").fetchone()[0]
        phase_before = env["sps"].get_current_phase(TODAY).id

        class FakeDialog:
            def __init__(self, service, today, parent=None):
                self.saved = {"summary_date": TODAY, "sample_count": 15,
                              "target_type": TARGET}

            def exec(self):
                from PySide6.QtWidgets import QDialog

                return QDialog.DialogCode.Accepted

        monkeypatch.setattr("app.ui.career_dialogs.JdSummaryInputDialog",
                            FakeDialog)
        w._on_add_jd_summary()
        assert "已保存" in w.statusBar().currentMessage()
        assert env["repo"].list_by_date(TODAY) == tasks_before  # 未改任务
        assert env["repo"].conn.execute(
            "SELECT COUNT(*) FROM planner_decisions").fetchone()[0] == plans_before
        assert env["sps"].get_current_phase(TODAY).id == phase_before

    def test_unmatched_not_created_as_skill(self, qtbot, conn):
        env = _env(conn)
        env["js"].save_summary(TODAY, "Two-Tower 6", 15, TARGET)
        assert env["sr"].get_by_name("Two-Tower") is None
        assert env["repo"].list_by_date(TODAY) == []
