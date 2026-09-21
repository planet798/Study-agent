"""Phase 6：UI 测试（offscreen）——学习准备度 / 要求编辑 / Route 阻塞。"""

from __future__ import annotations

import pytest

from app.services.capability import EXPLAIN, IMPLEMENT, UNLEARNED
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    ensure_capability,
    make_requirement_project,
    topic_in_current_phase,
)


def _labels(widget):
    from PySide6.QtWidgets import QLabel, QPushButton, QRadioButton

    out = [lbl.text() for lbl in widget.findChildren(QLabel)]
    out += [b.text() for b in widget.findChildren(QPushButton)]
    out += [r.text() for r in widget.findChildren(QRadioButton)]
    return out


def _detail(env, project_id, qtbot):
    from app.ui.practice_page import PracticeProjectDetailDialog

    dlg = PracticeProjectDetailDialog(
        project_id, env.service, env.route_repo, env.skill_repo,
        env.plan_repo, None, readiness_service=env.readiness,
    )
    qtbot.addWidget(dlg)
    return dlg


class TestProjectDetailReadiness:
    def test_readiness_section(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        dlg = _detail(env, project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "【学习准备】" in joined
        assert "学习准备度：0 / 1 已满足" in joined
        assert "vLLM 与 PagedAttention" in joined
        assert "要求：能够写代码（3）" in joined
        assert "当前：未学习" in joined
        assert "能力缺口" in joined
        assert "设置学习要求" in joined

    def test_satisfied_shown(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        ensure_capability(env, topics[0].id, 4)  # EXPERIMENT >= IMPLEMENT
        dlg = _detail(env, project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "学习准备度：1 / 1 已满足" in joined
        assert "✓ 已满足" in joined

    def test_not_set_requirement_hint(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r3.id])
        vllm = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        env.service.add_topic(project["id"], vllm.id)
        dlg = _detail(env, project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "能力要求：未设置" in joined

    def test_planned_project_hint(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        project, _ = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"], status="planned"
        )
        dlg = _detail(env, project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "不驱动 Planner" in joined


class TestRequirementEditor:
    def test_no_project_option(self, practice_readiness_env, qtbot):
        from app.ui.practice_dialogs import PracticeTopicRequirementDialog

        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": 2},
        )
        existing = env.readiness.get_requirement(
            project["id"], topics[0].id
        )
        dlg = PracticeTopicRequirementDialog(
            project, topics[0].id, topics[0].name, env.readiness,
            existing=existing,
        )
        qtbot.addWidget(dlg)
        joined = "\n".join(_labels(dlg))
        assert "能够写代码" in joined
        assert "完成独立实验" in joined
        # 不允许选择 PROJECT
        assert "已在真实项目中使用" not in joined
        assert dlg.selected_level() == 2

    def test_save_updates_requirement(self, practice_readiness_env, qtbot):
        from app.ui.practice_dialogs import PracticeTopicRequirementDialog

        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": 2},
        )
        dlg = PracticeTopicRequirementDialog(
            project, topics[0].id, topics[0].name, env.readiness,
        )
        qtbot.addWidget(dlg)
        dlg._buttons[4].setChecked(True)
        dlg.note_edit.setPlainText("需要能独立做实验")
        dlg._on_accept()
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        assert req["target_capability_level"] == 4
        assert req["note"] == "需要能独立做实验"


class TestRouteDetailBlockers:
    def _dialog(self, env, qtbot):
        from app.database.assessment_repository import AssessmentRepository
        from app.services.learning_route_service import LearningRouteService
        from app.services.route_plan_service import RoutePlanService
        from app.services.route_progress_service import RouteProgressService
        from app.ui.routes_page import RouteDetailDialog

        progress = RouteProgressService(
            env.repo, AssessmentRepository(env.conn), env.plan_repo,
            route_repo=env.route_repo, capability_service=env.cap,
        )
        dlg = RouteDetailDialog(
            env.r3, LearningRouteService(env.route_repo),
            RoutePlanService(env.plan_repo, env.repo,
                             AssessmentRepository(env.conn)),
            progress_service=progress,
            today_provider=lambda: DEFAULT_PLAN_DATE,
            capability_service=env.cap,
            practice_readiness_service=env.readiness,
        )
        qtbot.addWidget(dlg)
        return dlg

    def test_blocker_count_and_list(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        dlg = self._dialog(env, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "项目学习阻塞：1" in joined
        assert "vLLM 与 PagedAttention" in joined
        assert "未学习 → 能够写代码" in joined

    def test_no_blockers(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        dlg = self._dialog(env, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "项目学习阻塞：0" in joined

    def test_needs_assessment_hint(self, practice_readiness_env, qtbot):
        env = practice_readiness_env
        from tests.practice_capability_helpers import (
            complete_required_components,
        )

        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        ensure_capability(env, topics[0].id, EXPLAIN)
        complete_required_components(env, topics[0].id)
        dlg = self._dialog(env, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "进行验收" in joined


class TestPracticePageWiring:
    def test_projects_page_accepts_readiness(self, practice_readiness_env,
                                             qtbot):
        from app.ui.practice_page import PracticeProjectsPage

        env = practice_readiness_env
        page = PracticeProjectsPage(
            env.service, env.route_repo, env.skill_repo, env.plan_repo,
            capability_service=None, readiness_service=env.readiness,
        )
        qtbot.addWidget(page)
        assert page.readiness_service is env.readiness
