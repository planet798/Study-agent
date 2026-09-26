"""Phase F：JD 候选技能 → 学习路线映射。

覆盖需求 54 的 23~37、48~49。
"""

from __future__ import annotations

import json

import pytest

from app.database.jd_summary_repository import JdSkillCandidateRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.ai_route_service import AIRouteBuilderService
from app.services.jd_summary_service import JdSummaryService
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService

CANDIDATE = "后训练 / 对齐"


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        if self.error:
            raise self.error
        return self.response


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(StudyPlanRepository(conn), repo)
    candidate_repo = JdSkillCandidateRepository(conn)
    service = JdSummaryService(
        summary_repo=None, skill_repo=skill_repo,
        candidate_repo=candidate_repo, route_repo=route_repo,
    )
    return {
        "conn": conn, "skill_repo": skill_repo, "route_repo": route_repo,
        "route_service": route_service, "route_plan": route_plan,
        "candidate_repo": candidate_repo, "service": service,
    }


def _candidate(env):
    return env["candidate_repo"].upsert_candidate(
        canonical_name=CANDIDATE, raw_names=["后训练"], mention_count_30d=7,
        sample_count_30d=15, frequency_30d=0.467,
    )


class TestAcceptWithRoutes:
    def test_accept_one_route(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        cand = _candidate(env)
        out = env["service"].accept_candidate(
            cand["id"], tier="A", route_ids=[rl.id]
        )
        skill = out["skill"]
        assert env["skill_repo"].get_by_name(CANDIDATE) is not None
        assert env["route_repo"].list_skill_ids(rl.id) == [skill["id"]]
        assert env["candidate_repo"].get(cand["id"])["status"] == "accepted"

    def test_accept_multiple_routes(self, env):
        a = env["route_service"].create_learning_route("强化学习")
        b = env["route_service"].create_learning_route("搜广推 + LLM")
        cand = _candidate(env)
        out = env["service"].accept_candidate(
            cand["id"], route_ids=[a.id, b.id]
        )
        sid = out["skill"]["id"]
        assert env["route_repo"].list_skill_ids(a.id) == [sid]
        assert env["route_repo"].list_skill_ids(b.id) == [sid]
        assert env["skill_repo"].list_all().__len__() == 1

    def test_accept_no_route(self, env):
        cand = _candidate(env)
        out = env["service"].accept_candidate(cand["id"])
        assert out["skill"] is not None
        assert env["route_repo"].count_route_skills() == 0

    def test_ignored_creates_nothing(self, env):
        cand = _candidate(env)
        env["service"].ignore_candidate(cand["id"])
        assert env["skill_repo"].get_by_name(CANDIDATE) is None
        assert env["route_repo"].count_route_skills() == 0

    def test_atomic_rollback(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        cand = _candidate(env)
        env["conn"].execute(
            "CREATE TRIGGER boom BEFORE INSERT ON route_skills "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        env["conn"].commit()
        try:
            with pytest.raises(Exception):
                env["service"].accept_candidate(cand["id"], route_ids=[rl.id])
        finally:
            env["conn"].execute("DROP TRIGGER IF EXISTS boom")
            env["conn"].commit()
        # 全部回滚：没有 skill、candidate 仍 candidate、无 relation
        assert env["skill_repo"].get_by_name(CANDIDATE) is None
        assert env["candidate_repo"].get(cand["id"])["status"] == "candidate"
        assert env["route_repo"].count_route_skills() == 0

    def test_invalid_route_rejected(self, env):
        cand = _candidate(env)
        with pytest.raises(ValueError):
            env["service"].accept_candidate(cand["id"], route_ids=[99999])
        assert env["skill_repo"].get_by_name(CANDIDATE) is None


class TestAiSuggestion:
    def test_suggestion_only_existing_routes(self):
        routes = [{"id": 1, "name": "强化学习", "goal": "RL"},
                  {"id": 2, "name": "C++", "goal": "cpp"}]
        client = FakeClient(response=json.dumps(
            {"suggested_route_names": ["强化学习", "后训练路线"],
             "reason": "r"}, ensure_ascii=False))
        svc = AIRouteBuilderService(client)
        sugg = svc.suggest_routes(CANDIDATE, routes)
        assert sugg.suggested_route_names == ("强化学习",)
