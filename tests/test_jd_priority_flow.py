"""Phase B 端到端：JD → 技能 → frequency → priority → 1~2 周预览。

- 使用真实 career_context.json seed 技能池（非模拟技能）
- “真实 JD”验证：基于仓库既有真实 JD 研究结论（docs/career_context.json 的
  jd_evidence，华为/科大讯飞等 legacy 记录）重构的代表性 JD 文本——
  技能要求来自真实记录，表述为人工重构，区别于单元测试模板。
- CLI 正式 add-jd 写入数据库
"""

from __future__ import annotations

import json

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.connection import get_connection
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.jd_service import JdService
from app.services.skill_service import SkillService

# 基于仓库真实 JD 研究结论（legacy：Python/PyTorch/LLM/Agent/RAG/SFT/LoRA/
# vLLM/Docker 高频）重构的代表性 JD 文本，标注来源。
REAL_LEGACY_JD = (
    "算法工程师（实习）\n"
    "负责大模型相关算法研究与落地：熟悉 Python、PyTorch，"
    "理解 Transformer 与 LLM 原理；有 SFT / LoRA 微调经验优先；"
    "熟悉 RAG 与 Agent 应用；有 vLLM、Docker 部署经验者优先。"
)


@pytest.fixture()
def seed_env(conn):
    """按真实 career_context 全量 seed 技能池 + 评估/CLI 需要的服务。"""
    skill_repo = SkillRepository(conn)
    jd_repo = JdRepository(conn)
    assessment_repo = AssessmentRepository(conn)
    svc = SkillService(skill_repo, assessment_repo=assessment_repo)
    seeded = svc.seed_from_career_context()
    assert "Python" in seeded
    svc.recompute_all_priority_scores()
    jds = JdService(jd_repo, skill_repo, svc)
    return {
        "conn": conn,
        "skill_repo": skill_repo,
        "jd_repo": jd_repo,
        "assessment_repo": assessment_repo,
        "skill_service": svc,
        "jd_service": jds,
        "seeded": seeded,
    }


class TestRealLegacyJdVerification:
    def test_real_jd_full_loop(self, seed_env):
        """真实 JD → 解析 → 入库 → frequency → priority 全链路。"""
        jd_service = seed_env["jd_service"]
        skill_repo = seed_env["skill_repo"]

        r = jd_service.add_jd(REAL_LEGACY_JD, company="（legacy 记录）",
                              title="算法工程师（实习）")
        assert r["inserted"] is True
        parsed = r["parsed"]
        assert parsed["intern"] is True

        # 技能要求来自真实记录：must 与 plus 区分
        assert "Python" in parsed["must"]
        assert "PyTorch" in parsed["must"]
        assert "Transformer" in parsed["must"]
        assert "LLM 基础" in parsed["must"]
        assert "SFT" in parsed["plus"]      # 有…经验优先 -> plus
        assert "LoRA / QLoRA" in parsed["plus"]
        assert "vLLM" in parsed["plus"]
        assert "Docker" in parsed["plus"]
        assert "RAG" in parsed["must"]      # 熟悉 RAG 与 Agent 应用
        assert "Agent" in parsed["must"]

        # frequency 落库
        freq = skill_repo.get_by_name("PyTorch")["jd_frequency"]
        assert freq["must"] == 1 and freq["total_jds"] == 1

        # 受影响技能出现，且优先级变化有解释
        imp = r["impact"]
        names = {e["skill"] for e in imp["affected_skills"]}
        assert "PyTorch" in names
        assert all(e["explanation"] for e in imp["affected_skills"])

    def test_idempotent_reuse(self, seed_env):
        jd_service = seed_env["jd_service"]
        jd_repo = seed_env["jd_repo"]
        jd_service.add_jd(REAL_LEGACY_JD)
        r2 = jd_service.add_jd(REAL_LEGACY_JD)
        assert r2["idempotent"] is True
        assert len(jd_repo.list_all()) == 1

    def test_gate_blocks_advanced_skill_despite_must(self, seed_env):
        """即使 JD 把 Recsis 高阶技能列为必须，前置未满足仍不越级。"""
        jd_service = seed_env["jd_service"]
        text = (
            "推荐算法工程师：负责用户画像、精排 CTR、Rerank 与 LLM+推荐；"
            "要求熟悉 Ranking、Rerank、CTR。"
        )
        r = jd_service.add_jd(text)
        blocked = {e["skill"] for e in r["impact"]["prerequisite_blocked"]}
        focus = {e["skill"] for e in r["impact"]["weekly_focus"]}
        # Rerank / Ranking / CTR 前置依赖 Embedding/推荐系统基础 等未达标
        assert "Ranking" in blocked or "Rerank" in blocked or "CTR" in blocked
        # 不会因为 JD 高频直接出现在 1~2 周重点里
        assert not ({"Rerank", "Ranking", "CTR"} & focus)

    def test_weekly_preview_uses_seed(self, seed_env):
        w = seed_env["jd_service"].preview_weekly_priorities(
            "2026-09-08", days=7)
        assert w["window_days"] == 7
        # 有激活候选（mastered/learning pool 已 seed）
        assert "Python" not in {
            o["skill"] for o in w["overall_priority"]
        }  # mastered 不进入候选
        assert any(len(d["skills"]) > 0 for d in w["daily_focus"])

    def test_weekly_preview_does_not_create_tasks(self, seed_env):
        conn = seed_env["conn"]
        w = seed_env["jd_service"].preview_weekly_priorities("2026-09-08")
        dates = {d["date"] for d in w["daily_focus"]}
        for d in dates:
            assert TaskRepository(conn).list_by_date(d) == []


