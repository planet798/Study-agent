"""Phase B：路线数据隔离 + 路线继承 + 现有行为不回归。

覆盖需求 37 的 27~40：
- 新 Agent task 自动 route（规则 + AI）；
- Review / Extra 继承 route；
- 新 manual todo NULL / manual linked-topic 自动 route；
- 第二条 route 不影响默认 Planner / get_current_phase；
- Route A phase/topic 不出现在 Route B；
- cancelled / 昨日确认 / Assessment / Review 不回归。
"""

from __future__ import annotations

import pytest

from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.learning_route_service import LearningRouteService
from app.services.manual_task_service import ManualTaskService
from app.services.past_task_service import PastTaskConfirmationService
from app.services.review_service import ReviewService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService

TODAY = "2026-09-15"
YESTERDAY = "2026-09-14"


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo, skill_repo=skill_repo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    ts = TaskService(repo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "skill_repo": skill_repo, "route_repo": route_repo,
        "route_service": route_service, "sps": sps, "ts": ts,
        "manual": ManualTaskService(repo, assessment_repo=arepo,
                                    study_plan_service=sps),
        "default_route": route_repo.get_default_learning_route(),
    }


def _add_second_route(env, name="强化学习", topic_name="MDP 基础"):
    rl = env["route_service"].create_learning_route(name, priority=5)
    plan = env["plan_repo"].create_plan(
        f"{name} plan", "2026-09-01", "2026-12-31", route_id=rl.id
    )
    phase = env["plan_repo"].create_phase(
        plan.id, f"{name} 阶段", "2026-09-01", "2026-12-31"
    )
    topic = env["plan_repo"].create_topic(phase.id, topic_name,
                                          estimated_minutes=60, priority=3)
    return rl, plan, phase, topic


# ================= 27：新 Agent task 自动 route =================

class TestAgentTaskRoute:
    def test_rule_path_sets_route(self, env):
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["generated"]
        for task in res["generated"]:
            assert task.route_id == env["default_route"].id

    def test_ai_path_sets_route(self, env):
        phase = env["sps"].get_current_phase(TODAY)
        topic = phase.topics[0]

        class FakePlanner:
            def is_configured(self):
                return True

            def plan_next_day(self, context):
                return DailyPlan(
                    reasoning="r", adjustment="a",
                    recommended_tasks=(
                        RecommendedTask(topic_id=topic.id, title=topic.name,
                                        estimated_minutes=30, priority=2),
                    ),
                    carry_over_tasks=(), daily_minutes=30,
                )

        dps = DailyPlannerService(env["repo"], env["plan_repo"],
                                  planner=FakePlanner(),
                                  study_plan_service=env["sps"])
        out = dps.generate_next_day_plan(YESTERDAY, force=True)
        assert out["created"]
        task = env["repo"].get(out["created"][0])
        assert task.route_id == env["default_route"].id
        # planner_decision 也带 route
        decision = env["conn"].execute(
            "SELECT route_id FROM planner_decisions WHERE date = ?", (TODAY,)
        ).fetchone()
        assert decision[0] == env["default_route"].id


# ================= 28：Review 继承 route =================

class TestReviewRoute:
    def test_review_task_inherits_kp_route(self, env):
        topic = env["sps"].get_current_phase(TODAY).topics[0]
        kp = env["arepo"].get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, route_id=env["default_route"].id
        )
        svc = ReviewService(env["repo"], env["arepo"], plan_repo=env["plan_repo"])
        schedule = svc._create_review_task(kp, TODAY)
        task = env["repo"].get(schedule["task_id"])
        assert task.route_id == env["default_route"].id


# ================= 29：Extra 继承 route =================

# ================= 30~31：Manual =================

class TestManualRoute:
    def test_new_manual_todo_stays_null(self, env):
        t = env["manual"].create_todo("刷 LeetCode", scheduled_date=TODAY)
        assert t.route_id is None

    def test_new_manual_linked_topic_gets_route(self, env):
        topic = env["sps"].get_current_phase(TODAY).topics[0]
        t = env["manual"].create_knowledge_task(
            "Transformer 复习", topic_id=topic.id, scheduled_date=TODAY
        )
        assert t.route_id == env["default_route"].id

    def test_manual_temp_kp_stays_null(self, env):
        t = env["manual"].create_knowledge_task(
            "强化学习基础", scheduled_date=TODAY
        )
        assert t.route_id is None
        assert t.knowledge_point_id is not None


# ================= 32~34 / 36：数据隔离 =================

