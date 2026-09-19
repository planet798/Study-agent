"""Phase F：Route Draft Preview / RouteDetail 持久化流程。

覆盖需求 54 的 11~16、46。
"""

from __future__ import annotations

import pytest

from app.ai.schemas import parse_route_draft_from_json
from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.ui import routes_page as routes_mod
from app.ui.route_builder_dialogs import RouteDraftPreviewDialog
from app.ui.routes_page import RouteDetailDialog

TODAY = "2026-09-15"

DRAFT = {
    "route_name": "强化学习", "plan_name": "强化学习学习计划", "summary": "s",
    "phases": [
        {"name": "RL 基础", "goal": "g", "order": 1, "topics": [
            {"name": "MDP", "description": "目标/核心/实践/标准",
             "estimated_minutes": 45, "priority": 3, "order": 1},
            {"name": "Policy", "description": "目标/核心/实践/标准",
             "estimated_minutes": 45, "priority": 3, "order": 2}]},
        {"name": "Value-based", "goal": "g", "order": 2, "topics": [
            {"name": "Q-Learning", "description": "目标/核心/实践/标准",
             "estimated_minutes": 45, "priority": 3, "order": 1},
            {"name": "DQN", "description": "目标/核心/实践/标准",
             "estimated_minutes": 60, "priority": 4, "order": 2}]},
    ],
}


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan,
    }


def _draft():
    import json
    return parse_route_draft_from_json(json.dumps(DRAFT, ensure_ascii=False))


# ================= Preview 编辑 =================

class TestPreviewEditing:
    def test_edit_plan_and_phase_and_topic(self, qtbot):
        dlg = RouteDraftPreviewDialog(_draft())
        qtbot.addWidget(dlg)
        dlg.plan_name_edit.setText("我的 RL 计划")
        dlg._phase_widgets[0]["name"].setText("基础阶段")
        dlg._phase_widgets[0]["topics"][0]["minutes"].setValue(60)
        dlg._phase_widgets[0]["topics"][0]["description"].setPlainText("新说明")
        out = dlg.draft()
        assert out.plan_name == "我的 RL 计划"
        assert out.phases[0].name == "基础阶段"
        assert out.phases[0].topics[0].estimated_minutes == 60
        assert out.phases[0].topics[0].description == "新说明"

    def test_add_and_delete_topic(self, qtbot):
        dlg = RouteDraftPreviewDialog(_draft())
        qtbot.addWidget(dlg)
        dlg._on_add_topic(0)      # phase0 现在 3 topics
        assert len(dlg.draft().phases[0].topics) == 3
        dlg._on_delete_topic(0, 0)
        assert len(dlg.draft().phases[0].topics) == 2

    def test_delete_phase_min_one(self, qtbot, monkeypatch):
        dlg = RouteDraftPreviewDialog(_draft())
        qtbot.addWidget(dlg)
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        # RouteDraftPreviewDialog imports show_warning from .dialogs directly
        from app.ui import route_builder_dialogs as rbd
        monkeypatch.setattr(rbd, "show_warning",
                            lambda *a, **k: warnings.append(a))
        dlg._on_delete_phase(0)
        assert len(dlg.draft().phases) == 1
        dlg._on_delete_phase(0)
        assert warnings
        assert len(dlg.draft().phases) == 1

    def test_add_phase(self, qtbot):
        dlg = RouteDraftPreviewDialog(_draft())
        qtbot.addWidget(dlg)
        dlg._on_add_phase()
        assert len(dlg.draft().phases) == 3

    def test_cancel_writes_nothing(self, qtbot, env):
        rl = env["route_service"].create_learning_route("强化学习")
        dlg = RouteDraftPreviewDialog(_draft())
        qtbot.addWidget(dlg)
        dlg.reject()
        assert env["plan_repo"].get_plan_by_route(rl.id) is None


# ================= RouteDetail 持久化 =================

class _FakePreview:
    payload = None
    accepted = True
    replace_empty = False

    def __init__(self, draft, mode="no_plan", parent=None):
        self._draft = draft
        self._mode = mode

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return (QDialog.DialogCode.Accepted if self.accepted
                else QDialog.DialogCode.Rejected)

    def draft(self):
        return self.payload if self.payload is not None else self._draft

    @property
    def replace_empty(self):
        return self._replace_empty

    _replace_empty = False


class TestRouteDetailPersist:
    def _detail(self, qtbot, env, route):
        dlg = RouteDetailDialog(route, env["route_service"],
                                env["route_plan"])
        qtbot.addWidget(dlg)
        return dlg

    def test_confirm_creates_plan(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("强化学习")
        monkeypatch.setattr(routes_mod, "RouteDraftPreviewDialog", _FakePreview)
        _FakePreview.accepted = True
        _FakePreview.payload = None
        dlg = self._detail(qtbot, env, rl)
        dlg._on_draft_ready(_draft())
        assert env["plan_repo"].get_plan_by_route(rl.id) is not None
        assert len(env["plan_repo"].list_topics_by_route(rl.id)) == 4

    def test_cancel_creates_nothing(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("强化学习")
        monkeypatch.setattr(routes_mod, "RouteDraftPreviewDialog", _FakePreview)
        _FakePreview.accepted = False
        dlg = self._detail(qtbot, env, rl)
        dlg._on_draft_ready(_draft())
        assert env["plan_repo"].get_plan_by_route(rl.id) is None

    def test_blocked_plan_warns(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        ph = env["route_plan"].add_phase(rl.id, "已有")
        env["route_plan"].add_topic(rl.id, ph.id, "已有 Topic")
        warnings = []
        monkeypatch.setattr(routes_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))
        dlg = self._detail(qtbot, env, rl)
        dlg._on_draft_ready(_draft())
        assert warnings
        # 原结构未被破坏
        assert len(env["plan_repo"].list_topics_by_route(rl.id)) == 1

    def test_empty_plan_replace_via_preview(self, qtbot, env, monkeypatch):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        monkeypatch.setattr(routes_mod, "RouteDraftPreviewDialog", _FakePreview)
        _FakePreview.accepted = True
        _FakePreview.payload = None
        _FakePreview._replace_empty = True
        dlg = self._detail(qtbot, env, rl)
        dlg._on_draft_ready(_draft())
        assert len(env["plan_repo"].list_topics_by_route(rl.id)) == 4
