"""PracticeProject ↔ Route / Skill / Topic 关系测试（Phase 4）。"""

from __future__ import annotations

import pytest

from app.services.practice_project_service import PracticeError


def _topic(env, route, name):
    return env.plan_repo.find_topic_by_name_in_route(route.id, name)


class TestRouteRelations:
    def test_route_nn_and_idempotent(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other",
                                       route_ids=[env.r1.id, env.r2.id])
        assert set(env.service.projects.list_route_ids(p["id"])) == {
            env.r1.id, env.r2.id
        }
        # 重复添加幂等
        assert env.service.add_route(p["id"], env.r1.id) is False
        assert len(env.service.projects.list_route_ids(p["id"])) == 2

    def test_group_route_rejected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.add_route(p["id"], env.group.id)

    def test_archived_route_new_relation_rejected(self, practice_env):
        env = practice_env
        legacy = env.route_repo.get_by_key("LEGACY_SEARCH_LLM")
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.add_route(p["id"], legacy.id)

    def test_multiple_routes(self, practice_env):
        env = practice_env
        p = env.service.create_project(
            "P", "other", route_ids=[env.r1.id, env.r2.id, env.r3.id]
        )
        assert len(env.service.projects.list_route_ids(p["id"])) == 3


class TestTopicRelations:
    def test_topic_must_be_in_project_routes(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        lora = _topic(env, env.r2, "LoRA / QLoRA")
        with pytest.raises(PracticeError):
            env.service.add_topic(p["id"], lora.id)

    def test_topic_ok_when_route_linked(self, practice_env):
        env = practice_env
        p = env.service.create_project(
            "P", "other", route_ids=[env.r1.id, env.r2.id]
        )
        lora = _topic(env, env.r2, "LoRA / QLoRA")
        assert env.service.add_topic(p["id"], lora.id) is True
        assert env.service.projects.list_topic_ids(p["id"]) == [lora.id]

    def test_remove_route_with_linked_topic_rejected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        qwen = _topic(env, env.r1, "Qwen / LLaMA 架构：GQA / SwiGLU")
        env.service.add_topic(p["id"], qwen.id)
        with pytest.raises(PracticeError):
            env.service.remove_route(p["id"], env.r1.id)
        assert env.service.projects.list_route_ids(p["id"]) == [env.r1.id]

    def test_set_routes_rejects_removing_route_with_topic(
        self, practice_env
    ):
        env = practice_env
        p = env.service.create_project(
            "P", "other", route_ids=[env.r1.id, env.r2.id]
        )
        qwen = _topic(env, env.r1, "Qwen / LLaMA 架构：GQA / SwiGLU")
        env.service.add_topic(p["id"], qwen.id)
        with pytest.raises(PracticeError):
            env.service.set_routes(p["id"], [env.r2.id])
        assert set(env.service.projects.list_route_ids(p["id"])) == {
            env.r1.id, env.r2.id
        }

    def test_set_topics_transaction_rollback(self, practice_env):
        env = practice_env
        p = env.service.create_project(
            "P", "other", route_ids=[env.r1.id, env.r2.id]
        )
        qwen = _topic(env, env.r1, "Qwen / LLaMA 架构：GQA / SwiGLU")
        env.service.add_topic(p["id"], qwen.id)
        r6_topic = _topic(env, env.r6, "C++ 基础")
        # r6 未关联 → 校验失败，existing topic 不被清空
        with pytest.raises(PracticeError):
            env.service.set_topics(p["id"], [qwen.id, r6_topic.id])
        assert env.service.projects.list_topic_ids(p["id"]) == [qwen.id]

    def test_multiple_topics_cross_route(self, practice_env):
        env = practice_env
        p = env.service.create_project(
            "P", "other", route_ids=[env.r1.id, env.r2.id, env.r3.id]
        )
        names = [
            (env.r1, "Qwen / LLaMA 架构：GQA / SwiGLU"),
            (env.r2, "LoRA / QLoRA"),
            (env.r3, "vLLM 与 PagedAttention"),
        ]
        ids = []
        for route, name in names:
            t = _topic(env, route, name)
            env.service.add_topic(p["id"], t.id)
            ids.append(t.id)
        assert set(env.service.projects.list_topic_ids(p["id"])) == set(ids)
        assert env.service.count_by_topic(ids[0]) == 1
        assert env.service.list_by_topic(ids[1])[0]["id"] == p["id"]


class TestSkillRelations:
    def test_skill_nn_and_idempotent(self, practice_env):
        env = practice_env
        s1 = env.skill_repo.create("LoRA / QLoRA", tier="S")
        s2 = env.skill_repo.create("PyTorch", tier="S")
        p = env.service.create_project("P", "other")
        assert env.service.add_skill(p["id"], s1["id"]) is True
        assert env.service.add_skill(p["id"], s1["id"]) is False
        env.service.add_skill(p["id"], s2["id"])
        assert set(env.service.projects.list_skill_ids(p["id"])) == {
            s1["id"], s2["id"]
        }
        assert env.service.count_by_skill(s1["id"]) == 1
        assert env.service.list_by_skill(s1["id"])[0]["id"] == p["id"]

    def test_skill_relation_does_not_create_route_skill(self, practice_env):
        env = practice_env
        s = env.skill_repo.create("MySkill", tier="B")
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        env.service.add_skill(p["id"], s["id"])
        # Project 使用 Skill ≠ Skill 成为 Route curriculum skill
        assert s["id"] not in env.route_repo.list_skill_ids(env.r1.id)

    def test_invalid_skill_rejected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.add_skill(p["id"], 999999)

    def test_multiple_skills(self, practice_env):
        env = practice_env
        ids = [env.skill_repo.create(f"S{i}", tier="B")["id"] for i in range(3)]
        p = env.service.create_project("P", "other")
        for sid in ids:
            env.service.add_skill(p["id"], sid)
        assert set(env.service.projects.list_skill_ids(p["id"])) == set(ids)
