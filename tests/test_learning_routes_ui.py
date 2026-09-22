"""Phase C：学习路线 UI 测试（页面 / 创建 / 暂停 / 归档）。

覆盖需求 35 的 1~11。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import (
    LearningRouteService,
    RouteValidationError,
)
from app.services.route_plan_service import RoutePlanService
from app.ui import routes_page as routes_mod
from app.ui.routes_page import LearningRoutesPage, RouteDetailDialog

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


def _page(qtbot, env):
    page = LearningRoutesPage(env["route_service"], route_plan_service=env["route_plan"])
    qtbot.addWidget(page)
    return page


class _FakeDialog:
    payload: dict = {}

    def __init__(self, *a, **k):
        pass

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted

    def result_payload(self):
        return dict(self.payload)


# ================= 1~2：导航 / 页面 =================

class TestRoutesPage:
    def test_main_window_nav_has_routes(self, qtbot, env, conn):
        from app.services.date_service import DateService
        from app.services.manual_task_service import ManualTaskService
        from app.ui.main_window import MainWindow

        repo = env["repo"]
        w = MainWindow(
            task_service=__import__("app.services.task_service",
                                    fromlist=["TaskService"]).TaskService(repo),
            date_service=DateService(repo),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            manual_task_service=ManualTaskService(repo, assessment_repo=env["arepo"]),
            route_service=env["route_service"],
            route_plan_service=env["route_plan"],
        )
        qtbot.addWidget(w)
        assert w.nav_today_btn.text() == "今日"
        assert w.nav_routes_btn.text() == "学习路线"
        assert w.nav_monthly_btn.text() == "月度回顾"
        assert w.nav_routes_btn.isEnabled() is True
        w.nav_routes_btn.click()
        assert w.stack.currentIndex() == w.routes_page_index

    def test_page_shows_parent_and_child(self, qtbot, env):
        page = _page(qtbot, env)
        from PySide6.QtWidgets import QLabel

        texts = [l.text() for l in page.list_container.findChildren(QLabel)]
        assert "求职准备" in texts
        assert "搜广推 + LLM" in texts
        assert any("类型：分组" in t for t in texts)


# ================= 3~8：创建 / 调整 / 暂停 =================

class TestRouteActions:
    def test_create_learning_route_via_page(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        default = env["route_repo"].get_default_learning_route()
        monkeypatch.setattr(routes_mod, "CreateLearningRouteDialog", _FakeDialog)
        _FakeDialog.payload = {
            "name": "强化学习", "parent_id": default.parent_id,
            "goal": "RL 面试", "description": "", "priority": 5,
            "planning_enabled": False, "is_group": False,
        }
        page._on_create_route()
        created = env["route_repo"].get_by_name_under_parent(
            "强化学习", default.parent_id
        )
        assert created is not None
        assert created.priority == 5
        assert created.planning_enabled is False

    def test_create_group_via_page(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        monkeypatch.setattr(routes_mod, "CreateLearningRouteDialog", _FakeDialog)
        _FakeDialog.payload = {
            "name": "新分组", "parent_id": None, "goal": "", "description": "",
            "priority": 3, "planning_enabled": False, "is_group": True,
        }
        page._on_create_route()
        assert env["route_repo"].get_by_name_under_parent("新分组", None) is not None

    def test_duplicate_warns_and_not_created(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        default = env["route_repo"].get_default_learning_route()
        monkeypatch.setattr(routes_mod, "CreateLearningRouteDialog", _FakeDialog)
        _FakeDialog.payload = {
            "name": default.name, "parent_id": default.parent_id,
            "goal": "", "description": "", "priority": 3,
            "planning_enabled": False, "is_group": False,
        }
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        before = len(env["route_repo"].list_all())
        page._on_create_route()
        assert warnings and len(env["route_repo"].list_all()) == before

    def test_edit_priority(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        rl = env["route_service"].create_learning_route("RL", priority=2)
        monkeypatch.setattr(routes_mod, "EditLearningRouteDialog", _FakeDialog)
        _FakeDialog.payload = {
            "name": "RL", "goal": "g", "description": "", "priority": 5,
        }
        page._edit(rl)
        assert env["route_repo"].get(rl.id).priority == 5

    def test_pause_and_resume(self, qtbot, env):
        page = _page(qtbot, env)
        rl = env["route_service"].create_learning_route("RL")
        page._pause(rl)
        assert env["route_repo"].get(rl.id).planning_enabled is False
        page._resume(rl)
        assert env["route_repo"].get(rl.id).planning_enabled is True

    def test_archive_and_restore(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        rl = env["route_service"].create_learning_route("RL")
        monkeypatch.setattr(page, "_confirm_archive_dialog", lambda: True)
        page._archive(rl)
        assert env["route_repo"].get(rl.id).status == "archived"
        # 归档后默认树里隐藏；已归档区可见
        page.show_archived = True
        page.refresh()
        page._restore(env["route_repo"].get(rl.id))
        assert env["route_repo"].get(rl.id).status == "active"
        assert env["route_repo"].get(rl.id).planning_enabled is False

    def test_archive_cancelled_by_dialog(self, qtbot, env, monkeypatch):
        page = _page(qtbot, env)
        rl = env["route_service"].create_learning_route("RL")
        monkeypatch.setattr(page, "_confirm_archive_dialog", lambda: False)
        page._archive(rl)
        assert env["route_repo"].get(rl.id).status == "active"


# ================= 11：归档限制 / 不删数据 =================

class TestArchiveRules:
    def test_group_with_active_child_cannot_archive(self, env):
        route_repo = env["route_repo"]
        svc = env["route_service"]
        parent = svc.create_group("G")
        child = svc.create_learning_route("C", parent_id=parent.id)
        with pytest.raises(RouteValidationError):
            svc.archive_route(parent.id)
        # 先归档子路线后可归档父
        svc.archive_route(child.id)
        assert svc.archive_route(parent.id).status == "archived"

    def test_archive_keeps_structure_and_tasks(self, env):
        svc = env["route_service"]
        rl = svc.create_learning_route("RL")
        plan = env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "基础")
        topic = env["route_plan"].add_topic(rl.id, phase.id, "MDP")
        task = env["repo"].create("学习 MDP", scheduled_date=TODAY,
                                  topic_id=topic.id, route_id=rl.id)
        svc.archive_route(rl.id)
        assert env["plan_repo"].get_plan(plan.id) is not None
        assert env["plan_repo"].get_phase(phase.id) is not None
        assert env["plan_repo"].get_topic(topic.id) is not None
        assert env["repo"].get(task.id) is not None
