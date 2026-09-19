"""Phase F：AI Route Builder schema / service / 持久化。

覆盖需求 54 的 1~22、46。
"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import (
    MAX_ROUTE_TOPICS,
    parse_route_draft_from_json,
)
from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.ai_route_service import AIRouteBuilderService
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService, RouteStructureError
from app.services.route_scheduler import GlobalDailyScheduler

TODAY = "2026-09-15"


def _draft_json(phases=None, **over):
    base = {
        "route_name": "强化学习",
        "plan_name": "强化学习学习计划",
        "summary": "从基础到 PPO",
        "phases": phases or [
            {"name": "RL 基础", "goal": "打基础", "order": 1, "topics": [
                {"name": "MDP", "description": "目标/核心/实践/标准",
                 "estimated_minutes": 45, "priority": 3, "order": 1},
                {"name": "Policy", "description": "目标/核心/实践/标准",
                 "estimated_minutes": 45, "priority": 3, "order": 2},
            ]},
            {"name": "Value-based", "goal": "值方法", "order": 2, "topics": [
                {"name": "Q-Learning", "description": "目标/核心/实践/标准",
                 "estimated_minutes": 45, "priority": 3, "order": 1},
                {"name": "DQN", "description": "目标/核心/实践/标准",
                 "estimated_minutes": 60, "priority": 4, "order": 2},
            ]},
        ],
    }
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append(user_prompt)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo, skill_repo=skill_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "skill_repo": skill_repo, "route_repo": route_repo,
        "route_service": route_service, "route_plan": route_plan,
    }


# ================= 1~9：schema =================

class TestRouteDraftSchema:
    def test_valid_parse(self):
        d = parse_route_draft_from_json(_draft_json())
        assert len(d.phases) == 2
        assert d.topic_count == 4
        assert d.phases[0].topics[0].name == "MDP"

    def test_invalid_json_rejected(self):
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json("not json")

    def test_empty_phase_rejected(self):
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(json.dumps({
                "route_name": "x", "plan_name": "p", "phases": []
            }))

    def test_empty_topic_rejected(self):
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(json.dumps({
                "route_name": "x", "plan_name": "p",
                "phases": [{"name": "a", "topics": []},
                           {"name": "b", "topics": []}],
            }))

    def test_duplicate_topic_rejected(self):
        phases = [
            {"name": "a", "order": 1, "topics": [
                {"name": "MDP", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 1},
                {"name": " mdp ", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 2},
            ]},
            {"name": "b", "order": 2, "topics": [
                {"name": "PPO", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 1},
                {"name": "DQN", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 2},
            ]},
        ]
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))

    def test_duplicate_phase_rejected(self):
        phases = [
            {"name": "a", "order": 1, "topics": [
                {"name": "MDP", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 1},
                {"name": "Policy", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 2}]},
            {"name": "A", "order": 2, "topics": [
                {"name": "PPO", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 1},
                {"name": "DQN", "description": "d", "estimated_minutes": 30,
                 "priority": 3, "order": 2}]},
        ]
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))

    def test_minutes_validation(self):
        phases = json.loads(_draft_json())["phases"]
        phases[0]["topics"][0]["estimated_minutes"] = 0
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))

    def test_priority_validation(self):
        phases = json.loads(_draft_json())["phases"]
        phases[0]["topics"][0]["priority"] = 9
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))

    def test_phase_count_limit(self):
        phases = [{"name": f"p{i}", "order": i, "topics": [
            {"name": f"t{i}a", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 1},
            {"name": f"t{i}b", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 2}]} for i in range(1, 10)]
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))

    def test_topic_total_limit(self):
        # 8 phases * 6 topics = 48 > 40
        phases = []
        for i in range(1, 9):
            phases.append({"name": f"p{i}", "order": i, "topics": [
                {"name": f"t{i}_{j}", "description": "d",
                 "estimated_minutes": 30, "priority": 3, "order": j}
                for j in range(1, 7)]})
        with pytest.raises(AIServiceError):
            parse_route_draft_from_json(_draft_json(phases=phases))


# ================= 10：worker 纯数据 =================

class TestWorker:
    def test_worker_has_no_db_and_emits(self):
        from app.ui.ai_worker import AIRouteBuilderWorker

        service = AIRouteBuilderService(FakeClient([_draft_json()]))
        worker = AIRouteBuilderWorker(service, {"route_name": "RL"})
        got = {}
        worker.succeeded.connect(lambda d: got.update(draft=d))
        worker.run()
        assert "draft" in got
        # 只持有纯 context / service，不持有 sqlite 连接
        assert not hasattr(worker, "_conn")
        assert "conn" not in worker._context


# ================= 11~20：持久化 =================

class TestPersistence:
    def _service(self, env, responses=None):
        return AIRouteBuilderService(
            FakeClient(responses or [_draft_json()])
        )

    def test_no_write_before_confirm(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        before = {
            "plans": len(env["plan_repo"].list_plans()),
            "phases": env["conn"].execute(
                "SELECT COUNT(*) FROM study_phases").fetchone()[0],
            "topics": env["conn"].execute(
                "SELECT COUNT(*) FROM study_topics").fetchone()[0],
        }
        env["route_plan"].get_structure(rl.id)  # 只读
        after = {
            "plans": len(env["plan_repo"].list_plans()),
            "phases": env["conn"].execute(
                "SELECT COUNT(*) FROM study_phases").fetchone()[0],
            "topics": env["conn"].execute(
                "SELECT COUNT(*) FROM study_topics").fetchone()[0],
        }
        assert before == after

    def test_confirm_creates_plan(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(rl.id, draft)
        plan = env["plan_repo"].get_plan_by_route(rl.id)
        assert plan is not None and plan.route_id == rl.id
        topics = env["plan_repo"].list_topics_by_route(rl.id)
        assert {t.name for t in topics} == {"MDP", "Policy", "Q-Learning", "DQN"}

    def test_creates_no_skill_task_kp_mastery(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(rl.id, draft)
        assert env["skill_repo"].list_all() == []
        assert env["repo"].list_by_date(TODAY) == []
        assert env["arepo"].list_knowledge_points() == []

    def test_does_not_change_priority(self, env):
        rl = env["route_service"].create_learning_route("强化学习", priority=2)
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(rl.id, draft)
        assert env["route_repo"].get(rl.id).priority == 2

    def test_transaction_rollback(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        draft = parse_route_draft_from_json(_draft_json())
        # 用 SQLite trigger 在第 2 个 topic 插入时强制失败，验证真实回滚
        env["conn"].execute(
            "CREATE TRIGGER boom BEFORE INSERT ON study_topics "
            "WHEN (SELECT COUNT(*) FROM study_topics) >= 1 "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        env["conn"].commit()
        try:
            with pytest.raises(Exception):
                env["route_plan"].create_plan_from_draft(rl.id, draft)
        finally:
            env["conn"].execute("DROP TRIGGER IF EXISTS boom")
            env["conn"].commit()
        assert env["plan_repo"].get_plan_by_route(rl.id) is None
        assert env["conn"].execute(
            "SELECT COUNT(*) FROM study_phases").fetchone()[0] == 0
        assert env["conn"].execute(
            "SELECT COUNT(*) FROM study_topics").fetchone()[0] == 0

    def test_nonempty_plan_blocked(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "已有阶段")
        env["route_plan"].add_topic(rl.id, phase.id, "已有 Topic")
        draft = parse_route_draft_from_json(_draft_json())
        mode, _ = env["route_plan"].ai_plan_mode(rl.id)
        assert mode == "blocked"
        with pytest.raises(RouteStructureError):
            env["route_plan"].create_plan_from_draft(rl.id, draft, True)

    def test_empty_plan_replace(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)  # 空 plan
        assert env["route_plan"].ai_plan_mode(rl.id)[0] == "empty_plan"
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(rl.id, draft, replace_empty=True)
        topics = env["plan_repo"].list_topics_by_route(rl.id)
        assert len(topics) == 4

    def test_route_a_draft_does_not_pollute_b(self, env):
        a = env["route_service"].create_learning_route("A")
        b = env["route_service"].create_learning_route("B")
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(a.id, draft)
        assert env["plan_repo"].list_topics_by_route(b.id) == []
        assert env["plan_repo"].get_plan_by_route(b.id) is None


# ================= 21、46：Scheduler / 一致性 =================

class TestSchedulerAndConsistency:
    def test_scheduler_can_use_ai_plan(self, env):
        rl = env["route_service"].create_learning_route("强化学习", priority=5)
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(rl.id, draft)
        sched = GlobalDailyScheduler(env["repo"], env["plan_repo"],
                                     env["route_repo"], budget=3)
        out = sched.generate(TODAY)
        tasks = [env["repo"].get(i) for i in out["created_ids"]]
        assert any(t.route_id == rl.id for t in tasks)

    def test_ai_plan_same_structure_as_manual(self, env):
        ai_route = env["route_service"].create_learning_route("AI路线")
        manual_route = env["route_service"].create_learning_route("手动路线")
        draft = parse_route_draft_from_json(_draft_json())
        env["route_plan"].create_plan_from_draft(ai_route.id, draft)
        # 手动创建同样的最小结构
        env["route_plan"].ensure_manual_plan(manual_route.id, manual_route.name)
        ph = env["route_plan"].add_phase(manual_route.id, "RL 基础")
        env["route_plan"].add_topic(manual_route.id, ph.id, "MDP")
        cols = [r[1] for r in env["conn"].execute(
            "PRAGMA table_info(study_plans)")]
        assert "route_id" in cols
        pcols = [r[1] for r in env["conn"].execute(
            "PRAGMA table_info(study_phases)")]
        assert "order_index" in pcols
        ai_plan = env["plan_repo"].get_plan_by_route(ai_route.id)
        manual_plan = env["plan_repo"].get_plan_by_route(manual_route.id)
        assert ai_plan.route_id == ai_route.id
        assert manual_plan.route_id == manual_route.id
