"""Phase D：route-aware PlannerDecision / force replan / phase 隔离。

覆盖需求 46 的 33~43、46~49 与关键回归。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.date_service import DateService
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_scheduler import GlobalDailyScheduler
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.utils.date_utils import add_days

TODAY = "2026-09-15"
NEXT = "2026-09-16"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    sched = GlobalDailyScheduler(repo, plan_repo, route_repo,
                                 assessment_repo=arepo, budget=3)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "sps": sps, "sched": sched,
        "default": route_repo.get_default_learning_route(),
        "ts": TaskService(repo),
    }


def _add_route(env, name, topics, priority=5, planning_enabled=True):
    rl = env["route_service"].create_learning_route(
        name, priority=priority, planning_enabled=planning_enabled
    )
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    for i, t in enumerate(topics):
        env["route_plan"].add_topic(rl.id, phase.id, t, order_index=i)
    return rl, phase


# ================= 33~35：PlannerDecision route 化 =================

class TestPlannerDecision:
    def test_decision_per_route(self, env):
        rl, _ = _add_route(env, "强化学习", ["MDP", "Policy"])
        env["sched"].generate(TODAY)
        rl_dec = env["conn"].execute(
            "SELECT COUNT(*) FROM planner_decisions WHERE date=? AND route_id=?",
            (TODAY, rl.id),
        ).fetchone()[0]
        default_dec = env["conn"].execute(
            "SELECT COUNT(*) FROM planner_decisions WHERE date=? AND route_id=?",
            (TODAY, env["default"].id),
        ).fetchone()[0]
        assert rl_dec >= 1 and default_dec >= 1

    def test_historical_default_decision_compatible(self, env):
        env["conn"].execute(
            "INSERT INTO planner_decisions (date, current_phase_id,"
            " input_context, ai_response, accepted_tasks, source, created_at,"
            " route_id) VALUES (?, NULL, '{}', '{}', '[]', 'ai', 'x', ?)",
            (TODAY, env["default"].id),
        )
        env["conn"].commit()
        assert env["sched"]._make_route_planner(env["default"].id) \
            .latest_plan_for_date(TODAY, route_id=env["default"].id) is not None

    def test_force_replan_generates_for_multiple_routes(self, env):
        rl, _ = _add_route(env, "强化学习", ["MDP", "Policy", "Value"])
        env["sched"].generate(TODAY)
        # 模拟“重新规划今天”：删除 active generated
        for t in env["repo"].list_by_date(TODAY):
            if t.status == "active" and t.source == "generated":
                env["repo"].delete(t.id)
        out = env["sched"].generate(TODAY, force=True)
        routes = {env["repo"].get(i).route_id for i in out["created_ids"]}
        assert rl.id in routes


# ================= 36~38：force 保护 / cancelled =================

class TestForceReplan:
    def test_force_keeps_done_and_manual(self, env):
        done = env["repo"].create("已完成", scheduled_date=TODAY,
                                  source="generated", task_type="new",
                                  route_id=env["default"].id)
        env["repo"].mark_done(done.id)
        manual = env["repo"].create("手动", scheduled_date=TODAY,
                                    source="manual", task_type="manual")
        gen = env["repo"].create("待重排", scheduled_date=TODAY,
                                 source="generated", task_type="new",
                                 route_id=env["default"].id)
        # 仅删除 active generated（与 MainWindow._on_replan 一致）
        env["repo"].delete(gen.id)
        env["sched"].generate(TODAY, force=True)
        assert env["repo"].get(done.id).status == "done"
        assert env["repo"].get(manual.id) is not None
        assert env["repo"].get(done.id) is not None

    def test_done_generated_consumes_today_budget(self, env):
        done = env["repo"].create("已完成 Agent", scheduled_date=TODAY,
                                  source="generated", task_type="new",
                                  route_id=env["default"].id)
        env["repo"].mark_done(done.id)
        out = env["sched"].generate(TODAY)
        assert out["existing"] == 1
        assert len(out["created_ids"]) <= 2

    def test_cancelled_topic_not_regenerated_today(self, env):
        env["route_service"].pause_planning(env["default"].id)
        rl, _ = _add_route(env, "强化学习", ["MDP", "Policy", "Value"])
        first = env["sched"].generate(TODAY)
        task = env["repo"].get(first["created_ids"][0])
        cancelled_title = task.title
        env["ts"].cancel_task(task.id)
        # 删除其它 active generated，腾出 budget
        for t in env["repo"].list_by_date(TODAY):
            if t.status == "active" and t.source == "generated":
                env["repo"].delete(t.id)
        out = env["sched"].generate(TODAY, force=True)
        titles = [env["repo"].get(i).title for i in out["created_ids"]]
        assert cancelled_title not in titles

    def test_cancelled_topic_reborn_next_day(self, env):
        env["route_service"].pause_planning(env["default"].id)
        rl, _ = _add_route(env, "强化学习", ["MDP", "Policy", "Value"])
        first = env["sched"].generate(TODAY)
        task = env["repo"].get(first["created_ids"][0])
        title = task.title
        env["ts"].cancel_task(task.id)
        for t in env["repo"].list_by_date(TODAY):
            if t.status == "active" and t.source == "generated":
                env["repo"].delete(t.id)
        out = env["sched"].generate(NEXT, force=True)
        titles = [env["repo"].get(i).title for i in out["created_ids"]]
        assert title in titles


# ================= 40~43：phase / route 隔离 =================

class TestPhaseIsolation:
    def test_phase_progression_route_local(self, env):
        rl, _ = _add_route(env, "强化学习", ["MDP"])
        # 加第二个阶段
        plan = env["plan_repo"].get_plan_by_route(rl.id)
        ph2 = env["route_plan"].add_phase(rl.id, "RL 进阶")
        env["route_plan"].add_topic(rl.id, ph2.id, "PPO")
        # 完成第一阶段 topic
        mdp = [t for t in env["plan_repo"].list_topics_by_route(rl.id)
               if t.name == "MDP"][0]
        done = env["repo"].create("MDP", scheduled_date="2026-09-10",
                                  topic_id=mdp.id, route_id=rl.id)
        env["repo"].mark_done(done.id)
        planner = env["sched"]._make_route_planner(rl.id)
        phase = planner.study_plan_service.get_current_phase(TODAY)
        assert phase.name == "RL 进阶"

    def test_route_a_complete_does_not_advance_route_b(self, env):
        a, _ = _add_route(env, "RL", ["MDP"])
        b, ph_b = _add_route(env, "DS", ["Hash", "Tree"])
        mdp = env["plan_repo"].list_topics_by_route(a.id)[0]
        done = env["repo"].create("MDP", scheduled_date="2026-09-10",
                                  topic_id=mdp.id, route_id=a.id)
        env["repo"].mark_done(done.id)
        assert env["sched"].route_skip_reason(
            env["route_repo"].get(a.id), TODAY
        ) == "route_complete"
        planner_b = env["sched"]._make_route_planner(b.id)
        phase = planner_b.study_plan_service.get_current_phase(TODAY)
        assert phase.name == "DS 基础"

    def test_all_routes_paused_no_task(self, env):
        env["route_service"].pause_planning(env["default"].id)
        _add_route(env, "RL", ["MDP"], planning_enabled=False)
        out = env["sched"].generate(TODAY)
        assert out["created_ids"] == []
        assert out["plannable_route_ids"] == []

    def test_default_paused_rl_still_generates(self, env):
        env["route_service"].pause_planning(env["default"].id)
        rl, _ = _add_route(env, "RL", ["MDP", "Policy"], priority=5)
        out = env["sched"].generate(TODAY)
        assert out["created_ids"]
        assert all(env["repo"].get(i).route_id == rl.id
                   for i in out["created_ids"])


# ================= 47：MainWindow replan eligibility =================

class TestReplanButton:
    def _window(self, qtbot, env):
        from app.services.manual_task_service import ManualTaskService
        from app.ui.main_window import MainWindow

        ds = DateService(env["repo"], study_plan_service=env["sps"],
                         scheduler=env["sched"])
        w = MainWindow(
            task_service=env["ts"], date_service=ds,
            today_provider=lambda: TODAY, assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
            manual_task_service=ManualTaskService(
                env["repo"], assessment_repo=env["arepo"],
                study_plan_service=env["sps"]),
            route_service=env["route_service"],
            route_plan_service=env["route_plan"],
            scheduler=env["sched"],
        )
        qtbot.addWidget(w)
        return w

    def test_button_enabled_when_any_plannable(self, qtbot, env):
        env["route_service"].pause_planning(env["default"].id)
        _add_route(env, "RL", ["MDP"], priority=5)
        w = self._window(qtbot, env)
        assert w.planner_replan_btn.isEnabled() is True
        assert "多路线调度已启用" in w.planner_status_label.text()

    def test_button_disabled_when_none_plannable(self, qtbot, env):
        env["route_service"].pause_planning(env["default"].id)
        w = self._window(qtbot, env)
        assert w.planner_replan_btn.isEnabled() is False
        assert "没有可自动规划" in w.planner_status_label.text()

    def test_replan_runs_global_scheduler(self, qtbot, env, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        rl, _ = _add_route(env, "强化学习", ["MDP", "Policy", "Value"])
        w = self._window(qtbot, env)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        w._on_replan()
        titles = [t.title for t in env["repo"].list_by_date(TODAY)
                  if t.source == "generated"]
        assert titles  # 全局 Scheduler 生效

    def test_manual_task_survives_replan(self, qtbot, env, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        manual = env["repo"].create("手动任务", scheduled_date=TODAY,
                                    source="manual", task_type="manual")
        w = self._window(qtbot, env)
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        w._on_replan()
        assert env["repo"].get(manual.id) is not None


# ================= 48~55：关键回归 =================

class TestRegression:
    def test_cancelled_completion_rate_not_regressed(self, env):
        t = env["repo"].create("取消", scheduled_date=TODAY,
                               source="generated", task_type="new",
                               route_id=env["default"].id)
        env["ts"].cancel_task(t.id)
        stats = env["ts"].get_daily_stats(TODAY)
        assert stats["total"] == 0

    def test_review_and_assessment_not_regressed(self, env):
        topic = env["sps"].get_current_phase(TODAY).topics[0]
        kp = env["arepo"].get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, route_id=env["default"].id
        )
        attempt = env["arepo"].create_attempt(kp["id"], "{}")
        assert attempt["knowledge_point_id"] == kp["id"]

    def test_yesterday_confirmation_runs_before_scheduler(self, env):
        # 历史 active 任务应先被补确认逻辑发现（不被 Scheduler 提前生成）
        env["repo"].create("昨天", scheduled_date=add_days(TODAY, -1),
                           source="generated", task_type="new",
                           route_id=env["default"].id)
        from app.services.past_task_service import PastTaskConfirmationService

        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        assert len(svc.find_unresolved(TODAY)) == 1
