"""S5: new manual tasks are learning, while historical manual rows remain valid."""
from __future__ import annotations

import pytest

from PySide6.QtWidgets import QLabel

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.date_service import DateService
from app.services.learning_route_service import LearningRouteService
from app.services.manual_task_service import ManualTaskService
from app.services.past_task_service import PastTaskConfirmationService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow
from app.ui.manual_task_dialog import AddLearningTaskDialog, KIND_ACTIVITY, KIND_KNOWLEDGE
from app.ui.task_widget import TaskWidget

DAY = "2026-09-15"


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    """Keep UI smoke tests from leaking a Dark system palette to other files."""
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


def test_dialog_product_terms_and_default(qtbot):
    dlg = AddLearningTaskDialog(default_date=DAY)
    qtbot.addWidget(dlg)
    labels = " ".join(w.text() for w in dlg.findChildren(QLabel))
    assert dlg.knowledge_radio.isChecked()
    assert dlg.knowledge_radio.text() == "知识学习"
    assert dlg.activity_radio.text() == "学习活动"
    assert "一次性的学习行为" in labels
    assert "完成后可进行验收" in labels
    assert not any(word in labels for word in ("Todo", "To-do", "普通学习任务"))
    dlg.activity_radio.setChecked(True)
    assert dlg.result_payload()["kind"] == KIND_ACTIVITY
    dlg.knowledge_radio.setChecked(True)
    assert dlg.result_payload()["kind"] == KIND_KNOWLEDGE


def test_activity_and_knowledge_keep_distinct_assessment_paths(conn, qtbot):
    repo, arepo = TaskRepository(conn), AssessmentRepository(conn)
    service = ManualTaskService(repo, assessment_repo=arepo)
    activity = service.create_learning_activity("刷 LeetCode", scheduled_date=DAY)
    knowledge = service.create_knowledge_task("Self-Attention", scheduled_date=DAY)
    assert (activity.source, activity.task_type, activity.topic_id,
            activity.knowledge_point_id) == ("manual", "manual", None, None)
    assert (knowledge.source, knowledge.task_type) == ("manual", "new")
    assert knowledge.knowledge_point_id is not None
    aw, kw = TaskWidget(activity), TaskWidget(knowledge)
    qtbot.addWidget(aw)
    qtbot.addWidget(kw)
    assert not hasattr(aw, "assessment_btn")
    assert hasattr(kw, "assessment_btn")
    assert aw.source_tag_label.text() == "手动学习"
    assert kw.source_tag_label.text() == "知识学习"
    TaskService(repo).complete_task(activity.id)
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM capability_evidence").fetchone()[0] == 0
    assert arepo.get_knowledge_point(knowledge.knowledge_point_id)["mastery_estimate"] == 0
    assert repo.get(activity.id).knowledge_point_id is None


def test_new_activity_and_temporary_knowledge_require_route_in_ui(conn, qtbot):
    route_repo = LearningRouteRepository(conn)
    route = route_repo.get_default_learning_route()
    routes = [{"id": route.id, "name": route.name}]
    for knowledge in (False, True):
        dlg = AddLearningTaskDialog(routes=routes, default_date=DAY)
        qtbot.addWidget(dlg)
        if not knowledge:
            dlg.activity_radio.setChecked(True)
        dlg.title_edit.setText("Learning")
        dlg._on_confirm()
        assert dlg.error_label.text() == "请选择所属学习路线。"
        dlg.route_combo.setCurrentIndex(dlg.route_combo.findData(route.id))
        dlg._on_confirm()
        assert dlg.result_payload()["route_id"] == route.id
    # 没有 active routes 时安全降级为未分类。
    fallback = AddLearningTaskDialog(routes=[], default_date=DAY)
    qtbot.addWidget(fallback)
    fallback.activity_radio.setChecked(True)
    fallback.title_edit.setText("Learning")
    fallback._on_confirm()
    assert fallback.result_payload()["route_id"] is None


