"""SPLIT_NEW / KEEP_LEGACY / MANUAL_REVIEW 迁移测试（Phase 1）。

验证：
- 跨路线语义的旧 Topic 保留在 LEGACY、id/mastery 不变；
- 新的 route-specific Topic 由 canonical seed 创建，且**不带**旧 mastery；
- MANUAL_REVIEW 的 Topic 不被移动。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.services import canonical_routes as C
from app.services.canonical_route_service import CanonicalRouteService
from app.services.route_migration_service import (
    ACTION_MANUAL_REVIEW,
    ACTION_SPLIT_NEW,
    RouteMigrationService,
)


def _legacy_topic(env, name: str):
    return env.plan_repo.find_topic_by_name_in_route(env.legacy_route.id, name)


def _seed_kp_mastery(env, topic_name: str, mastery: float) -> dict:
    topic = _legacy_topic(env, topic_name)
    a_repo = AssessmentRepository(env.conn)
    kp = a_repo.get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, topic.description, route_id=env.legacy_route.id
    )
    a_repo.update_knowledge_point(
        kp["id"], mastery_estimate=mastery,
        last_assessed_at="2026-09-05T10:00:00", review_count=2,
    )
    return {"topic_id": topic.id, "kp_id": kp["id"]}


def _canonical_topic(env, route_key: str, name: str):
    route = env.route_repo.get_by_key(route_key)
    return env.plan_repo.find_topic_by_name_in_route(route.id, name)


class TestSplitNew:
    @pytest.mark.parametrize(
        "legacy_name,new_topics",
        [
            ("Embedding 与向量检索", [
                ("R1_LLM_FUNDAMENTALS", "Embedding Fundamentals"),
                ("R4_AI_AGENT", "Embedding Retrieval for RAG"),
                ("R5_RECOMMENDATION_SEARCH", "Embedding Recall / Search Retrieval"),
            ]),
            ("BM25 与混合检索", [
                ("R4_AI_AGENT", "Hybrid Retrieval for RAG"),
                ("R5_RECOMMENDATION_SEARCH", "BM25 与搜索召回"),
            ]),
            ("KV Cache", [
                ("R1_LLM_FUNDAMENTALS", "KV Cache 原理"),
                ("R3_LLM_INFRA", "KV Cache 管理与推理优化"),
            ]),
            ("SQL 数据分析基础", [
                ("R5_RECOMMENDATION_SEARCH", "推荐 / 搜索数据分析 SQL"),
                ("R6_CS_FUNDAMENTALS", "数据库 / SQL 基础"),
            ]),
            ("C/C++ / CUDA（方向确定后深入）", [
                ("R3_LLM_INFRA", "CUDA / GPU Computing 基础"),
                ("R6_CS_FUNDAMENTALS", "C++ 基础"),
            ]),
        ],
    )
    def test_old_kept_legacy_and_new_created_without_mastery(
        self, six_route_env, legacy_name, new_topics
    ):
        env = six_route_env
        ids = _seed_kp_mastery(env, legacy_name, 0.85)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()

        # 旧 topic 仍在 legacy，id 与 mastery 不变
        old_topic = env.plan_repo.get_topic(ids["topic_id"])
        assert old_topic is not None
        assert old_topic.name == legacy_name
        assert env.plan_repo.get_route_id_for_topic(old_topic.id) == \
            env.legacy_route.id
        a_repo = AssessmentRepository(env.conn)
        kp = a_repo.get_knowledge_point(ids["kp_id"])
        assert float(kp["mastery_estimate"]) == pytest.approx(0.85)

        # 新 route-specific topics 存在
        for route_key, topic_name in new_topics:
            new_topic = _canonical_topic(env, route_key, topic_name)
            assert new_topic is not None, (route_key, topic_name)
            # 新 topic 是不同 id（不复用旧 topic）
            assert new_topic.id != ids["topic_id"]
            # 新 topic 没有 kp / mastery（不继承）
            assert a_repo.get_knowledge_point_by_topic(new_topic.id) is None

    def test_preview_classifies_split(self, six_route_env):
        env = six_route_env
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_pre_migration()
        preview = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).preview()
        entries = {e.old_name: e for e in preview.entries}
        assert entries["Embedding 与向量检索"].action == ACTION_SPLIT_NEW
        assert set(entries["Embedding 与向量检索"].target_route_key.split(",")) == {
            "R1_LLM_FUNDAMENTALS", "R4_AI_AGENT", "R5_RECOMMENDATION_SEARCH"
        }
        assert entries["Evaluation / Badcase / LLM-as-Judge"].action == \
            ACTION_SPLIT_NEW

    def test_legacy_evidence_not_copied_to_new_kps(self, six_route_env):
        env = six_route_env
        ids = _seed_kp_mastery(env, "Reranker 重排序", 0.9)
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        a_repo = AssessmentRepository(env.conn)
        # 新 R4/R5 rerank topics 无 kp
        for route_key, name in [
            ("R4_AI_AGENT", "RAG Reranker"),
            ("R5_RECOMMENDATION_SEARCH", "搜索 / 推荐 Reranking"),
        ]:
            t = _canonical_topic(env, route_key, name)
            assert t is not None
            assert a_repo.get_knowledge_point_by_topic(t.id) is None
        # 旧 kp 仍保留 mastery
        assert float(
            a_repo.get_knowledge_point(ids["kp_id"])["mastery_estimate"]
        ) == pytest.approx(0.9)


class TestKeepLegacyAndManualReview:
    def test_vla_stays_legacy(self, six_route_env):
        env = six_route_env
        topic = _legacy_topic(env, "VLA / World Model（了解）")
        assert topic is not None
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        moved = env.plan_repo.get_topic(topic.id)
        assert moved is not None
        assert env.plan_repo.get_route_id_for_topic(moved.id) == \
            env.legacy_route.id

    def test_vla_classified_manual_review(self, six_route_env):
        env = six_route_env
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_pre_migration()
        preview = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).preview()
        entry = next(
            e for e in preview.entries
            if e.old_name == "VLA / World Model（了解）"
        )
        assert entry.action == ACTION_MANUAL_REVIEW
        # 不在 MOVE 列表中
        assert entry not in preview.move

    def test_vlm_creates_r1_extension_topic(self, six_route_env):
        env = six_route_env
        old = _legacy_topic(env, "VLM / 多模态基础")
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        # 旧 VLM 保留 legacy
        assert env.plan_repo.get_route_id_for_topic(old.id) == \
            env.legacy_route.id
        # R1 有“扩展（可选）”阶段下的新 VLM topic
        r1_new = _canonical_topic(env, "R1_LLM_FUNDAMENTALS",
                                  "VLM / 多模态基础（扩展）")
        assert r1_new is not None
        assert r1_new.id != old.id

    def test_split_topics_have_correct_route(self, six_route_env):
        env = six_route_env
        res = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        # 每个 canonical topic 的 route 唯一且正确（不允许一 topic 多 route）
        for key, route_id in res["route_ids"].items():
            topics = env.plan_repo.list_topics_by_route(route_id)
            for t in topics:
                assert env.plan_repo.get_route_id_for_topic(t.id) == route_id
