"""Phase E：RouteProgressService（课程覆盖 / 掌握 / 薄弱 / 复习状态）。

覆盖需求 36 的 21~31。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_progress_service import RouteProgressService

TODAY = "2026-09-15"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    progress = RouteProgressService(repo, arepo, plan_repo, route_repo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "progress": progress,
    }


def _route(env, name, topics):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    ts = [env["route_plan"].add_topic(rl.id, phase.id, t, order_index=i)
          for i, t in enumerate(topics)]
    return rl, ts


def _assess(env, route, topic, mastery, next_review=None, weak=None):
    kp = env["arepo"].get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, route_id=route.id
    )
    env["arepo"].update_knowledge_point(
        kp["id"], mastery_estimate=mastery, last_assessed_at="2026-09-10",
        next_review_date=next_review, interval_days=3,
    )
    if weak:
        attempt = env["arepo"].create_attempt(kp["id"], "{}")
        env["arepo"].update_attempt(
            attempt["id"], judge_status="judged", result_level="ok",
            mastery_estimate=mastery,
            weak_points_json='["%s"]' % weak,
        )
    return env["arepo"].get_knowledge_point(kp["id"])


def _cover(env, route, topic):
    t = env["repo"].create("cover", scheduled_date="2026-09-10",
                           topic_id=topic.id, route_id=route.id)
    env["repo"].mark_done(t.id)


# ================= 21~29：字段正确 =================

class TestRouteProgressFields:
    def test_topic_total_and_covered(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy", "Value"])
        _cover(env, rl, ts[0])
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.topic_total == 3
        assert p.topic_covered == 1
        assert p.coverage_percent == 33

    def test_no_assessment_shows_none(self, env):
        rl, ts = _route(env, "RL", ["MDP"])
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.assessment_evidence_count == 0
        assert p.mastery_rate is None
        assert p.mastery_percent is None

    def test_assessed_and_mastered_counts(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy", "Value"])
        _assess(env, rl, ts[0], 0.9)
        _assess(env, rl, ts[1], 0.6)
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.assessment_evidence_count == 2
        assert p.mastered_count == 1
        assert p.mastery_rate == pytest.approx(0.5)

    def test_weak_count(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy"])
        _assess(env, rl, ts[0], 0.3)
        _assess(env, rl, ts[1], 0.9)
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.weak_count == 1

    def test_review_counts(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy", "Value", "Q"])
        _assess(env, rl, ts[0], 0.6, next_review="2026-09-14")  # overdue+today
        _assess(env, rl, ts[1], 0.6, next_review=TODAY)         # due
        _assess(env, rl, ts[2], 0.6, next_review="2026-09-18")  # upcoming
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.due_review_count == 2
        assert p.overdue_review_count == 1
        assert p.upcoming_review_count == 1  # 9/18 在未来7天且未到期

    def test_knowledge_status_list(self, env):
        rl, ts = _route(env, "RL", ["MDP", "Policy", "Value", "Q"])
        _cover(env, rl, ts[1])
        _assess(env, rl, ts[0], 0.9)
        _assess(env, rl, ts[2], 0.3)
        p = env["progress"].get_progress(rl.id, TODAY)
        by_name = {k.name: k.status for k in p.knowledge}
        assert by_name["MDP"] == "已掌握"
        assert by_name["Policy"] == "已学习 · 未验收"
        assert by_name["Value"] == "薄弱"
        assert by_name["Q"] == "未学习"


# ================= 30：group =================

class TestGroupSummary:
    def test_group_counts(self, env):
        g = env["route_service"].create_group("求职准备X")
        a = env["route_service"].create_learning_route("RL", parent_id=g.id)
        b = env["route_service"].create_learning_route("C++", parent_id=g.id,
                                                       priority=4)
        env["route_service"].pause_planning(b.id)
        c = env["route_service"].create_learning_route("DS", parent_id=g.id)
        env["route_service"].archive_route(c.id)
        gs = env["progress"].group_summary(g.id)
        assert gs["children_total"] == 3
        assert gs["paused"] == 1
        assert gs["archived"] == 1
        assert gs["active"] == 2


# ================= 31：未分类 / cancelled =================

class TestUnclassifiedAndCancelled:
    def test_unclassified_not_in_route_progress(self, env):
        rl, ts = _route(env, "RL", ["MDP"])
        manual_kp = env["arepo"].create_knowledge_point("临时")
        p = env["progress"].get_progress(rl.id, TODAY)
        assert all(k.knowledge_point_id != manual_kp["id"] for k in p.knowledge)
        assert p.assessment_evidence_count == 0

    def test_cancelled_not_counted_as_weak(self, env):
        rl, ts = _route(env, "RL", ["MDP"])
        t = env["repo"].create("取消", scheduled_date=TODAY,
                               topic_id=ts[0].id, route_id=rl.id)
        env["repo"].cancel(t.id)
        p = env["progress"].get_progress(rl.id, TODAY)
        assert p.topic_covered == 0
        assert p.weak_count == 0