def test_topic_route_is_authoritative_and_temp_kp_is_route_scoped(conn):
    repo, arepo = TaskRepository(conn), AssessmentRepository(conn)
    plans = StudyPlanRepository(conn)
    route = LearningRouteRepository(conn).get_default_learning_route()
    plan = plans.create_plan("manual", "2026-09-01", "2026-09-30", route_id=route.id)
    phase = plans.create_phase(plan.id, "study", "2026-09-01", "2026-09-30")
    topic = plans.create_topic(phase.id, "Attention")
    service = ManualTaskService(repo, assessment_repo=arepo,
                                study_plan_service=StudyPlanService(repo, plans, assessment_repo=arepo))
    linked = service.create_knowledge_task("Attention", topic_id=topic.id, route_id=999,
                                           scheduled_date=DAY)
    assert linked.route_id == route.id
    assert arepo.get_knowledge_point(linked.knowledge_point_id)["topic_id"] == topic.id
    temp = service.create_knowledge_task("BFS", route_id=route.id, scheduled_date=DAY)
    kp = arepo.get_knowledge_point(temp.knowledge_point_id)
    assert kp["topic_id"] is None and kp["route_id"] == route.id


def test_historical_unclassified_manual_row_keeps_today_and_past_transitions(
    conn, qtbot,
):
    repo = TaskRepository(conn)
    historical = repo.create("历史学习活动", scheduled_date=DAY,
                             source="manual", task_type="manual", route_id=None)
    service = TaskService(repo)
    window = MainWindow(service, DateService(repo), today_provider=lambda: DAY,
                        route_service=LearningRouteService(LearningRouteRepository(conn)))
    qtbot.addWidget(window)
    assert historical.id in [w.task().id for w in window._task_widgets]
    assert repo.get(historical.id).route_id is None
    service.mark_not_done(historical.id, "稍后继续")
    postponed = service.postpone_task(historical.id)
    assert postponed.scheduled_date == "2026-09-16" and postponed.route_id is None
    service.complete_task(postponed.id)
    assert repo.get(historical.id).status == "done"
    past = repo.create("跨日旧学习活动", scheduled_date="2026-09-14",
                       source="manual", task_type="manual", route_id=None)
    assert past.id in [t.id for t in PastTaskConfirmationService(repo, service).find_unresolved(DAY)]
    assert repo.get(past.id).route_id is None


def test_legacy_alias_preserves_unclassified_manual_rows(conn):
    repo = TaskRepository(conn)
    historical = ManualTaskService(repo).create_todo("legacy study", scheduled_date=DAY)
    assert historical.task_type == "manual"
    assert historical.route_id is None
    assert historical.knowledge_point_id is None


def test_mainwindow_rejects_unrouted_new_activity_even_with_dialog_stub(
    conn, task_service, date_service, qtbot, monkeypatch,
):
    from app.ui import main_window as mw

    route_repo = LearningRouteRepository(conn)
    assert route_repo.get_default_learning_route() is not None
    window = MainWindow(task_service, date_service, today_provider=lambda: DAY,
                        route_service=LearningRouteService(route_repo))
    qtbot.addWidget(window)

    class DialogStub:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def result_payload(self):
            return {"kind": KIND_ACTIVITY, "title": "study", "route_id": None}

    warnings = []
    monkeypatch.setattr(mw, "AddLearningTaskDialog", DialogStub)
    monkeypatch.setattr("app.ui.dialogs.show_warning", lambda _parent, msg: warnings.append(msg))
    window._on_add_learning_task()
    assert warnings == ["请选择所属学习路线。"]
    assert task_service.repo.list_by_date(DAY) == []


def test_planner_gate_still_excludes_out_of_phase_topics(conn):
    from tests.test_planner_gate_coverage import _env, TODAY as PLAN_DAY

    env = _env(conn, phase_a_done=False)
    route = LearningRouteRepository(conn).get_default_learning_route()
    manual = ManualTaskService(env["repo"]).create_learning_activity(
        "刷题", scheduled_date=PLAN_DAY, route_id=route.id
    )
    assert env["ss"].is_blocked(env["sr"].get_by_name("Post"))
    generated = env["sps"].generate_daily_tasks(PLAN_DAY)
    assert env["t_b"].id not in generated["selected"]
    assert env["repo"].get(manual.id).task_type == "manual"
