"""Phase F：RouteDetail 关联技能 UI。

覆盖需求 54 的 23~26、30~31。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.skill_service import SkillService
from app.ui import routes_page as routes_mod
from app.ui.routes_page import RouteDetailDialog


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo, skill_repo=skill_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    skill_service = SkillService(skill_repo, plan_repo=plan_repo,
                                 assessment_repo=arepo, route_repo=route_repo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "skill_repo": skill_repo, "route_repo": route_repo,
        "route_service": route_service, "route_plan": route_plan,
        "skill_service": skill_service,
    }


def _route(env, name, topic=None, linked_skill=None):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    if topic:
        phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
        t = env["route_plan"].add_topic(rl.id, phase.id, topic)
        if linked_skill:
            env["skill_repo"].update(linked_skill["id"], linked_topics=[t.id])
        return rl, t
    return rl, None


def _detail(qtbot, env, route):
    dlg = RouteDetailDialog(route, env["route_service"], env["route_plan"],
                            skill_service=env["skill_service"])
    qtbot.addWidget(dlg)
    return dlg


class _FakePicker:
    selected = None
    accepted = True

    def __init__(self, names, parent=None):
        self.names = names

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return (QDialog.DialogCode.Accepted if self.accepted
                else QDialog.DialogCode.Rejected)


class TestRouteSkillsUI:
    def test_bound_skill_shown(self, env, qtbot):
        rl, _ = _route(env, "强化学习")
        s = env["skill_repo"].create("PyTorch", tier="S")
        env["route_repo"].assign_skill(rl.id, s["id"])
        dlg = _detail(qtbot, env, rl)
        from PySide6.QtWidgets import QLabel

        texts = [l.text() for l in dlg.findChildren(QLabel)]
        assert any("PyTorch" in t for t in texts)
        assert "关联技能" in texts

    def test_assign_existing_skill(self, env, qtbot, monkeypatch):
        rl, _ = _route(env, "强化学习")
        env["skill_repo"].create("PyTorch", tier="S")
        monkeypatch.setattr(routes_mod, "SkillPickerDialog", _FakePicker)
        _FakePicker.selected = "PyTorch"
        _FakePicker.accepted = True
        dlg = _detail(qtbot, env, rl)
        dlg._on_assign_skill()
        s = env["skill_repo"].get_by_name("PyTorch")
        assert env["route_repo"].list_skill_ids(rl.id) == [s["id"]]

    def test_remove_relation_keeps_skill(self, env, qtbot):
        rl, _ = _route(env, "强化学习")
        s = env["skill_repo"].create("PyTorch", tier="S")
        env["route_repo"].assign_skill(rl.id, s["id"])
        dlg = _detail(qtbot, env, rl)
        dlg._on_remove_skill(s)
        assert env["route_repo"].list_skill_ids(rl.id) == []
        assert env["skill_repo"].get_by_name("PyTorch") is not None

    def test_remove_blocked_when_topic_linked(self, env, qtbot, monkeypatch):
        s = env["skill_repo"].create("PyTorch", tier="S")
        rl, topic = _route(env, "强化学习", topic="PyTorch 基础",
                           linked_skill=s)
        env["route_repo"].assign_skill(rl.id, s["id"])
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        dlg = _detail(qtbot, env, rl)
        dlg._on_remove_skill(s)
        assert warnings
        assert env["route_repo"].list_skill_ids(rl.id) == [s["id"]]

    def test_skill_bound_to_multiple_routes(self, env, qtbot):
        a, _ = _route(env, "强化学习")
        b, _ = _route(env, "搜广推 + LLM")
        s = env["skill_repo"].create("PyTorch", tier="S")
        env["route_repo"].assign_skill(a.id, s["id"])
        env["route_repo"].assign_skill(b.id, s["id"])
        assert env["route_repo"].list_route_ids_for_skill(s["id"]) == [a.id, b.id]
        assert len(env["skill_repo"].list_all()) == 1

    def test_cpp_route_does_not_see_pytorch(self, env, qtbot):
        a, _ = _route(env, "强化学习")
        cpp, _ = _route(env, "C++")
        s = env["skill_repo"].create("PyTorch", tier="S")
        env["route_repo"].assign_skill(a.id, s["id"])
        assert env["route_repo"].list_skill_ids(cpp.id) == []
