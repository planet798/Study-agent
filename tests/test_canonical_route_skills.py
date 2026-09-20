"""Canonical Skill → Route 映射与 linked_topics 测试（Phase 1）。"""

from __future__ import annotations

from app.services import canonical_routes as C
from app.services.canonical_route_service import CanonicalRouteService
from app.services.skill_service import SkillService


def _seed_skills(env):
    SkillService(env.skill_repo, plan_repo=env.plan_repo).seed_from_career_context()


def _route_keys_for_skill(env, skill_name):
    skill = env.skill_repo.get_by_name(skill_name)
    assert skill is not None, skill_name
    ids = set(env.route_repo.list_route_ids_for_skill(skill["id"]))
    key_by_id = {
        r.id: r.route_key for r in env.route_repo.list_all()
    }
    return {key_by_id.get(i) for i in ids}


class TestCanonicalBinding:
    def test_bindings(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        expected = {
            "Python": {C.ROUTE_KEY_R6},
            "Linux": {C.ROUTE_KEY_R6},
            "Git": {C.ROUTE_KEY_R6},
            "C++": {C.ROUTE_KEY_R6},
            "PyTorch": {C.ROUTE_KEY_R1, C.ROUTE_KEY_R2, C.ROUTE_KEY_R5},
            "Transformer": {C.ROUTE_KEY_R1, C.ROUTE_KEY_R2},
            "LLM 基础": {C.ROUTE_KEY_R1, C.ROUTE_KEY_R2, C.ROUTE_KEY_R4},
            "SFT": {C.ROUTE_KEY_R2},
            "LoRA / QLoRA": {C.ROUTE_KEY_R2, C.ROUTE_KEY_R3},
            "Embedding": {C.ROUTE_KEY_R1, C.ROUTE_KEY_R4, C.ROUTE_KEY_R5},
            "RAG": {C.ROUTE_KEY_R4},
            "Agent": {C.ROUTE_KEY_R4},
            "推荐系统基础": {C.ROUTE_KEY_R5},
            "Recall": {C.ROUTE_KEY_R5},
            "Ranking": {C.ROUTE_KEY_R5},
            "Rerank": {C.ROUTE_KEY_R4, C.ROUTE_KEY_R5},
            "LLM + Recommendation": {C.ROUTE_KEY_R4, C.ROUTE_KEY_R5},
            "vLLM": {C.ROUTE_KEY_R3},
            "Docker": {C.ROUTE_KEY_R3, C.ROUTE_KEY_R6},
            "CUDA": {C.ROUTE_KEY_R3, C.ROUTE_KEY_R6},
            "SQL": {C.ROUTE_KEY_R5, C.ROUTE_KEY_R6},
            "模型评估": {
                C.ROUTE_KEY_R1, C.ROUTE_KEY_R2,
                C.ROUTE_KEY_R4, C.ROUTE_KEY_R5,
            },
        }
        for skill, keys in expected.items():
            assert _route_keys_for_skill(env, skill) == keys, skill

    def test_multi_route_skill(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        assert len(_route_keys_for_skill(env, "PyTorch")) == 3
        assert len(_route_keys_for_skill(env, "模型评估")) == 4

    def test_route_skills_idempotent(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        svc = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        )
        svc.ensure_all()
        first = env.route_repo.count_route_skills()
        svc.ensure_all()
        assert env.route_repo.count_route_skills() == first


class TestAliasTargetsExist:
    def test_all_explicit_alias_targets_exist(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        from app.services.jd_summary_service import _EXPLICIT_ALIASES

        missing = sorted({
            target for target in _EXPLICIT_ALIASES.values()
            if env.skill_repo.get_by_name(target) is None
        })
        assert missing == [], f"alias 指向不存在的 skill: {missing}"

    def test_extra_skills_created(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        for name in ("PEFT", "MoE", "后训练 / 对齐", "模型蒸馏"):
            assert env.skill_repo.get_by_name(name) is not None, name

    def test_post_training_alias_normalizes(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        from app.database.jd_summary_repository import JdDailySummaryRepository
        from app.services.jd_summary_service import JdSummaryService

        svc = JdSummaryService(JdDailySummaryRepository(env.conn), env.skill_repo)
        for raw in ("RLHF", "DPO", "GRPO", "PPO"):
            hit = svc.normalize_skill(raw)
            assert hit is not None and hit["name"] == "后训练 / 对齐", raw


class TestLinkedTopics:
    def test_split_topics_linked_to_skills(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        res = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        embedding = env.skill_repo.get_by_name("Embedding")
        linked = set(embedding["linked_topics"])
        r1 = env.plan_repo.find_topic_by_name_in_route(
            res["route_ids"]["R1_LLM_FUNDAMENTALS"], "Embedding Fundamentals")
        r5 = env.plan_repo.find_topic_by_name_in_route(
            res["route_ids"]["R5_RECOMMENDATION_SEARCH"],
            "Embedding Recall / Search Retrieval")
        assert r1.id in linked
        assert r5.id in linked

    def test_legacy_embedding_topic_id_not_reused(self, six_route_env):
        env = six_route_env
        _seed_skills(env)
        legacy_topic = env.plan_repo.find_topic_by_name_in_route(
            env.legacy_route.id, "Embedding 与向量检索")
        res = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        embedding = env.skill_repo.get_by_name("Embedding")
        linked = set(embedding["linked_topics"])
        # 新 canonical topics 各自有独立 id（不复用 legacy id 来代表 R1/R4/R5）
        new_ids = {
            env.plan_repo.find_topic_by_name_in_route(
                res["route_ids"]["R1_LLM_FUNDAMENTALS"],
                "Embedding Fundamentals").id,
            env.plan_repo.find_topic_by_name_in_route(
                res["route_ids"]["R4_AI_AGENT"],
                "Embedding Retrieval for RAG").id,
            env.plan_repo.find_topic_by_name_in_route(
                res["route_ids"]["R5_RECOMMENDATION_SEARCH"],
                "Embedding Recall / Search Retrieval").id,
        }
        assert len(new_ids) == 3
        assert legacy_topic.id not in new_ids
        assert new_ids.issubset(linked)

    def test_move_topic_link_preserved(self, six_route_env):
        env = six_route_env
        # 先手工建立 RAG skill 并链接 legacy RAG topic id
        rag_topic = env.plan_repo.find_topic_by_name_in_route(
            env.legacy_route.id, "RAG 全流程搭建")
        env.skill_repo.create("RAG", tier="S", linked_topics=[rag_topic.id])
        _seed_skills(env)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        rag = env.skill_repo.get_by_name("RAG")
        assert rag_topic.id in rag["linked_topics"]
        # topic id 未变，route 已变为 R4
        moved = env.plan_repo.get_topic(rag_topic.id)
        assert env.plan_repo.get_route_id_for_topic(moved.id) == \
            env.route_repo.get_by_key("R4_AI_AGENT").id
