"""技能概览（Step 3）UI 测试。

覆盖：
1 learning -> 学习中
2 blocked -> 待解锁
3 mastered -> 已掌握
4 无 mastery evidence 不显示“暂无验收证据”
5 有 mastery evidence 显示百分比
6 mastery 来源仍为真实 assessment evidence
7 待解锁显示缺失 prerequisite 名称
8 blocked 最多显示 5 个
9 当前学习最多显示 5 个
10 已掌握摘要展示
11/12/13 不显示 priority_score / mastery_ref / 原始状态
14 首页不铺满全部 skill
15 今日学习 / JD 面板不回归
16 托盘不回归
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.date_service import DateService
from app.services.jd_service import JdService
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow

TODAY = "2026-09-20"


def _env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    prepo = StudyPlanRepository(conn)
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo)
    sps.ensure_default_plan()
    sr = SkillRepository(conn)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo)
    if not sr.list_all():
        ss.seed_from_career_context()
        ss.recompute_all_priority_scores()
    jd = JdService(JdRepository(conn), sr, ss)
    return {"repo": repo, "arepo": arepo, "sps": sps, "sr": sr, "ss": ss,
            "jd": jd}


def _window(qtbot, env):
    w = MainWindow(
        task_service=TaskService(env["repo"]),
        date_service=DateService(env["repo"], study_plan_service=env["sps"]),
        today_provider=lambda: TODAY,
        study_plan_service=env["sps"],
        skill_service=env["ss"],
        jd_service=env["jd"],
        assessment_repo=env["arepo"],
    )
    qtbot.addWidget(w)
    return w


def _labels(w):
    return [lbl.text() for lbl in w.list_container.findChildren(QLabel)]


def _txt(w):
    return "\n".join(_labels(w))


def _group_items(w, group):
    """取某个分组标题后面、下一个分组/区域前的条目行。"""
    labels = _labels(w)
    boundaries = {"当前学习", "待解锁", "已掌握", "技能概览",
                  "近期 JD 技术趋势"}
    if group not in labels:
        return []
    i = labels.index(group)
    out = []
    for lbl in labels[i + 1:]:
        if lbl in boundaries:
            break
        out.append(lbl)
    return out


class TestSkillOverviewText:
    def test_learning_shows_zh(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        txt = _txt(w)
        assert "学习中" in txt
        assert "learning" not in txt

    def test_blocked_shows_zh_and_missing_prereq(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        items = _group_items(w, "待解锁")
        assert any("缺：" in x for x in items)
        # 缺失前置名称确实来自 SkillService
        blocked = [s for s in env["sr"].list_all()
                   if env["ss"].is_blocked(s)]
        assert blocked
        a_missing = env["ss"].missing_prerequisites(blocked[0])
        assert any(name in "\n".join(items) for name in a_missing)

    def test_mastered_summary(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        items = _group_items(w, "已掌握")
        assert len(items) == 1  # 摘要只占一行
        assert items[0].startswith("已掌握")
        assert "Python" in items[0]

    def test_no_mastery_placeholder_absent(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        txt = _txt(w)
        assert "暂无验收证据" not in txt
        assert "AI验收" not in txt

    def test_mastery_still_from_real_evidence(self, qtbot, conn):
        env = _env(conn)
        kp = env["arepo"].create_knowledge_point("pytorch.core")
        env["arepo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.62,
            last_assessed_at="2026-09-19T10:00:00")
        pytorch = env["sr"].get_by_name("PyTorch")
        env["sr"].update(pytorch["id"], mastery_ref=f"kp:{kp['id']}")
        w = _window(qtbot, env)
        w.refresh()
        assert "AI验收 62%" in _txt(w)

    def test_evidence_without_assessed_at_is_ignored(self, qtbot, conn):
        env = _env(conn)
        kp = env["arepo"].create_knowledge_point("pytorch.core")  # 未验收
        pytorch = env["sr"].get_by_name("PyTorch")
        env["sr"].update(pytorch["id"], mastery_ref=f"kp:{kp['id']}")
        w = _window(qtbot, env)
        w.refresh()
        assert "AI验收" not in _txt(w)

    def test_no_internal_fields(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        txt = _txt(w)
        for raw in ("priority_score", "mastery_ref", "mastery:", "jd_frequency",
                    "not_started", "deferred", "learning", "前置:"):
            assert raw not in txt


class TestSkillOverviewLimits:
    def test_current_max_5(self, qtbot, conn):
        env = _env(conn)
        for i in range(10):
            env["sr"].create(name=f"学习项{i}", tier="A", status="learning")
        w = _window(qtbot, env)
        assert len([x for x in _group_items(w, "当前学习") if "级 ·" in x]) <= 5

    def test_blocked_max_5(self, qtbot, conn):
        env = _env(conn)
        for i in range(9):
            env["sr"].create(name=f"阻塞{i}", tier="S", status="not_started",
                             prerequisites=["不存在的技能"])
        w = _window(qtbot, env)
        assert len([x for x in _group_items(w, "待解锁") if "缺：" in x]) <= 5

    def test_page_not_flooded(self, qtbot, conn):
        env = _env(conn)
        total = len(env["sr"].list_all())
        assert total > 15  # 已 seed 的技能池确实很大
        w = _window(qtbot, env)
        rows = [x for x in _labels(w) if "级 ·" in x]
        assert len(rows) <= 5  # 只有“当前学习”逐条展开

    def test_empty_current_state(self, qtbot, conn):
        env = _env(conn)
        for s in env["sr"].list_all():
            env["sr"].update(s["id"], status="mastered")
        env["ss"].recompute_all_priority_scores()
        w = _window(qtbot, env)
        assert "当前暂无正在学习的技能" in _txt(w)


class TestCurrentLearningClassification:
    def test_learning_status_in_current(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        items = "\n".join(_group_items(w, "当前学习"))
        for s in env["sr"].list_all():
            if s["status"] == "learning" and not env["ss"].is_blocked(s):
                assert s["name"] in items

    def test_phase_linked_skill_in_current(self, qtbot, conn):
        env = _env(conn)
        phase = env["sps"].get_current_phase(TODAY)
        linked = set()
        for t in phase.topics:
            linked.update(env["ss"].skills_for_topic(t.id) or [])
        assert linked  # 当前 phase 有明确关联技能
        w = _window(qtbot, env)
        items = "\n".join(_group_items(w, "当前学习"))
        assert any(name in items for name in linked)

    def test_gate_ok_future_skill_not_in_current(self, qtbot, conn):
        """SQL 场景：A级 + gate 放行 + 高 priority，但无 phase 关联 → 不进入。"""
        env = _env(conn)
        sql = env["sr"].get_by_name("SQL")
        assert sql is not None
        env["sr"].update(sql["id"], status="not_started",
                         prerequisites=[])
        env["ss"].recompute_all_priority_scores()
        sql = env["sr"].get_by_name("SQL")
        # 确认它确实在 active candidates（gate=ok、未阻塞）里
        cands = [d["name"] for d in env["ss"].select_active_candidates(limit=20)]
        assert "SQL" in cands
        assert not env["ss"].is_blocked(sql)
        w = _window(qtbot, env)
        assert "SQL" not in "\n".join(_group_items(w, "当前学习"))
        # 未被阻塞 → 也不应出现在“待解锁”
        assert "SQL" not in "\n".join(_group_items(w, "待解锁"))

    def test_blocked_only_in_unlock(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        unlock = "\n".join(_group_items(w, "待解锁"))
        current = "\n".join(_group_items(w, "当前学习"))
        for s in env["sr"].list_all():
            if env["ss"].is_blocked(s):
                assert s["name"] not in current
        assert "缺：" in unlock


class TestNoRegression:
    def test_today_and_jd_panels_still_present(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        txt = _txt(w)
        assert "技能概览" in txt
        assert "近期 JD 技术趋势" in txt
        # 今日页任务区仍在
        assert w.scroll is not None

    def test_tray_still_works(self, qtbot, conn):
        env = _env(conn)
        w = _window(qtbot, env)
        w.closeEvent(QCloseEvent())
        assert w.isHidden() or not w.isVisible()
