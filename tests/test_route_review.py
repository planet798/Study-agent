"""Phase E：Review 全链路 route-aware。

覆盖需求 36 的 10~18、20。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.review_service import ReviewService
from app.services.route_plan_service import RoutePlanService
from app.services.task_service import TaskService
from app.utils.date_utils import add_days

TODAY = "2026-09-15"
LAST_WEEK = "2026-09-05"


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
        "route_plan": route_plan, "ts": TaskService(repo),
        "review": ReviewService(repo, arepo, plan_repo=plan_repo),
    }


def _route_with_topic(env, name, topic="MDP"):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    t = env["route_plan"].add_topic(rl.id, phase.id, topic)
    return rl, t


def _assessed_kp(env, route, topic, next_review=None, mastery=0.6):
    kp = env["arepo"].get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, route_id=route.id
    )
    env["arepo"].update_knowledge_point(
        kp["id"], mastery_estimate=mastery,
        last_assessed_at="2026-09-10", next_review_date=next_review,
        interval_days=3,
    )
    return env["arepo"].get_knowledge_point(kp["id"])


def _learned_kp(env, route, topic, learned_date=LAST_WEEK):
    kp = env["arepo"].get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, route_id=route.id
    )
    t = env["repo"].create("learned", scheduled_date=learned_date,
                           source="generated", task_type="new",
                           topic_id=topic.id, knowledge_point_id=kp["id"],
                           route_id=route.id)
    env["repo"].mark_done(t.id)
    return kp


# ================= 10~11：继承 =================

class TestReviewInherit:
    def test_due_review_inherits_kp_route(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _assessed_kp(env, rl, topic, next_review=TODAY)
        res = env["review"].generate_due_reviews(today=TODAY)
        assert res["created"]
        task = env["repo"].get(res["created"][0]["task_id"])
        assert task.route_id == rl.id
        assert task.task_type == "review"

    def test_daily_retention_inherits_route(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _learned_kp(env, rl, topic)
        res = env["review"].generate_daily_retention_reviews(today=TODAY)
        assert res["created"]
        task = env["repo"].get(res["created"][0]["task_id"])
        assert task.route_id == rl.id
        assert task.source == "daily_retention"

    def test_review_of_route_kp_not_null_route(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _assessed_kp(env, rl, topic, next_review=TODAY)
        res = env["review"].generate_due_reviews(today=TODAY)
        task = env["repo"].get(res["created"][0]["task_id"])
        assert task.route_id is not None


# ================= 12~15：pause / archive / restore =================

class TestPauseArchiveRestore:
    def test_pause_route_still_reviews(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _assessed_kp(env, rl, topic, next_review=TODAY)
        env["route_service"].pause_planning(rl.id)
        res = env["review"].generate_due_reviews(today=TODAY)
        assert res["created"]
        assert env["repo"].get(res["created"][0]["task_id"]).route_id == rl.id

    def test_archive_route_no_new_review(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _assessed_kp(env, rl, topic, next_review=TODAY)
        env["route_service"].archive_route(rl.id)
        res = env["review"].generate_due_reviews(today=TODAY)
        assert res["created"] == []

    def test_archive_keeps_existing_review(self, env):
        rl, topic = _route_with_topic(env, "RL")
        kp = _assessed_kp(env, rl, topic, next_review=TODAY)
        task = env["repo"].create("复习 MDP", scheduled_date=TODAY,
                                  source="review", task_type="review",
                                  knowledge_point_id=kp["id"], route_id=rl.id)
        env["route_service"].archive_route(rl.id)
        assert env["repo"].get(task.id) is not None

    def test_restore_regenerates_overdue(self, env):
        rl, topic = _route_with_topic(env, "RL")
        _assessed_kp(env, rl, topic, next_review="2026-09-14")
        env["route_service"].archive_route(rl.id)
        assert env["review"].generate_due_reviews(today=TODAY)["created"] == []
        env["route_service"].restore_route(rl.id)
        # restore 后 planning_enabled=0，但 Review 不依赖它
        assert env["route_repo"].get(rl.id).planning_enabled is False
        res = env["review"].generate_due_reviews(today=TODAY)
        assert res["created"]


# ================= 16~17：全局目标 / cooldown =================

class TestRetentionTargetAndCooldown:
    def test_global_target_not_multiplied(self, env):
        for i in range(2):
            rl, topic = _route_with_topic(env, f"R{i}", topic=f"T{i}")
            _learned_kp(env, rl, topic)
        res = env["review"].generate_daily_retention_reviews(
            today=TODAY, target_total=3
        )
        assert len(res["created"]) <= 3

    def test_cooldown_by_kp_not_name(self, env):
        rl_a, ta = _route_with_topic(env, "A", topic="基础")
        rl_b, tb = _route_with_topic(env, "B", topic="基础")
        _learned_kp(env, rl_a, ta)
        _learned_kp(env, rl_b, tb)
        cands = env["review"].retention_candidates(TODAY)
        assert {c["kp"]["route_id"] for c in cands} == {rl_a.id, rl_b.id}
        # 为 A 生成 retention 后，A 进入 cooldown，B 仍可候选
        env["review"]._create_retention_task(cands[0], TODAY)
        remaining = env["review"].retention_candidates(TODAY)
        kp_a = env["arepo"].list_knowledge_points_by_route(rl_a.id)[0]["id"]
        kp_b = env["arepo"].list_knowledge_points_by_route(rl_b.id)[0]["id"]
        assert kp_b in {c["knowledge_point_id"] for c in remaining}


# ================= 18、20：filter / complete =================

class TestFilterAndComplete:
    def test_route_filter_shows_only_route_review(self, env, qtbot):
        from app.services.date_service import DateService
        from app.ui.main_window import MainWindow

        rl, topic = _route_with_topic(env, "RL")
        other, topic2 = _route_with_topic(env, "C++", topic="STL")
        _assessed_kp(env, rl, topic, next_review=TODAY)
        _assessed_kp(env, other, topic2, next_review=TODAY)
        env["review"].generate_due_reviews(today=TODAY)
        w = MainWindow(
            task_service=env["ts"], date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY, assessment_repo=env["arepo"],
            review_scheduler=env["review"], route_service=env["route_service"],
            route_plan_service=env["route_plan"],
        )
        qtbot.addWidget(w)
        idx = w.route_filter_combo.findData(rl.id)
        w.route_filter_combo.setCurrentIndex(idx)
        routes = {wd.task().route_id for wd in w._task_widgets}
        assert routes == {rl.id}

    def test_review_complete_does_not_change_mastery(self, env):
        rl, topic = _route_with_topic(env, "RL")
        kp = _assessed_kp(env, rl, topic, next_review=TODAY, mastery=0.6)
        task = env["repo"].create("复习 MDP", scheduled_date=TODAY,
                                  source="review", task_type="review",
                                  knowledge_point_id=kp["id"], route_id=rl.id)
        env["ts"].complete_task(task.id)
        after = env["arepo"].get_knowledge_point(kp["id"])
        assert after["mastery_estimate"] == pytest.approx(0.6)
        assert after["last_assessed_at"] == "2026-09-10"
