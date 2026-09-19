"""Phase F：route-specific curriculum gap。

覆盖需求 54 的 38~41。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.skill_service import SkillService


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    skill_repo = SkillRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo, skill_repo=skill_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    skill_service = SkillService(
        skill_repo, plan_repo=plan_repo, assessment_repo=arepo,
        route_repo=route_repo,
    )
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "skill_repo": skill_repo, "route_repo": route_repo,
        "route_service": route_service, "route_plan": route_plan,
        "skill_service": skill_service,
    }


def _market(skill_service, *names, freq=0.5):
    skill_service._market = {
        "source": "daily_summary",
        "sample_count_30d": 15,
        "skills": {
            n: {"freq30": freq, "mention_30d": 7} for n in names
        },
    }


def _route_with_topic(env, name, topic, linked_skill=None):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    t = env["route_plan"].add_topic(rl.id, phase.id, topic)
    if linked_skill:
        env["skill_repo"].update(linked_skill["id"], linked_topics=[t.id])
    return rl, t


class TestRouteSpecificGap:
    def test_bound_skill_without_route_topic_is_gap(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        env["route_plan"].add_phase(rl.id, "RL 基础")
        s = env["skill_repo"].create("后训练 / 对齐", tier="A")
        env["route_repo"].assign_skill(rl.id, s["id"])
        _market(env["skill_service"], "后训练 / 对齐")
        gaps = env["skill_service"].curriculum_gap_skills(route_id=rl.id)
        assert [g["skill"] for g in gaps] == ["后训练 / 对齐"]
        assert gaps[0]["frequency_30d"] == pytest.approx(0.5)

    def test_skill_with_topic_in_other_route_still_gap(self, env):
        other, other_topic = _route_with_topic(env, "搜广推", "PyTorch")
        s = env["skill_repo"].get_by_name("PyTorch") or \
            env["skill_repo"].create("PyTorch", tier="S")
        env["skill_repo"].update(s["id"], linked_topics=[other_topic.id])
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        env["route_plan"].add_phase(rl.id, "RL 基础")
        env["route_repo"].assign_skill(rl.id, s["id"])
        _market(env["skill_service"], "PyTorch", freq=0.66)
        gaps = env["skill_service"].curriculum_gap_skills(route_id=rl.id)
        assert "PyTorch" in [g["skill"] for g in gaps]

    def test_gap_disappears_after_route_topic(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        phase = env["route_plan"].add_phase(rl.id, "RL 基础")
        s = env["skill_repo"].create("PyTorch", tier="S")
        env["route_repo"].assign_skill(rl.id, s["id"])
        _market(env["skill_service"], "PyTorch")
        assert env["skill_service"].curriculum_gap_skills(route_id=rl.id)
        # 在本 route 添加对应 topic 并建立 linked_topic
        t = env["route_plan"].add_topic(rl.id, phase.id, "PyTorch 基础")
        env["skill_repo"].update(s["id"], linked_topics=[t.id])
        assert env["skill_service"].curriculum_gap_skills(route_id=rl.id) == []

    def test_unbound_skill_not_in_route_gap(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        env["route_plan"].add_phase(rl.id, "RL 基础")
        env["skill_repo"].create("Ranking", tier="S")
        _market(env["skill_service"], "Ranking")
        assert env["skill_service"].curriculum_gap_skills(route_id=rl.id) == []

    def test_market_below_threshold_no_gap(self, env):
        rl = env["route_service"].create_learning_route("强化学习")
        env["route_plan"].ensure_manual_plan(rl.id, rl.name)
        env["route_plan"].add_phase(rl.id, "RL 基础")
        s = env["skill_repo"].create("后训练 / 对齐")
        env["route_repo"].assign_skill(rl.id, s["id"])
        _market(env["skill_service"], "后训练 / 对齐", freq=0.02)
        assert env["skill_service"].curriculum_gap_skills(route_id=rl.id) == []

    def test_global_gap_legacy_still_works(self, env):
        env["skill_repo"].create("Ranking", tier="S")
        _market(env["skill_service"], "Ranking")
        gaps = env["skill_service"].curriculum_gap_skills()
        assert [g["skill"] for g in gaps] == ["Ranking"]
