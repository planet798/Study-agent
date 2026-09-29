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


class TestReplanSafety:
    def _generated(self, env, title, route_id=None, date=TODAY):
        return env["repo"].create(title, scheduled_date=date, source="generated",
                                   task_type="new", route_id=route_id)

    def test_eligibility_and_historyless_replacement(self, env):
        route = env["default"].id
        replace = self._generated(env, "replace", route)
        done = self._generated(env, "done", route)
        env["ts"].complete_task(done.id)
        not_done = self._generated(env, "not_done", route)
        env["ts"].mark_not_done(not_done.id, "未完成")
        cancelled = self._generated(env, "cancelled", route)
        env["ts"].cancel_task(cancelled.id)
        manual = env["repo"].create("manual", scheduled_date=TODAY,
                                     source="manual", route_id=route)
        other_date = self._generated(env, "tomorrow", route, NEXT)
        review = env["repo"].create("review", scheduled_date=TODAY,
                                     source="generated", task_type="review", route_id=route)
        other_route, _ = _add_route(env, "Other", ["Topic"])
        other = self._generated(env, "other route", other_route.id)
        result = env["ts"].prepare_replan(TODAY, route_ids=(route,))
        assert result == {"removed_ids": [replace.id], "preserved_ids": []}
        assert env["repo"].get(replace.id) is None
        for task, status in ((done, "done"), (not_done, "not_done"),
                             (cancelled, "cancelled"), (manual, "active"),
                             (other_date, "active"), (review, "active"),
                             (other, "active")):
            assert env["repo"].get(task.id).status == status

    def test_session_and_messages_survive_single_route_ui_replan(self, qtbot, env, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from app.agent.session import AgentSessionService
        from app.database.agent_repository import AgentRepository
        from app.services.daily_planner_service import DailyPlannerService
        from app.ui.main_window import MainWindow

        task = self._generated(env, "studied legacy task")
        other_route, _ = _add_route(env, "Unrelated", ["Other topic"])
        unrelated = self._generated(env, "different route", other_route.id)
        sessions = AgentSessionService(AgentRepository(env["conn"]), env["ts"])
        sid = sessions.start_or_resume(task.id)["id"]
        sessions.append_user_message(sid, "old question")
        sessions.append_assistant_message(sid, "old answer")

        class Planner:
            def is_configured(self):
                return True

        planner_service = DailyPlannerService(env["repo"], env["plan_repo"],
                                              planner=Planner(), study_plan_service=env["sps"])
        monkeypatch.setattr(planner_service, "generate_next_day_plan",
                            lambda *_args, **_kwargs: {"created": []})
        w = MainWindow(env["ts"], DateService(env["repo"]),
                       today_provider=lambda: TODAY, study_plan_service=env["sps"],
                       daily_planner_service=planner_service,
                       agent_session_service=sessions, agent_runtime_factory=lambda conn: None)
        qtbot.addWidget(w)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        w._on_replan()
        assert env["repo"].get(task.id) == task
        assert env["repo"].get(unrelated.id).status == "active"
        assert sessions.get(sid)["task_id"] == task.id
        assert [m["content"] for m in sessions.messages(sid)] == ["old question", "old answer"]
        assert w.sidebar.session_item(sid) is not None
        w.sidebar.session_item(sid).click()
        assert w.agent_workspace_page.current_session_id == sid
        sessions.append_user_message(sid, "follow-up")
        assert sessions.messages(sid)[-1]["content"] == "follow-up"
        w.close()

    @pytest.mark.parametrize("action", ["request_complete_current_task",
                                        "request_start_assessment",
                                        "request_save_learning_note"])
    def test_formal_approval_still_available_after_replan(self, env, action):
        import json
        from app.agent.approval.service import AgentApprovalService
        from app.agent.session import AgentSessionService
        from app.database.agent_approval_repository import AgentApprovalRepository
        from app.database.agent_repository import AgentRepository

        kp_id = None
        if action == "request_start_assessment":
            kp_id = env["arepo"].create_knowledge_point("Replan KP")["id"]
        task = env["repo"].create("study", scheduled_date=TODAY,
                                   source="generated", task_type="new",
                                   route_id=env["default"].id, knowledge_point_id=kp_id)
        agent_repo = AgentRepository(env["conn"])
        sessions = AgentSessionService(agent_repo, env["ts"])
        sid = sessions.start_or_resume(task.id)["id"]
        args = ({"title": "note", "content": "learning"}
                if action == "request_save_learning_note" else {})
        call_id = "replan-call"
        assistant = agent_repo.add_message(
            sid, "assistant", "",
            tool_calls_json=json.dumps([{"id": call_id, "name": action,
                                         "arguments": json.dumps(args)}]),
        )
        assert env["ts"].prepare_replan(TODAY, route_ids=(task.route_id,)) == {
            "removed_ids": [], "preserved_ids": [task.id],
        }
        assert env["repo"].get(task.id) == task
        approval = AgentApprovalService(AgentApprovalRepository(env["conn"]), env["ts"])
        requested = getattr(approval, action)(sid, task.id, assistant["id"], call_id,
                                               *([args] if args else []))
        assert requested["status"] == "pending"
        if action == "request_complete_current_task":
            executed = approval.approve_and_execute(requested["approval_id"])
            assert executed["status"] == "executed"
            assert env["ts"].get_status(task.id) == "done"
            assert sessions.get(sid)["status"] == "active"

    def test_assessment_reference_keeps_active_task(self, env):
        kp = env["arepo"].create_knowledge_point("Referenced KP")
        task = env["repo"].create("assessment task", scheduled_date=TODAY,
                                   source="generated", task_type="new",
                                   route_id=env["default"].id,
                                   knowledge_point_id=kp["id"])
        attempt = env["arepo"].create_attempt(kp["id"], "{}", task_id=task.id)
        result = env["ts"].prepare_replan(TODAY, route_ids=(task.route_id,))
        assert result == {"removed_ids": [], "preserved_ids": [task.id]}
        assert env["repo"].get(task.id) == task
        assert env["arepo"].get_attempt(attempt["id"])["task_id"] == task.id

    def test_preserved_topic_is_existing_commitment(self, env):
        from app.agent.session import AgentSessionService
        from app.database.agent_repository import AgentRepository
        env["route_service"].pause_planning(env["default"].id)
        route, _ = _add_route(env, "Topic route", ["Only topic"])
        topic = env["plan_repo"].list_topics_by_route(route.id)[0]
        task = env["repo"].create("studied topic", scheduled_date=TODAY,
                                   source="generated", task_type="new",
                                   route_id=route.id, topic_id=topic.id)
        AgentSessionService(AgentRepository(env["conn"]), env["ts"]).start_or_resume(task.id)
        prepared = env["ts"].prepare_replan(TODAY, route_ids=(route.id,))
        assert prepared["preserved_ids"] == [task.id]
        env["sched"].generate(TODAY, force=True)
        equivalent = [t for t in env["repo"].list_by_date(TODAY)
                      if t.topic_id == topic.id and t.route_id == route.id]
        assert [t.id for t in equivalent] == [task.id]
        assert env["repo"].get(task.id).status == "active"

    @pytest.mark.parametrize("kind", ["managed", "local"])
    def test_workspace_binding_and_contents_survive(self, env, tmp_path, monkeypatch, kind):
        from app.database.task_workspace_repository import TaskWorkspaceRepository
        from app.services.workspace_service import TaskWorkspaceService
        import app.services.workspace_service as workspace_module

        task = self._generated(env, "workspace", env["default"].id)
        service = TaskWorkspaceService(TaskWorkspaceRepository(env["conn"]), env["ts"])
        monkeypatch.setattr(workspace_module, "default_sandbox_workspace_root",
                            lambda: tmp_path / "managed")
        if kind == "managed":
            service.use_managed(task.id)
            folder = service.ensure_managed_directory(task.id)
        else:
            folder = tmp_path / "local"
            folder.mkdir()
            service.bind_local(task.id, folder)
        (folder / "keep.txt").write_text("unchanged", encoding="utf-8")
        before = service.get(task.id)
        result = env["ts"].prepare_replan(TODAY, route_ids=(env["default"].id,))
        assert result["preserved_ids"] == [task.id]
        assert env["repo"].get(task.id) == task
        assert service.get(task.id) == before
        assert (folder / "keep.txt").read_text(encoding="utf-8") == "unchanged"

    def test_paused_route_remains_untouched_via_ui(self, qtbot, env, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        active, _ = _add_route(env, "Active", ["A topic"])
        paused, _ = _add_route(env, "Paused", ["B topic"])
        env["route_service"].pause_planning(paused.id)
        a = self._generated(env, "A1", active.id)
        b = self._generated(env, "B1", paused.id)
        w = TestReplanButton()._window(qtbot, env)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        w._on_replan()
        assert env["repo"].get(a.id) is None
        assert env["repo"].get(b.id).status == "active"
        w.close()

    def test_preparation_is_atomic_on_failure(self, env):
        import sqlite3
        route = env["default"].id
        first = self._generated(env, "first delete", route)
        doomed = self._generated(env, "second delete", route)
        protected = self._generated(env, "third protected", route)
        from app.agent.session import AgentSessionService
        from app.database.agent_repository import AgentRepository
        AgentSessionService(AgentRepository(env["conn"]), env["ts"]).start_or_resume(protected.id)
        env["conn"].execute(
            f"CREATE TRIGGER fail_replan_delete BEFORE DELETE ON tasks "
            f"WHEN OLD.id = {doomed.id} BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            env["ts"].prepare_replan(TODAY, route_ids=(route,))
        assert env["repo"].get(first.id).status == "active"
        assert env["repo"].get(doomed.id).status == "active"
        assert env["repo"].get(protected.id).status == "active"
