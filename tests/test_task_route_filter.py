"""Phase C：今日任务路线归属 / 筛选 / 手动任务路线 / 暂停规划。

覆盖需求 35 的 18~24、28、33~38、40~42。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.date_service import DateService
from app.services.learning_route_service import LearningRouteService
from app.services.manual_task_service import ManualTaskService
from app.services.route_plan_service import RoutePlanService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.manual_task_dialog import AddLearningTaskDialog
from app.ui.past_task_dialog import PastTaskConfirmationDialog
from app.ui.task_widget import TaskWidget

TODAY = "2026-09-15"
YESTERDAY = "2026-09-14"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    default = route_repo.get_default_learning_route()
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service, "sps": sps,
        "ts": TaskService(repo), "default": default,
        "manual": ManualTaskService(repo, assessment_repo=arepo,
                                    study_plan_service=sps),
        "route_plan": RoutePlanService(plan_repo, repo, arepo),
    }


def _second_route(env, name="强化学习", topic="MDP"):
    rl = env["route_service"].create_learning_route(name, priority=5)
    plan = env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    t = env["route_plan"].add_topic(rl.id, phase.id, topic)
    return rl, plan, phase, t


# ================= 18~19：TaskWidget 路线标签 =================

class TestTaskRouteLabel:
    def test_route_label_shows_name(self, env, qtbot):
        t = env["repo"].create("任务", scheduled_date=TODAY, route_id=env["default"].id)
        w = TaskWidget(env["repo"].get(t.id), route_name="搜广推 + LLM")
        qtbot.addWidget(w)
        assert w.route_tag_label.text() == "搜广推 + LLM"

    def test_null_route_shows_unclassified(self, env, qtbot):
        t = env["repo"].create("任务", scheduled_date=TODAY)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert w.route_tag_label.text() == "未分类"


# ================= 20~23：今日筛选 =================

class TestTodayRouteFilter:
    def _window(self, qtbot, env):
        from app.ui.main_window import MainWindow

        w = MainWindow(
            task_service=env["ts"],
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
            manual_task_service=env["manual"],
            route_service=env["route_service"],
            route_plan_service=env["route_plan"],
        )
        qtbot.addWidget(w)
        return w

    def test_filter_options_and_filtering(self, qtbot, env):
        rl, plan, phase, topic = _second_route(env)
        a = env["repo"].create("Agent 任务", scheduled_date=TODAY,
                               source="generated", route_id=env["default"].id)
        b = env["manual"].create_todo("RL 任务", scheduled_date=TODAY,
                                      route_id=rl.id)
        c = env["manual"].create_todo("未分类任务", scheduled_date=TODAY)
        w = self._window(qtbot, env)

        # 默认全部
        assert w.route_filter_combo.itemText(0) == "全部路线"
        assert {wd.task().id for wd in w._task_widgets} == {a.id, b.id, c.id}

        # 筛强化学习
        idx = w.route_filter_combo.findData(rl.id)
        w.route_filter_combo.setCurrentIndex(idx)
        assert {wd.task().id for wd in w._task_widgets} == {b.id}

        # 筛未分类
        idx = w.route_filter_combo.findData("none")
        w.route_filter_combo.setCurrentIndex(idx)
        assert {wd.task().id for wd in w._task_widgets} == {c.id}

        # 筛选不改任务
        assert env["repo"].get(a.id) is not None
        assert env["repo"].get(b.id) is not None

    def test_filter_does_not_change_status(self, qtbot, env):
        t = env["manual"].create_todo("x", scheduled_date=TODAY)
        w = self._window(qtbot, env)
        w.route_filter_combo.setCurrentIndex(w.route_filter_combo.findData("none"))
        assert env["ts"].get_status(t.id) == "active"

    def test_route_stats_completion(self, qtbot, env):
        rl, *_ = _second_route(env)
        a = env["manual"].create_todo("a", scheduled_date=TODAY, route_id=rl.id)
        b = env["manual"].create_todo("b", scheduled_date=TODAY, route_id=rl.id)
        c = env["manual"].create_todo("c", scheduled_date=TODAY, route_id=rl.id)
        env["ts"].complete_task(a.id)
        env["ts"].complete_task(b.id)
        w = self._window(qtbot, env)
        w.route_filter_combo.setCurrentIndex(w.route_filter_combo.findData(rl.id))
        assert "完成 2 / 3" in w.route_stats_label.text()

    def test_cancelled_not_in_filter_stats(self, qtbot, env):
        rl, *_ = _second_route(env)
        a = env["manual"].create_todo("a", scheduled_date=TODAY, route_id=rl.id)
        b = env["manual"].create_todo("b", scheduled_date=TODAY, route_id=rl.id)
        env["ts"].complete_task(a.id)
        env["ts"].cancel_task(b.id)
        w = self._window(qtbot, env)
        w.route_filter_combo.setCurrentIndex(w.route_filter_combo.findData(rl.id))
        assert "完成 1 / 1" in w.route_stats_label.text()


# ================= 22~24：AddLearningTaskDialog 路线 =================

class TestAddDialogRoutes:
    def test_route_options_and_scoped_topics(self, env, qtbot):
        rl, plan, phase, topic = _second_route(env)
        default_topics = env["plan_repo"].list_topics_by_route(env["default"].id)
        dialog = AddLearningTaskDialog(
            routes=[{"id": env["default"].id, "name": env["default"].name},
                    {"id": rl.id, "name": rl.name}],
            topics_by_route={
                env["default"].id: [
                    {"id": t.id, "name": t.name} for t in default_topics
                ],
                rl.id: [{"id": topic.id, "name": topic.name}],
            },
            default_date=TODAY,
        )
        qtbot.addWidget(dialog)
        # 选强化学习 → topic 只有 MDP
        dialog.route_combo.setCurrentIndex(dialog.route_combo.findData(rl.id))
        names = [dialog.topic_combo.itemText(i)
                 for i in range(dialog.topic_combo.count())]
        assert names == ["MDP"]
        # 选未分类 → 无可关联 topic
        dialog.route_combo.setCurrentIndex(
            dialog.route_combo.findData(None)
        )
        assert dialog.topic_combo.count() == 0
        assert dialog.topic_radio.isEnabled() is False

    def test_payload_includes_route(self, env, qtbot):
        rl, *_ = _second_route(env)
        dialog = AddLearningTaskDialog(
            routes=[{"id": rl.id, "name": rl.name}], default_date=TODAY
        )
        qtbot.addWidget(dialog)
        dialog.title_edit.setText("看 STL 视频")
        dialog.route_combo.setCurrentIndex(dialog.route_combo.findData(rl.id))
        payload = dialog.result_payload()
        assert payload["route_id"] == rl.id


# ================= 23~27：manual 任务路线 =================

class TestManualRoute:
    def test_todo_with_route_no_kp(self, env):
        rl, *_ = _second_route(env)
        t = env["manual"].create_todo("看 STL", scheduled_date=TODAY,
                                      route_id=rl.id)
        assert t.route_id == rl.id
        assert t.knowledge_point_id is None

    def test_linked_topic_route_forced_from_topic(self, env):
        rl, plan, phase, topic = _second_route(env)
        # 即使误传别的 route，也应以 topic 的 route 为准
        t = env["manual"].create_knowledge_task(
            "MDP 复习", topic_id=topic.id, scheduled_date=TODAY,
            route_id=env["default"].id,
        )
        assert t.route_id == rl.id
        assert t.topic_id == topic.id
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert kp["route_id"] == rl.id

    def test_manual_kp_route_and_multiroute_isolation(self, env):
        rl, *_ = _second_route(env)
        kp_rl = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl.id
        )
        kp_rl2 = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl.id
        )
        kp_none = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=None
        )
        assert kp_rl["id"] == kp_rl2["id"]
        assert kp_rl["id"] != kp_none["id"]
        assert kp_rl["route_id"] == rl.id
        assert kp_none["route_id"] is None

    def test_manual_temp_knowledge_task_route(self, env):
        rl, *_ = _second_route(env)
        t = env["manual"].create_knowledge_task(
            "强化学习基础", scheduled_date=TODAY, route_id=rl.id
        )
        assert t.route_id == rl.id
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert kp["route_id"] == rl.id


# ================= 29：昨日确认路线显示 =================

class TestPastDialogRoute:
    def test_past_dialog_shows_route_labels(self, env, qtbot):
        rl, *_ = _second_route(env)
        t1 = env["manual"].create_todo("RL 任务", scheduled_date=YESTERDAY,
                                       route_id=rl.id)
        t2 = env["manual"].create_todo("未分类任务", scheduled_date=YESTERDAY)
        dialog = PastTaskConfirmationDialog(
            [env["repo"].get(t1.id), env["repo"].get(t2.id)],
            route_names={rl.id: rl.name},
        )
        qtbot.addWidget(dialog)
        from PySide6.QtWidgets import QLabel

        texts = [l.text() for l in dialog.findChildren(QLabel)]
        assert f"【{rl.name}】" in texts
        assert "【未分类】" in texts


# ================= 36~38：暂停 / 恢复自动规划 =================

class TestPausePlanning:
    def test_pause_blocks_auto_generation(self, env):
        env["route_service"].pause_planning(env["default"].id)
        ds = DateService(env["repo"], study_plan_service=env["sps"])
        result = ds.process_date_transition(TODAY)
        assert result["generated"] == []
        assert env["repo"].list_by_date(TODAY) == []

    def test_pause_still_allows_manual(self, env):
        env["route_service"].pause_planning(env["default"].id)
        t = env["manual"].create_todo("刷题", scheduled_date=TODAY)
        assert t.status == "active"

    def test_resume_does_not_generate_immediately(self, env):
        env["route_service"].pause_planning(env["default"].id)
        DateService(env["repo"], study_plan_service=env["sps"]).process_date_transition(TODAY)
        env["route_service"].resume_planning(env["default"].id)
        # 仅 resume 不应自动生成
        assert env["repo"].list_by_date(TODAY) == []
        # 显式重新生成才产生
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["generated"]

    def test_archived_route_does_not_generate(self, env):
        env["route_service"].archive_route(env["default"].id)
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["generated"] == []



# ================= 39~42：不回归 =================

class TestNoRegression:
    def test_second_route_does_not_pollute_planner(self, env):
        _second_route(env)
        phase = env["sps"].get_current_phase(TODAY)
        assert "MDP" not in [t.name for t in phase.topics]
        res = env["sps"].generate_daily_tasks(TODAY)
        assert all(t.route_id == env["default"].id for t in res["generated"])

    def test_assessment_supports_route_manual_knowledge(self, env):
        rl, *_ = _second_route(env)
        t = env["manual"].create_knowledge_task(
            "PPO 基础", scheduled_date=TODAY, route_id=rl.id
        )
        attempt = env["arepo"].create_attempt(t.knowledge_point_id, "{}",
                                              task_id=t.id)
        assert attempt["knowledge_point_id"] == t.knowledge_point_id


    def test_historical_null_manual_kp_not_guessed(self, env):
        kp = env["arepo"].create_knowledge_point("历史基础")
        assert kp["route_id"] is None
