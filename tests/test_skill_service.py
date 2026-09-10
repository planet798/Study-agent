"""Phase A：skills / jds / learning_outcomes 数据底座 + SkillService 确定性计算。

覆盖：
1. v5 → v6 migration（见 test_schema_migrations.py）
2. migration 幂等（见 test_schema_migrations.py）
3. 旧数据保留（见 test_schema_migrations.py）
4. skills CRUD / validation
5. tier 权重（S=4 / A=3 / B=2 / C=1）
6. JD frequency 计算
7. mastery 影响 active_needed
8. mastered 降权
9. prerequisite gate
10. connector bonus
11. priority_score 可重复计算（确定性）
12. 没有 JD 数据时正常 fallback
13. 没有 mastery evidence 时不误判
14. skill ↔ topic 映射
15. learning_outcomes 正常保存
16. jds / skills 数据结构正确

边界：本文件不接 Planner，不改每日任务生成。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.skill_repository import (
    JdRepository,
    LearningOutcomeRepository,
    SkillRepository,
    STATUSES,
    TIERS,
)
from app.services.skill_service import (
    JD_FALLBACK_FACTOR,
    MASTERY_HIGH,
    MASTERY_LOW,
    CONNECTOR_DEFAULT,
    SkillService,
    W_ACTIVE,
    W_CONNECTOR,
    W_JD,
    W_TIER,
)

REC_TOPIC_NAME = "PyTorch 张量与自动求导（Tensor / autograd）"


@pytest.fixture()
def skill_repo(conn):
    return SkillRepository(conn)


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def svc(skill_repo):
    return SkillService(skill_repo)


def _topic_row(plan_repo):
    plan = plan_repo.create_plan(
        name="mini", start_date="2026-09-01", end_date="2026-12-31",
        description="mini",
    )
    phase = plan_repo.create_phase(
        plan_id=plan.id, name="mini-phase", start_date="2026-09-01",
        end_date="2026-12-31", goals="g",
    )
    return plan_repo.create_topic(
        phase_id=phase.id, name=REC_TOPIC_NAME,
        estimated_minutes=60, priority=3,
    )


class TestSkillCrud:
    def test_create_get_update_list_delete(self, skill_repo):
        s = skill_repo.create(
            name="PyTorch", tier="S", category="core",
            status="learning", prerequisites=["Python"],
            linked_topics=[1, 2], shared_connector=False,
            jd_frequency={"must": 3, "plus": 1, "total_jds": 10},
        )
        assert s["id"] > 0
        assert s["tier"] == "S"
        assert s["status"] == "learning"
        assert s["prerequisites"] == ["Python"]
        assert s["linked_topics"] == [1, 2]
        assert s["shared_connector"] is False
        assert s["jd_frequency"]["must"] == 3

        got = skill_repo.get(s["id"])
        assert got["name"] == "PyTorch"
        assert skill_repo.get_by_name("PyTorch")["id"] == s["id"]

        skill_repo.update(s["id"], status="mastered")
        assert skill_repo.get(s["id"])["status"] == "mastered"

        assert skill_repo.list_all()  # 至少一个
        assert skill_repo.delete(s["id"]) is True
        assert skill_repo.get(s["id"]) is None

    def test_validation_rejects_bad_tier_and_status(self, skill_repo):
        with pytest.raises(ValueError):
            skill_repo.create(name="X", tier="Z")
        with pytest.raises(ValueError):
            skill_repo.create(name="X", status="finished")
        with pytest.raises(ValueError):
            skill_repo.create(name="  ", tier="S")

    def test_json_fields_roundtrip(self, skill_repo):
        s = skill_repo.create(
            name="RAG", tier="S", category="llm",
            jd_frequency={"must": 0, "plus": 0, "total_jds": 0},
            prerequisites=["Embedding", "LLM 基础"], linked_topics=[9],
        )
        got = skill_repo.get(s["id"])
        assert got["prerequisites"] == ["Embedding", "LLM 基础"]
        assert got["linked_topics"] == [9]
        assert got["jd_frequency"] == {"must": 0, "plus": 0, "total_jds": 0}


class TestTierWeight:
    def test_tier_weight_map(self, svc):
        assert svc.tier_weight("S") == 4.0
        assert svc.tier_weight("A") == 3.0
        assert svc.tier_weight("B") == 2.0
        assert svc.tier_weight("C") == 1.0
        assert svc.tier_weight("s") == 4.0  # 大小写不敏感
        # 未知 tier 兜底为 C=1
        assert SkillService.tier_weight("???") == 1.0


class TestJdFactor:
    def test_jd_factor_must_heavier_than_plus(self, skill_repo, svc):
        assert svc.jd_factor({"must": 5, "plus": 0, "total_jds": 10}) == pytest.approx(0.4)
        assert svc.jd_factor({"must": 0, "plus": 5, "total_jds": 10}) == pytest.approx(0.2)
        mixed = svc.jd_factor({"must": 5, "plus": 5, "total_jds": 10})
        assert mixed == pytest.approx(0.6)

    def test_no_jd_data_fallback_is_neutral(self, svc):
        # 无 JD 证据：中性回退，不引入虚构提升/降权
        assert svc.jd_factor({}) == JD_FALLBACK_FACTOR == 0.0
        assert svc.jd_factor(None) == 0.0
        assert svc.jd_factor({"must": 0, "plus": 0, "total_jds": 0}) == 0.0

    def test_jd_term_raises_score_deterministically(self, skill_repo, svc):
        a = skill_repo.create(name="SQL", tier="A")
        b = skill_repo.create(
            name="SQL+", tier="A",
            jd_frequency={"must": 5, "plus": 0, "total_jds": 10},
        )
        da = svc.compute_skill_score(a)
        db = svc.compute_skill_score(b)
        assert db["score"] > da["score"]
        # 只有 jd_term 不同
        assert db["tier_term"] == da["tier_term"]
        assert db["active_term"] == da["active_term"]
        assert db["jd_term"] > da["jd_term"]
        # 确定性：两次计算完全一致
        assert svc.compute_skill_score(b) == db


class TestActiveNeeded:
    def test_mastered_demotes_to_zero(self, svc):
        assert SkillService.active_needed("mastered") == 0.0
        assert SkillService.active_needed("deferred") == 0.0

    def test_not_started_and_learning_are_high(self, svc):
        assert SkillService.active_needed("not_started") == pytest.approx(0.8)
        assert SkillService.active_needed("learning") == pytest.approx(0.7)

    def test_mastery_weak_low_evidence_boosts(self, svc):
        # 明确薄弱证据（<= MASTERY_LOW）→ 巩固优先
        weak = SkillService.active_needed("not_started", mastery_estimate=0.3)
        base = SkillService.active_needed("not_started", mastery_estimate=None)
        assert weak == pytest.approx(0.9)
        assert weak > base

    def test_mastery_high_evidence_demotes(self, svc):
        high = SkillService.active_needed("not_started", mastery_estimate=0.95)
        assert high == pytest.approx(0.15)
        assert high < SkillService.active_needed("not_started")

    def test_prerequisite_not_satisfied_lowers(self, svc):
        low = SkillService.active_needed("not_started",
                                         prerequisite_satisfied=False)
        ok = SkillService.active_needed("not_started",
                                        prerequisite_satisfied=True)
        assert low <= 0.3
        assert low < ok

    def test_no_mastery_is_not_misjudged(self, skill_repo, svc):
        """没有 mastery 证据：不推断为 weak，保持 status 基准。"""
        s = skill_repo.create(name="PyTorch", tier="S", status="learning")
        detail = svc.compute_skill_score(s)
        assert detail["mastery_estimate"] is None
        # learning 基准 0.7 → active_term = 0.15*0.7
        assert detail["active_term"] == pytest.approx(W_ACTIVE * 0.7)

        # mastery_ref 指向不存在的 kp 也等同“无证据”
        skill_repo.update(s["id"], mastery_ref="kp:999")
        detail2 = svc.compute_skill_score(skill_repo.get(s["id"]))
        assert detail2["mastery_estimate"] is None


class TestMasteryFromAssessment:
    def test_low_mastery_from_assessment_raises_active_term(
        self, conn, skill_repo, assessment_repo
    ):
        svc = SkillService(skill_repo, assessment_repo=assessment_repo)
        s = skill_repo.create(name="PyTorch", tier="S", status="learning")
        kp = assessment_repo.create_knowledge_point("pytorch.core")
        assessment_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.3,
            last_assessed_at="2026-09-08T10:00:00",
        )
        skill_repo.update(s["id"], mastery_ref=f"kp:{kp['id']}")

        with_evidence = svc.compute_skill_score(skill_repo.get(s["id"]))
        assert with_evidence["mastery_estimate"] == pytest.approx(0.3)
        assert with_evidence["active_term"] == pytest.approx(W_ACTIVE * 0.9)

        # 未挂接 assessment_repo → 无证据，不推断
        no_repo = SkillService(skill_repo)
        detail = no_repo.compute_skill_score(skill_repo.get(s["id"]))
        assert detail["mastery_estimate"] is None


class TestConnectorBonus:
    def test_connector_names_flagged(self, skill_repo, svc):
        for name in CONNECTOR_DEFAULT:
            assert svc.connector_value(name) == 1.0
        assert svc.connector_value("Python") == 0.0
        # shared_connector 显式标记也生效
        s = skill_repo.create(name="User Embedding", tier="A")
        skill_repo.update(s["id"], shared_connector=True)
        assert svc.connector_value(
            "User Embedding", shared_connector=True
        ) == 1.0

    def test_connector_term_bounded(self, skill_repo, svc):
        s = skill_repo.create(name="Embedding", tier="S")
        detail = svc.compute_skill_score(s)
        # 有限 bonus：= W_CONNECTOR * 1.0，不大
        assert detail["connector_term"] == pytest.approx(W_CONNECTOR)


class TestPrerequisiteGate:
    def _seed_route(self, skill_repo):
        skill_repo.create(name="推荐系统基础", tier="S", status="not_started")
        skill_repo.create(name="Recall", tier="S", status="not_started")
        skill_repo.create(name="Ranking", tier="S", status="not_started",
                          prerequisites=["推荐系统基础", "Recall"])
        skill_repo.create(name="Python", tier="S", status="mastered")

    def test_missing_prerequisites_and_effective_status(
        self, skill_repo, svc
    ):
        self._seed_route(skill_repo)
        ranking = skill_repo.get_by_name("Ranking")
        assert svc.missing_prerequisites(ranking) == [
            "推荐系统基础", "Recall"
        ]
        assert svc.is_blocked(ranking) is True
        assert svc.effective_status(ranking) == "deferred"  # 视为 blocked/deferred
        # 前置达标后放行
        skill_repo.update(skill_repo.get_by_name("推荐系统基础")["id"],
                          status="mastered")
        skill_repo.update(skill_repo.get_by_name("Recall")["id"],
                          status="mastered")
        ranking2 = skill_repo.get_by_name("Ranking")
        assert svc.missing_prerequisites(ranking2) == []
        assert svc.is_blocked(ranking2) is False
        assert svc.effective_status(ranking2) == "not_started"

    def test_mastery_evidence_satisfies_prerequisite(
        self, conn, skill_repo, assessment_repo
    ):
        """前置技能即使状态未标 mastered，有高掌握证据也算达标。"""
        svc = SkillService(skill_repo, assessment_repo=assessment_repo)
        skill_repo.create(name="Recall", tier="S", status="not_started")
        skill_repo.create(name="Ranking", tier="S", status="not_started",
                          prerequisites=["Recall"])
        kp = assessment_repo.create_knowledge_point("recall.core")
        assessment_repo.update_knowledge_point(kp["id"], mastery_estimate=0.9,
                                               last_assessed_at="2026-09-08")
        skill_repo.update(skill_repo.get_by_name("Recall")["id"],
                          mastery_ref=f"kp:{kp['id']}")
        ranking = skill_repo.get_by_name("Ranking")
        assert svc.missing_prerequisites(ranking) == []
        assert svc.is_blocked(ranking) is False

    def test_select_active_candidates_excludes_blocked_and_mastered(
        self, skill_repo, svc
    ):
        self._seed_route(skill_repo)
        cands = svc.select_active_candidates()
        names = [c["name"] for c in cands]
        assert "Embedding" not in names  # 不存在
        assert "推荐系统基础" in names   # not_started + 前置为空
        # Ranking 被前置阻塞 → 不入候选；Python mastered → 不入候选
        assert "Ranking" not in names
        assert "Python" not in names
        for c in cands:
            assert c["gate"] == "ok"


class TestRecomputeDeterministic:
    def test_recompute_twice_is_stable(self, skill_repo, svc):
        skill_repo.create(name="Python", tier="S", status="mastered")
        skill_repo.create(name="Embedding", tier="S", status="not_started",
                          shared_connector=True)
        skill_repo.create(name="Ranking", tier="A", status="not_started",
                          prerequisites=["推荐系统基础", "Recall"])
        first = svc.recompute_all_priority_scores()
        second = svc.recompute_all_priority_scores()
        assert [d["score"] for d in first] == [d["score"] for d in second]
        # 落库生效
        assert skill_repo.get_by_name("Embedding")["priority_score"] == \
            first[0]["score"]
        assert skill_repo.get_by_name("Embedding")["priority_score"] == \
            pytest.approx(W_TIER * 4.0 + W_ACTIVE * 0.8 + W_CONNECTOR)

    def test_blocked_skill_keeps_lower_score_even_at_high_tier(
        self, skill_repo, svc
    ):
        skill_repo.create(name="LLM + Recommendation", tier="S",
                          status="not_started",
                          prerequisites=["RAG", "Ranking"])
        detail = svc.recompute_all_priority_scores()[0]
        assert detail["gate"] == "blocked"
        # 前置未满足 → active 被压低到 <=0.3
        assert detail["active_term"] <= (W_ACTIVE * 0.3 + 1e-9)


class TestSkillTopicMapping:
    def test_link_and_reverse_lookup(self, conn, skill_repo, plan_repo):
        svc = SkillService(skill_repo, plan_repo=plan_repo)
        skill_repo.create(name="PyTorch", tier="S")
        topic = _topic_row(plan_repo)
        assert svc.link_topic_by_name("PyTorch", REC_TOPIC_NAME) is True
        # 未知主题：不伪造映射，返回 False
        assert svc.link_topic_by_name("PyTorch", "不存在的主题") is False

        assert svc.topics_for_skill("PyTorch") == [topic.id]
        assert svc.skills_for_topic(topic.id) == ["PyTorch"]
        # 一个 topic 服务多个 skill
        skill_repo.create(name="深度学习", tier="A")
        assert svc.link_topic_by_name("深度学习", REC_TOPIC_NAME) is True
        assert set(svc.skills_for_topic(topic.id)) == {"PyTorch", "深度学习"}

    def test_link_all_from_map(self, conn, skill_repo, plan_repo):
        svc = SkillService(skill_repo, plan_repo=plan_repo)
        skill_repo.create(name="PyTorch", tier="S")
        _topic_row(plan_repo)
        result = svc.link_all_from_map({
            "PyTorch": [REC_TOPIC_NAME, "未知主题"],
        })
        assert result["PyTorch"] is False  # 有一个缺失 → 整项 False
        assert svc.topics_for_skill("PyTorch")  # 已关联已知主题


class TestSeedFromCareerContext:
    def test_seed_from_real_context(self, conn, skill_repo, plan_repo):
        svc = SkillService(skill_repo, plan_repo=plan_repo)
        seeded = svc.seed_from_career_context()
        assert "Python" in seeded
        assert "SFT" in seeded
        skill = skill_repo.get_by_name("推荐系统基础")
        assert skill is not None
        assert skill["tier"] == "S"
        assert skill["prerequisites"] == ["Python", "PyTorch"]
        assert skill["status"] == "not_started"

        # 状态映射：mastered / learning / deferred
        assert skill_repo.get_by_name("Python")["status"] == "mastered"
        assert skill_repo.get_by_name("PyTorch")["status"] == "learning"
        assert skill_repo.get_by_name("vLLM")["status"] == "deferred"

        # 共享连接点
        for name in CONNECTOR_DEFAULT:
            assert skill_repo.get_by_name(name)["shared_connector"] is True

        # 幂等：再跑一次不重复、不覆盖 status
        total_before = len(skill_repo.list_all())
        seeded2 = svc.seed_from_career_context()
        assert len(skill_repo.list_all()) == total_before
        assert sorted(seeded2) == sorted(seeded)
        # Python 仍是 mastered（seed 不覆盖 status）
        assert skill_repo.get_by_name("Python")["status"] == "mastered"


class TestLearningOutcomes:
    def test_save_and_retrieve(self, conn, skill_repo):
        repo = LearningOutcomeRepository(conn)
        o = repo.create(
            date="2026-09-10",
            kind="project",
            title="LoRA 微调 Qwen 实现",
            content="训练并评估，测试集损失下降。",
            tech_stack=["LoRA", "PEFT", "transformers", "PyTorch"],
            dataset="xxx-alpaca",
            metrics={"eval_loss": 0.12, "perplexity": 3.1},
            git_commit="abc123",
            github_url="https://github.com/x/y",
            resume_keywords=["SFT", "LoRA", "模型评估"],
            linked_kp_id=1,
            linked_topic_id=9,
        )
        got = repo.get(o["id"])
        assert got["kind"] == "project"
        assert got["tech_stack"] == ["LoRA", "PEFT", "transformers", "PyTorch"]
        assert got["metrics"] == {"eval_loss": 0.12, "perplexity": 3.1}
        assert got["git_commit"] == "abc123"
        assert got["resume_keywords"][0] == "SFT"
        assert repo.list_all(kind="project")[0]["id"] == o["id"]
        # 更新
        repo.update(o["id"], metrics={"eval_loss": 0.09})
        assert repo.get(o["id"])["metrics"]["eval_loss"] == 0.09


class TestJds:
    def test_raw_text_preserved_and_parsed_structured(self, conn, skill_repo):
        repo = JdRepository(conn)
        raw = "职位：推荐算法实习生\n要求：熟悉 Python、PyTorch，有 Recall/Ranking 经验加分\n"
        jd = repo.create(
            company="某公司",
            title="推荐算法实习生",
            direction="recommendation",
            intern_requirement="每周 4 天以上",
            raw_text=raw,
            parsed={"must": ["Python", "PyTorch", "Recall", "Ranking"],
                    "plus": ["LLM", "RAG"], "keywords": {"Python": 3}},
        )
        got = repo.get(jd["id"])
        # 原文必须完整保留
        assert got["raw_text"] == raw
        assert "Recall/Ranking" in got["raw_text"]
        assert got["parsed"]["must"] == ["Python", "PyTorch", "Recall", "Ranking"]
        assert got["parsed"]["keywords"]["Python"] == 3
        assert got["direction"] == "recommendation"
        assert got["intern_requirement"] == "每周 4 天以上"
        assert repo.list_by_direction("recommendation")[0]["id"] == jd["id"]
