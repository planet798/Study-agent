"""S3: Today is an execution surface; JD/Skill signals stay in Planner."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    """UI smoke windows must not leak the system/Dark palette into other suites."""
    yield
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")

from app.database.jd_summary_repository import JdSkillCandidateRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.skill_service import SkillService

DAY = "2026-09-11"
FORBIDDEN = ("职业信号", "技能概览", "JD 趋势", "JD 新技能候选", "课程缺口",
             "查看历史 JD", "添加今日 JD 技术汇总", "当前主要技能缺口")


def _seed_career_data(conn):
    SkillRepository(conn).upsert_by_name("Embedding")
    JdRepository(conn).create(company="Sample", raw_text="Embedding", uploaded_at=DAY)
    from app.database.jd_summary_repository import JdDailySummaryRepository
    from app.services.jd_summary_service import JdSummaryService

    JdSummaryService(JdDailySummaryRepository(conn), SkillRepository(conn)).save_summary(
        DAY, "Embedding 9", 10
    )
    JdSkillCandidateRepository(conn).upsert_candidate("NewTech", ["NewTech"], 8, 10, .8)


def _today_labels(window):
    return [label.text() for label in window.today_page.findChildren(QLabel)]


def test_career_data_does_not_render_in_today(conn, task_service, date_service, qtbot):
    from app.ui.main_window import MainWindow

    _seed_career_data(conn)
    learning = task_service.create_task("学习 Tensor", scheduled_date=DAY, estimated_minutes=35)
    window = MainWindow(task_service, date_service, today_provider=lambda: DAY,
                        skill_service=SkillService(SkillRepository(conn)))
    qtbot.addWidget(window)
    window.refresh()
    text = "\n".join(_today_labels(window))
    assert "今日学习" in text and "学习 Tensor" in text
    assert not any(label in text for label in FORBIDDEN)
    assert window.today_page.pending_card.value() == "1"
    assert window.today_page.minutes_card.value() == "35 分钟"
    assert [w.task().id for w in window._task_widgets] == [learning.id]
    assert not hasattr(window, "_career_panel_added")
    assert not hasattr(window, "jd_service")
    assert not hasattr(window, "jd_summary_service")


def test_career_only_database_still_shows_learning_empty_state(
    conn, task_service, date_service, qtbot,
):
    from app.ui.main_window import MainWindow

    _seed_career_data(conn)
    window = MainWindow(task_service, date_service, today_provider=lambda: DAY,
                        skill_service=SkillService(SkillRepository(conn)))
    qtbot.addWidget(window)
    window.refresh()
    assert window.empty_hint.isHidden() is False
    assert window.scroll.isHidden() is True
    assert window.today_page.pending_card.value() == "0"
    assert window.today_page.minutes_card.value() == "0 分钟"
    assert not any(label in "\n".join(_today_labels(window)) for label in FORBIDDEN)


def test_jd_summary_market_signal_still_reaches_planner(conn):
    from tests.test_planner_market_trend import _env, _dps, NEXT, TODAY

    env = _env(conn)
    env["js"].save_summary(NEXT, "Embedding 9", 10)
    market = env["ss"].market_signal.compute(NEXT)
    assert market["source"] == "daily_summary"
    assert market["skills"]["Embedding"]["freq30"] == .9
    ctx = _dps(env).build_context(TODAY)
    assert ctx.market_source == "daily_summary"
    assert any(t.skill == "Embedding" and t.market_30d == .9 for t in ctx.market_trends)
    assert any(s.skill == "Embedding" and s.market_30d == .9 for s in ctx.skill_priorities)


def test_routes_keep_skill_ui(conn, task_service, date_service, qtbot):
    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.learning_route_service import LearningRouteService
    from app.services.route_plan_service import RoutePlanService
    from app.ui.main_window import MainWindow
    from app.ui.routes_page import RouteDetailDialog

    route_repo = LearningRouteRepository(conn)
    route = route_repo.get_default_learning_route()
    skill_repo = SkillRepository(conn)
    skill = skill_repo.upsert_by_name("Embedding")
    route_repo.assign_skill(route.id, skill["id"])
    StudyPlanRepository(conn).create_plan(
        "Route plan", "2026-01-01", "2026-12-31", route_id=route.id
    )
    service = LearningRouteService(route_repo)
    skill_service = SkillService(skill_repo)
    window = MainWindow(task_service, date_service, today_provider=lambda: DAY,
                        skill_service=skill_service, route_service=service,
                        route_plan_service=RoutePlanService(
                            StudyPlanRepository(conn), task_service.repo,
                            AssessmentRepository(conn)))
    qtbot.addWidget(window)
    window._switch_to_routes()
    assert window.stack.currentIndex() == window.routes_page_index
    detail = RouteDetailDialog(route, service, window.route_plan_service,
                               skill_service=skill_service, today_provider=lambda: DAY)
    qtbot.addWidget(detail)
    texts = "\n".join(l.text() for l in detail.findChildren(QLabel))
    assert "关联技能" in texts and "Embedding" in texts
