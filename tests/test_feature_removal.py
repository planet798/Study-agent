"""功能移除：额外学习 / 课外探索。

覆盖需求 13 的 1~12、16~17。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.services.date_service import DateService
from app.services.notes_service import NotesService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow

TODAY = "2026-09-15"
YESTERDAY = "2026-09-14"


def _window(qtbot, repo, task_service, date_service, **kw):
    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        today_provider=lambda: TODAY,
        **kw,
    )
    qtbot.addWidget(w)
    return w


class TestModulesRemoved:
    def test_extra_service_module_removed(self):
        with pytest.raises(ImportError):
            import app.services.extra_task_service  # noqa: F401

    def test_exploration_service_module_removed(self):
        with pytest.raises(ImportError):
            import app.services.exploration_service  # noqa: F401

    def test_exploration_resources_removed(self):
        assert not (Path("docs") / "exploration_resources.json").exists()

    def test_main_window_has_no_service_params(self):
        from app.ui.main_window import MainWindow as MW

        params = inspect.signature(MW.__init__).parameters
        assert "extra_service" not in params
        assert "exploration_service" not in params


class TestUiSectionsRemoved:
    def test_today_page_has_no_sections(self, qtbot, repo, task_service,
                                        date_service):
        task_service.create_task("任务", scheduled_date=TODAY)
        w = _window(qtbot, repo, task_service, date_service)
        from PySide6.QtWidgets import QLabel

        texts = [l.text() for l in w.list_container.findChildren(QLabel)]
        assert not any("额外学习" in t for t in texts)
        assert not any("课外探索" in t for t in texts)
        assert "今日学习" in texts

    def test_no_extra_buttons(self, qtbot, repo, task_service, date_service):
        task_service.create_task("任务", scheduled_date=TODAY)
        w = _window(qtbot, repo, task_service, date_service)
        from PySide6.QtWidgets import QPushButton

        texts = [b.text() for b in w.list_container.findChildren(QPushButton)]
        assert not any("额外任务" in t for t in texts)
        assert not any("打开链接" in t for t in texts)

    def test_notes_have_no_sections(self, conn):
        repo = TaskRepository(conn)
        ns = NotesService(repo=repo, study_plan_service=None,
                          outcome_service=None, assessment_repo=None,
                          jd_service=None)
        content = ns.build_daily_note(TODAY)
        assert "今日额外学习" not in content
        assert "课外探索" not in content

    def test_planner_prompt_no_extra_service(self):
        from app.ai.prompts import build_planner_system_prompt

        sysp = build_planner_system_prompt(180)
        assert "ExtraTaskService" not in sysp
        assert "额外学习" not in sysp


class TestNoNewExtraGeneration:
    def test_date_service_does_not_generate_extra(self, conn, plan_repo):
        repo = TaskRepository(conn)
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        ds = DateService(repo, study_plan_service=sps)
        ds.process_date_transition(TODAY)
        extras = [t for t in repo.list_by_date(TODAY)
                  if t.task_type == "extra" or t.source == "extra"]
        assert extras == []

    def test_scheduler_does_not_generate_extra(self, conn):
        repo = TaskRepository(conn)
        arepo = AssessmentRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        from app.services.route_scheduler import GlobalDailyScheduler
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                               learning_route_repo=route_repo)
        sps.ensure_default_plan()
        sched = GlobalDailyScheduler(repo, plan_repo, route_repo,
                                     assessment_repo=arepo)
        sched.generate(TODAY)
        assert all(t.task_type == "new" for t in repo.list_by_date(TODAY))


class TestLegacyExtraHandling:
    def test_legacy_active_extra_cancelled_not_deleted(self, conn, plan_repo):
        repo = TaskRepository(conn)
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        t = repo.create("legacy extra", scheduled_date=TODAY,
                        source="extra", task_type="extra")
        ds = DateService(repo, study_plan_service=sps)
        result = ds.process_date_transition(TODAY)
        assert result["legacy_extra_cancelled"] >= 1
        again = repo.get(t.id)
        assert again is not None  # 不物理删除
        assert again.status == "cancelled"

    def test_cancelled_extra_idempotent(self, conn, plan_repo):
        repo = TaskRepository(conn)
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        repo.create("legacy extra", scheduled_date=TODAY,
                    source="extra", task_type="extra")
        ds = DateService(repo, study_plan_service=sps)
        first = ds.process_date_transition(TODAY)["legacy_extra_cancelled"]
        second = ds._cancel_legacy_extra_tasks()
        assert first >= 1 and second == 0

    def test_legacy_extra_does_not_block_preflight(self, conn, repo, task_service):
        repo.create("legacy active extra", scheduled_date=YESTERDAY,
                    source="extra", task_type="extra")
        import app.main as main_mod

        assert main_mod._run_past_task_preflight(repo, task_service, TODAY) is True

    def test_done_extra_preserved(self, conn, plan_repo):
        repo = TaskRepository(conn)
        from app.services.study_plan_service import StudyPlanService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        t = repo.create("done extra", scheduled_date=YESTERDAY,
                        source="extra", task_type="extra")
        repo.mark_done(t.id)
        ds = DateService(repo, study_plan_service=sps)
        ds.process_date_transition(TODAY)
        assert repo.get(t.id).status == "done"
