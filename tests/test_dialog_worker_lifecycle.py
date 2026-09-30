"""Dialog-owned AI threads must finish before dialog or application teardown."""
from __future__ import annotations

import threading

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.ui.assessment_dialog import AssessmentDialog
from app.ui import routes_page as routes_mod
from app.ui.routes_page import RouteDetailDialog
from tests.test_ai_route_preview import _draft, _FakePreview


class BlockingGrader:
    def __init__(self, entered, release, fail=False):
        self.entered, self.release, self.fail = entered, release, fail
        self.calls = 0

    def submit_answers(self, attempt_id, answers, today=None):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test grading release timed out")
        if self.fail:
            raise RuntimeError("grading unavailable")
        return {"id": attempt_id, "judge_status": "judged", "task_id": 4,
                "mastery_estimate": 0.8, "result_level": "good",
                "ai_result_json": "{}", "weak_points_json": "[]"}


def _assessment(qtbot, tmp_path, *, fail=False, parent=None):
    entered, release = threading.Event(), threading.Event()
    grader = BlockingGrader(entered, release, fail)
    attempt = {"id": 1, "task_id": 4,
               "questions": [{"type": "concept", "question": "Why?"}]}
    dialog = AssessmentDialog(None, attempt, "2026-09-15", parent=parent,
                              service_factory=lambda conn: grader,
                              db_path=tmp_path / "assessment.db")
    dialog._answer_edits[0].setPlainText("answer")
    dialog.show()
    dialog._on_submit()
    qtbot.waitUntil(entered.is_set, timeout=5000)
    return dialog, grader, release


def _release_on_drain(dialog, release, monkeypatch):
    draining = threading.Event()
    original = dialog.drain_worker
    def drain():
        draining.set()
        return original()
    monkeypatch.setattr(dialog, "drain_worker", drain)
    def allow_completion():
        draining.wait(5)
        release.set()
    helper = threading.Thread(target=allow_completion)
    helper.start()
    return helper


@pytest.mark.parametrize("fail", [False, True])
def test_assessment_close_drains_and_discards_late_result(qtbot, tmp_path, monkeypatch, fail):
    dialog, grader, release = _assessment(qtbot, tmp_path, fail=fail)
    completed = []
    dialog.assessment_completed.connect(completed.append)
    worker = dialog._worker
    assert worker is not None and worker.isRunning()
    dialog._on_submit()  # bypass disabled button: still no second worker
    assert dialog._worker is worker and grader.calls == 1
    helper = _release_on_drain(dialog, release, monkeypatch)
    dialog.close()
    helper.join(timeout=5)
    assert not helper.is_alive() and not worker.isRunning()
    assert dialog._worker is None
    QCoreApplication.processEvents()
    assert not dialog.result_container.isVisible() and not completed
    assert dialog.status_label.text() == "正在判题…"
    assert not dialog.error_label.isVisible()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)


@pytest.mark.parametrize("fail", [False, True])
def test_assessment_normal_result_and_failure(qtbot, tmp_path, fail):
    dialog, grader, release = _assessment(qtbot, tmp_path, fail=fail)
    completed = []
    dialog.assessment_completed.connect(completed.append)
    release.set()
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
    if fail:
        assert "grading unavailable" in dialog.error_label.text()
        assert not completed
    else:
        assert dialog.status_label.text() == "验收完成"
        assert dialog.result_container.isVisible()
        assert completed == [4]
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)


def test_assessment_deferred_delete_drains_running_worker(qtbot, tmp_path, monkeypatch):
    dialog, _, release = _assessment(qtbot, tmp_path)
    worker = dialog._worker
    helper = _release_on_drain(dialog, release, monkeypatch)
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    helper.join(timeout=5)
    assert not helper.is_alive()
    assert not shiboken6.isValid(worker) or not worker.isRunning()
    assert not shiboken6.isValid(dialog)


@pytest.fixture()
def route_env(conn, repo):
    routes = LearningRouteRepository(conn)
    route_service = LearningRouteService(routes)
    plan_service = RoutePlanService(StudyPlanRepository(conn), repo,
                                    AssessmentRepository(conn))
    route = route_service.create_learning_route("Lifecycle route")
    return route, route_service, plan_service


class BlockingRouteAI:
    def __init__(self, entered, release, fail=False):
        self.entered, self.release, self.fail = entered, release, fail
        self.calls = 0

    def is_configured(self):
        return True

    def build_draft(self, context, *, route_skills=None, market=None):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test route release timed out")
        if self.fail:
            raise RuntimeError("route unavailable")
        return _draft()


class _AcceptedBuilder:
    def __init__(self, *args, **kwargs):
        pass
    def exec(self):
        return QDialog.DialogCode.Accepted
    def result_payload(self):
        return {"goal": "learn"}