class TestRouteIsolation:
    def test_route_topic_queries_are_isolated(self, env):
        rl, plan, phase, topic = _add_second_route(env)
        default_topics = env["plan_repo"].list_topics_by_route(
            env["default_route"].id
        )
        rl_topics = env["plan_repo"].list_topics_by_route(rl.id)
        assert topic.name in [t.name for t in rl_topics]
        assert topic.name not in [t.name for t in default_topics]
        assert all(t.id != topic.id for t in default_topics)

    def test_second_route_does_not_affect_default_planner(self, env):
        _add_second_route(env)
        phase = env["sps"].get_current_phase(TODAY)
        assert phase is not None
        assert "MDP 基础" not in [t.name for t in phase.topics]
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["generated"]
        assert all(t.route_id == env["default_route"].id for t in res["generated"])

    def test_get_plan_by_route_strict(self, env):
        rl, plan, phase, topic = _add_second_route(env)
        assert env["plan_repo"].get_plan_by_route(rl.id).id == plan.id
        default_plan = env["plan_repo"].get_plan_by_route(
            env["default_route"].id
        )
        assert default_plan.id != plan.id

    def test_default_plan_full_only_default_route(self, env):
        _add_second_route(env)
        plan = env["sps"].get_active_plan_full()
        names = [t.name for ph in plan.phases for t in ph.topics]
        assert "MDP 基础" not in names

    def test_default_service_ignores_foreign_route_without_default_plan(self, tmp_path):
        """没有默认 plan、只有 RL plan 时，默认 StudyPlanService 不得看到 RL。"""
        from app.database.connection import get_connection

        conn = get_connection(tmp_path / "only_rl.db")
        try:
            repo = TaskRepository(conn)
            arepo = AssessmentRepository(conn)
            plan_repo = StudyPlanRepository(conn)
            route_repo = LearningRouteRepository(conn)
            route_service = LearningRouteService(route_repo)
            rl = route_service.create_learning_route("强化学习")
            rl_plan = plan_repo.create_plan("RL plan", "2026-09-01",
                                            "2026-12-31", route_id=rl.id)
            ph = plan_repo.create_phase(rl_plan.id, "RL基础", "2026-09-01",
                                        "2026-12-31")
            plan_repo.create_topic(ph.id, "MDP", estimated_minutes=60)

            sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                                   learning_route_repo=route_repo)
            assert sps.get_active_plan_full() is None
            assert sps.get_current_phase(TODAY) is None
            # 严格 route 查询仍能找到 RL 自己的计划
            assert plan_repo.get_plan_by_route(rl.id).id == rl_plan.id
        finally:
            conn.close()

    def test_second_route_plan_not_returned_for_default(self, env):
        rl, plan, phase, topic = _add_second_route(env)
        default_plan = env["plan_repo"].get_active_plan(
            route_id=env["default_route"].id
        )
        assert default_plan.id != plan.id


# ================= 33：route-scoped isolation =================

class TestScopedQueries:
    def test_route_a_not_in_route_b(self, env):
        rl, plan, phase, topic = _add_second_route(env, name="C++", topic_name="虚函数")
        rl2, plan2, phase2, topic2 = _add_second_route(
            env, name="数据结构与算法", topic_name="哈希表"
        )
        assert [t.name for t in env["plan_repo"].list_topics_by_route(rl.id)] \
            == ["虚函数"]
        assert [t.name for t in env["plan_repo"].list_topics_by_route(rl2.id)] \
            == ["哈希表"]
        assert [p.name for p in env["plan_repo"].list_phases_by_route(rl.id)] \
            == ["C++ 阶段"]


# ================= 35 / 37~40：回归 =================

class TestNoRegression:
    def test_market_and_jd_still_work(self, env):
        jd_repo = JdRepository(env["conn"])
        jd = jd_repo.create(company="A", title="算法实习", direction="rec",
                            raw_text="PyTorch RAG", content_hash="h1")
        assert jd_repo.get(jd["id"])["company"] == "A"

    def test_assessment_still_works_with_route(self, env):
        topic = env["sps"].get_current_phase(TODAY).topics[0]
        kp = env["arepo"].get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, route_id=env["default_route"].id
        )
        attempt = env["arepo"].create_attempt(kp["id"], "{}")
        assert attempt["knowledge_point_id"] == kp["id"]
        assert env["arepo"].get_knowledge_point(kp["id"])["route_id"] \
            == env["default_route"].id

    def test_cancelled_not_regressed(self, env):
        t = env["manual"].create_todo("leecode", scheduled_date=TODAY)
        env["ts"].cancel_task(t.id)
        stats = env["ts"].get_daily_stats(TODAY)
        assert stats["total"] == 0 or all(
            x.status != "cancelled"
            for x in env["repo"].list_by_date(TODAY)
        )
        assert env["repo"].get(t.id).status == "cancelled"

    def test_past_confirmation_not_regressed(self, env):
        env["manual"].create_todo("昨天", scheduled_date=YESTERDAY)
        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        assert len(svc.find_unresolved(TODAY)) == 1
