"""Phase C：手动路线 Plan/Phase/Topic 结构编辑测试。

覆盖需求 35 的 12~17、27、30~31。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService, RouteStructureError
from app.ui import routes_page as routes_mod
from app.ui.routes_page import RouteDetailDialog

TODAY = "2026-09-15"


@pytest.fixture()
def env(conn):
    route_repo = LearningRouteRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    return {
        "conn": conn, "route_repo": route_repo, "plan_repo": plan_repo,
        "repo": repo, "arepo": arepo,
        "route_service": LearningRouteService(route_repo),
        "route_plan": RoutePlanService(plan_repo, repo, arepo),
    }


def _detail(qtbot, env, route):
    dlg = RouteDetailDialog(route, env["route_service"], env["route_plan"])
    qtbot.addWidget(dlg)
    return dlg


class _FakeDialog:
    payload: dict = {}

    def __init__(self, *a, **k):
        pass

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    def result_payload(self):
        return dict(self.payload)


# ================= 12：创建手动 Plan =================

class TestManualPlan:
    def test_create_plan_for_route(self, qtbot, env):
        rl = env["route_service"].create_learning_route("强化学习")
        dlg = _detail(qtbot, env, rl)
        from PySide6.QtWidgets import QLabel

        assert "该路线还没有学习计划" in [
            l.text() for l in dlg.findChildren(QLabel)
        ]
        dlg._on_create_plan()
        plan = env["plan_repo"].get_plan_by_route(rl.id)
        assert plan is not None
        assert plan.route_id == rl.id
        assert plan.name == "强化学习学习计划"


# ================= 13~14：添加 Phase / Topic =================

class TestAddStructure:
    def test_add_phase(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        dlg = _detail(qtbot, env, rl)
        monkeypatch.setattr(routes_mod, "AddPhaseDialog", _FakeDialog)
        _FakeDialog.payload = {"name": "RL 基础", "goal": "MDP", "order_index": 1}
        dlg._on_add_phase()
        plan = env["plan_repo"].get_plan_by_route(rl.id)
        phases = env["plan_repo"].list_phases(plan.id)
        assert [p.name for p in phases] == ["RL 基础"]

    def test_add_topic(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "RL 基础")
        dlg = _detail(qtbot, env, rl)
        phase_obj = env["plan_repo"].get_phase(phase.id)
        phase_obj.topics = env["plan_repo"].list_topics(phase.id)
        monkeypatch.setattr(routes_mod, "AddTopicDialog", _FakeDialog)
        _FakeDialog.payload = {
            "name": "MDP", "description": "马尔可夫", "estimated_minutes": 45,
            "priority": 3, "order_index": 1,
        }
        dlg._on_add_topic(phase_obj)
        topics = env["plan_repo"].list_topics(phase.id)
        assert [t.name for t in topics] == ["MDP"]

    def test_add_topic_wrong_route_rejected(self, env):
        rl1 = env["route_service"].create_learning_route("A")
        rl2 = env["route_service"].create_learning_route("B")
        env["route_plan"].ensure_manual_plan(rl1.id, rl1.name)
        phase = env["route_plan"].add_phase(rl1.id, "阶段")
        with pytest.raises(RouteStructureError):
            env["route_plan"].add_topic(rl2.id, phase.id, "X")


# ================= 15：路线隔离 =================

class TestRouteTopicIsolation:
    def test_route_a_topic_not_in_route_b(self, env):
        rl_a = env["route_service"].create_learning_route("A")
        rl_b = env["route_service"].create_learning_route("B")
        env["route_plan"].ensure_manual_plan(rl_a.id, rl_a.name)
        env["route_plan"].ensure_manual_plan(rl_b.id, rl_b.name)
        pa = env["route_plan"].add_phase(rl_a.id, "PA")
        pb = env["route_plan"].add_phase(rl_b.id, "PB")
        env["route_plan"].add_topic(rl_a.id, pa.id, "OnlyA")
        env["route_plan"].add_topic(rl_b.id, pb.id, "OnlyB")
        assert [t.name for t in env["plan_repo"].list_topics_by_route(rl_a.id)] \
            == ["OnlyA"]
        assert [t.name for t in env["plan_repo"].list_topics_by_route(rl_b.id)] \
            == ["OnlyB"]


# ================= 16~17：删除规则 / 进度 =================

class TestDeleteAndProgress:
    def test_topic_with_task_cannot_delete(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "基础")
        topic = env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        env["repo"].create("学习 MDP", scheduled_date=TODAY, topic_id=topic.id,
                           route_id=rl.id)
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        dlg = _detail(qtbot, env, rl)
        dlg._on_delete_topic(env["plan_repo"].get_topic(topic.id))
        assert warnings
        assert env["plan_repo"].get_topic(topic.id) is not None

    def test_empty_topic_can_delete(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "基础")
        topic = env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        dlg = _detail(qtbot, env, rl)
        monkeypatch.setattr(dlg, "_confirm_delete_dialog", lambda text: True)
        dlg._on_delete_topic(env["plan_repo"].get_topic(topic.id))
        assert env["plan_repo"].get_topic(topic.id) is None

    def test_phase_with_topics_cannot_delete(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "基础")
        env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        dlg = _detail(qtbot, env, rl)
        dlg._on_delete_phase(env["plan_repo"].get_phase(phase.id))
        assert warnings
        assert env["plan_repo"].get_phase(phase.id) is not None

    def test_progress_counts_done_topics(self, env):
        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "基础")
        t1 = env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        env["route_plan"].add_topic(rl.id, phase.id, "Policy")
        done = env["repo"].create("学 MDP", scheduled_date=TODAY,
                                  topic_id=t1.id, route_id=rl.id)
        env["repo"].mark_done(done.id)
        progress = env["route_plan"].route_progress(rl.id)
        assert progress == {"done": 1, "total": 2, "phases": 1, "has_plan": True}


# ================= 30~31：第二路线不污染默认 Planner =================

class TestManualSecondRoute:
    def test_default_planner_isolated(self, env, conn):
        from app.services.study_plan_service import StudyPlanService

        route_repo = env["route_repo"]
        sps = StudyPlanService(
            env["repo"], env["plan_repo"], assessment_repo=env["arepo"],
            learning_route_repo=route_repo,
        )
        sps.ensure_default_plan()
        default = route_repo.get_default_learning_route()

        # 手动创建第二路线 RL plan/phase/topic
        rl = env["route_service"].create_learning_route(
            "强化学习", priority=5, planning_enabled=False
        )
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "RL 基础")
        topic = env["route_plan"].add_topic(rl.id, phase.id, "MDP")

        phase_now = sps.get_current_phase(TODAY)
        assert "MDP" not in [t.name for t in phase_now.topics]
        res = sps.generate_daily_tasks(TODAY)
        assert all(t.route_id == default.id for t in res["generated"])

    def test_manual_knowledge_on_second_route_assessable(self, env):
        from app.services.manual_task_service import ManualTaskService
        from app.services.study_plan_service import StudyPlanService

        rl = env["route_service"].create_learning_route("RL")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "RL 基础")
        topic = env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        sps = StudyPlanService(env["repo"], env["plan_repo"],
                               assessment_repo=env["arepo"],
                               learning_route_repo=env["route_repo"])
        manual = ManualTaskService(env["repo"], assessment_repo=env["arepo"],
                                   study_plan_service=sps)
        t = manual.create_knowledge_task("MDP", topic_id=topic.id,
                                         scheduled_date=TODAY)
        assert t.route_id == rl.id
        assert t.knowledge_point_id is not None
        assert env["arepo"].get_knowledge_point(t.knowledge_point_id)["route_id"] \
            == rl.id