def _route_detail(qtbot, route_env, monkeypatch, *, fail=False, parent=None):
    route, route_service, plan_service = route_env
    entered, release = threading.Event(), threading.Event()
    service = BlockingRouteAI(entered, release, fail)
    monkeypatch.setattr(routes_mod, "AIRouteBuilderDialog", _AcceptedBuilder)
    dialog = RouteDetailDialog(route, route_service, plan_service, parent=parent,
                               ai_route_service=service)
    dialog.show()
    dialog._on_ai_generate()
    qtbot.waitUntil(entered.is_set, timeout=5000)
    return dialog, service, release


@pytest.mark.parametrize("fail", [False, True])
def test_route_close_drains_without_preview_or_warning(qtbot, route_env, monkeypatch, fail):
    previews, warnings = [], []
    monkeypatch.setattr(routes_mod, "RouteDraftPreviewDialog",
                        lambda *a, **k: previews.append(True))
    monkeypatch.setattr(routes_mod, "show_warning", lambda *a, **k: warnings.append(True))
    dialog, service, release = _route_detail(qtbot, route_env, monkeypatch, fail=fail)
    worker = dialog._ai_worker
    dialog._on_ai_generate()  # no second builder or worker
    assert dialog._ai_worker is worker and service.calls == 1
    helper = _release_on_drain(dialog, release, monkeypatch)
    dialog.close()
    helper.join(timeout=5)
    QCoreApplication.processEvents()
    assert not helper.is_alive()
    assert not shiboken6.isValid(worker) or not worker.isRunning()
    assert dialog._ai_worker is None
    assert not previews and not warnings
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)


@pytest.mark.parametrize("fail", [False, True])
def test_route_normal_success_preview_and_failure_warning(qtbot, route_env, monkeypatch, fail):
    warnings = []
    monkeypatch.setattr(routes_mod, "show_warning", lambda *a, **k: warnings.append(a))
    if not fail:
        monkeypatch.setattr(routes_mod, "RouteDraftPreviewDialog", _FakePreview)
        _FakePreview.accepted = True
        _FakePreview.payload = None
        _FakePreview._replace_empty = False
    dialog, _, release = _route_detail(qtbot, route_env, monkeypatch, fail=fail)
    release.set()
    qtbot.waitUntil(lambda: dialog._ai_worker is None, timeout=5000)
    if fail:
        assert warnings and "route unavailable" in warnings[0][1]
    else:
        assert route_env[2].get_structure(route_env[0].id) is not None
        assert not warnings
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("kind", ["assessment", "route"])
def test_accepted_hidden_dialog_drains_before_deferred_delete(qtbot, tmp_path,
                                                              route_env, monkeypatch, kind):
    if kind == "assessment":
        dialog, _, release = _assessment(qtbot, tmp_path)
        worker = dialog._worker
    else:
        dialog, _, release = _route_detail(qtbot, route_env, monkeypatch)
        worker = dialog._ai_worker
    helper = _release_on_drain(dialog, release, monkeypatch)
    dialog.accept()  # done()/hide, not necessarily QObject destruction
    helper.join(timeout=5)
    assert not helper.is_alive()
    assert not shiboken6.isValid(worker) or not worker.isRunning()
    assert dialog._closing
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)


def test_route_deferred_delete_drains_running_worker(qtbot, route_env, monkeypatch):
    dialog, _, release = _route_detail(qtbot, route_env, monkeypatch)
    worker = dialog._ai_worker
    helper = _release_on_drain(dialog, release, monkeypatch)
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    helper.join(timeout=5)
    assert not helper.is_alive()
    assert not shiboken6.isValid(worker) or not worker.isRunning()
    assert not shiboken6.isValid(dialog)


@pytest.mark.parametrize("kind", ["assessment", "route"])
def test_main_window_shutdown_drains_dialog_children(qtbot, tmp_path, route_env,
                                                     repo, task_service, date_service,
                                                     monkeypatch, kind):
    from app.ui.main_window import MainWindow
    window = MainWindow(task_service, date_service,
                        today_provider=lambda: "2026-09-15")
    qtbot.addWidget(window)
    if kind == "assessment":
        dialog, _, release = _assessment(qtbot, tmp_path, parent=window)
        worker = dialog._worker
    else:
        dialog, _, release = _route_detail(qtbot, route_env, monkeypatch,
                                            parent=window)
        worker = dialog._ai_worker
    helper = _release_on_drain(dialog, release, monkeypatch)
    window._shutdown()
    helper.join(timeout=5)
    assert not helper.is_alive() and not worker.isRunning()
    assert dialog._closing
    window.close()