class TestPriorityBehaviors:
    def test_new_jd_raises_high_freq_skill_priority(self, seed_env):
        skill_repo = seed_env["skill_repo"]
        before = skill_repo.get_by_name("PyTorch")["priority_score"]
        seed_env["jd_service"].add_jd(REAL_LEGACY_JD)
        after = skill_repo.get_by_name("PyTorch")["priority_score"]
        assert after > before

    def test_mastery_evidence_demotes_despite_jd(self, seed_env):
        skill_repo = seed_env["skill_repo"]
        jd_service = seed_env["jd_service"]
        assessment_repo = seed_env["assessment_repo"]
        jd_service.add_jd(REAL_LEGACY_JD)
        p1 = skill_repo.get_by_name("PyTorch")["priority_score"]
        kp = assessment_repo.create_knowledge_point("pytorch.mastered")
        assessment_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.92,
            last_assessed_at="2026-09-08T10:00:00",
        )
        skill_repo.update(skill_repo.get_by_name("PyTorch")["id"],
                          mastery_ref=f"kp:{kp['id']}")
        seed_env["skill_service"].recompute_all_priority_scores()
        p2 = skill_repo.get_by_name("PyTorch")["priority_score"]
        assert p2 < p1

    def test_connector_bonus_is_bounded(self, seed_env):
        """共享连接点 bonus 只加 0.10，不喧宾夺主。"""
        svc = seed_env["skill_service"]
        skill = seed_env["skill_repo"].get_by_name("Embedding")
        assert skill["shared_connector"] is True
        detail = svc.compute_skill_score(skill)
        assert detail["connector_term"] == pytest.approx(0.10)


class TestCli:
    def test_cli_formal_add_writes(self, seed_env, tmp_path):
        from app.main import _run_add_jd_cli

        conn = seed_env["conn"]
        db = tmp_path / "cli2.db"
        # 先把技能 seed 到 CLI 用的独立库，验证正式入库链路
        code = _run_add_jd_cli([
            "--text", REAL_LEGACY_JD, "--no-ai",
            "--company", "（legacy 记录）",
            "--title", "算法工程师（实习）",
            "--db", str(db), "--today", "2026-09-08",
        ])
        assert code == 0
        c = get_connection(db)
        try:
            assert c.execute("SELECT COUNT(*) FROM jds").fetchone()[0] == 1
            assert c.execute(
                "SELECT COUNT(*) FROM skills").fetchone()[0] >= 20
            row = c.execute("SELECT raw_text FROM jds").fetchone()
            assert REAL_LEGACY_JD.splitlines()[0] in row["raw_text"]
        finally:
            c.close()
