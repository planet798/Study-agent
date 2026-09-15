"""JD 明确 alias 映射、历史 repair、新技能候选、课程缺口测试。"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.jd_summary_repository import (
    JdDailySummaryRepository,
    JdSkillCandidateRepository,
)
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.jd_summary_service import (
    CANDIDATE_MIN_FREQUENCY,
    CANDIDATE_MIN_MENTIONS,
    JdSummaryService,
)
from app.services.market_signal import MarketSignal
from app.services.skill_service import SkillService

DAY = "2026-09-14"
TARGET = "internship"
SKILLS = ("LLM 基础", "模型评估", "VLM", "Python", "PyTorch", "Embedding",
          "Recall", "Ranking", "推荐系统基础", "RAG", "SFT", "Transformer",
          "Hugging Face", "Rerank", "CTR")


def _env(conn):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date="2026-09-01",
                                 end_date="2026-12-31")
    plan_repo.create_phase(plan_id=plan.id, name="阶段一",
                           start_date="2026-09-01", end_date="2026-12-31")
    sr = SkillRepository(conn)
    for n in SKILLS:
        sr.upsert_by_name(n)
    arepo = AssessmentRepository(conn)
    cand_repo = JdSkillCandidateRepository(conn)
    js = JdSummaryService(JdDailySummaryRepository(conn), sr,
                          candidate_repo=cand_repo)
    ss = SkillService(sr, plan_repo=plan_repo, assessment_repo=arepo,
                      market_signal=MarketSignal(js))
    return {"conn": conn, "plan_repo": plan_repo, "sr": sr, "arepo": arepo,
            "cand_repo": cand_repo, "js": js, "ss": ss}


# ================= alias 映射 =================

class TestAliases:
    def test_llm_alias(self, conn):
        env = _env(conn)
        s = env["js"].normalize_skill("LLM/大模型基础")
        assert s is not None and s["name"] == "LLM 基础"

    def test_llm_variants(self, conn):
        env = _env(conn)
        for raw in ("LLM", "大模型", "LLM基础", "Large Language Model"):
            s = env["js"].normalize_skill(raw)
            assert s is not None and s["name"] == "LLM 基础", raw

    def test_eval_alias(self, conn):
        env = _env(conn)
        s = env["js"].normalize_skill("模型评测/Eval/Benchmark")
        assert s is not None and s["name"] == "模型评估"

    def test_vlm_alias(self, conn):
        env = _env(conn)
        s = env["js"].normalize_skill("多模态/VLM/MLLM")
        assert s is not None and s["name"] == "VLM"

    def test_data_cleaning_not_mapped(self, conn):
        env = _env(conn)
        assert env["js"].normalize_skill("数据构建/清洗") is None

    def test_post_training_not_mapped(self, conn):
        env = _env(conn)
        assert env["js"].normalize_skill("DPO/RLHF/GRPO/PPO") is None

    def test_no_fuzzy_contains(self, conn):
        env = _env(conn)
        # 含子串但不等于别名 → 不应误匹配
        assert env["js"].normalize_skill("Python 数据分析") is None
        assert env["js"].normalize_skill("推荐系统基础与工程") is None


# ================= 历史 repair =================

class TestRepair:
    def _seed_legacy_unmatched(self, env, raw="LLM/大模型基础", mention=5):
        env["js"].summary_repo.upsert_summary(
            summary_date=DAY, target_type=TARGET, sample_count=10,
            raw_text=f"{raw} {mention}",
            stats=[{"skill_id": None, "raw_skill_name": raw,
                    "mention_count": mention, "must_count": 0, "plus_count": 0}],
        )

    def test_repair_maps_history(self, conn):
        env = _env(conn)
        self._seed_legacy_unmatched(env)
        res = env["js"].repair_unmatched_jd_skills()
        assert res["repaired"] == 1 and res["remaining"] == 0
        llm = env["sr"].get_by_name("LLM 基础")
        stat = env["js"].summary_repo.list_unmatched_stats()
        assert stat == []
        row = env["conn"].execute(
            "SELECT * FROM jd_daily_skill_stats WHERE raw_skill_name = ?",
            ("LLM/大模型基础",),
        ).fetchone()
        assert row["skill_id"] == llm["id"]

    def test_repair_keeps_raw_name_and_count(self, conn):
        env = _env(conn)
        self._seed_legacy_unmatched(env, mention=7)
        env["js"].repair_unmatched_jd_skills()
        row = env["conn"].execute(
            "SELECT * FROM jd_daily_skill_stats WHERE raw_skill_name = ?",
            ("LLM/大模型基础",),
        ).fetchone()
        assert row["raw_skill_name"] == "LLM/大模型基础"
        assert row["mention_count"] == 7

    def test_repair_idempotent(self, conn):
        env = _env(conn)
        self._seed_legacy_unmatched(env)
        assert env["js"].repair_unmatched_jd_skills()["repaired"] == 1
        assert env["js"].repair_unmatched_jd_skills()["repaired"] == 0

    def test_repair_leaves_unmappable(self, conn):
        env = _env(conn)
        self._seed_legacy_unmatched(env, raw="数据构建/清洗", mention=6)
        res = env["js"].repair_unmatched_jd_skills()
        assert res["repaired"] == 0 and res["remaining"] == 1

    def test_repaired_history_enters_trend(self, conn):
        env = _env(conn)
        self._seed_legacy_unmatched(env)
        env["js"].repair_unmatched_jd_skills()
        t = env["js"].compute_skill_trends(DAY, 30, TARGET)
        names = {r["name"]: r for r in t["skills"]}
        assert "LLM 基础" in names
        assert names["LLM 基础"]["mention_count"] == 5


# ================= 新技能候选 =================

class TestCandidates:
    def test_threshold_constants(self):
        assert CANDIDATE_MIN_MENTIONS == 3
        assert CANDIDATE_MIN_FREQUENCY == 0.10

    def test_high_frequency_enters_candidate(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9\nDPO/RLHF/GRPO/PPO 7",
                               30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        names = {c["canonical_name"] for c in cands}
        assert "数据工程 / 数据清洗" in names
        assert "后训练 / 对齐" in names

    def test_low_frequency_not_candidate(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "Obscure 1", 30, TARGET)
        assert env["js"].refresh_candidates(DAY, 30, TARGET) == []

    def test_low_frequency_no_skill_created(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "Obscure 1", 30, TARGET)
        env["js"].refresh_candidates(DAY, 30, TARGET)
        assert env["sr"].get_by_name("Obscure") is None

    def test_no_skill_before_user_confirms(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        env["js"].refresh_candidates(DAY, 30, TARGET)
        assert env["sr"].get_by_name("数据工程 / 数据清洗") is None

    def test_candidate_idempotent(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        a = env["js"].refresh_candidates(DAY, 30, TARGET)
        b = env["js"].refresh_candidates(DAY, 30, TARGET)
        assert len(a) == len(b) == 1

    def test_accept_creates_skill_not_started(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        cid = cands[0]["id"]
        res = env["js"].accept_candidate(cid, name="数据工程", tier="A")
        skill = res["skill"]
        assert skill["name"] == "数据工程"
        assert skill["status"] == "not_started"
        assert skill["tier"] == "A"
        assert env["cand_repo"].get(cid)["status"] == "accepted"
        # 不创建 task / assessment
        assert env["conn"].execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert env["arepo"].list_attempts() == []

    def test_ignore_excludes_from_planning(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        cid = cands[0]["id"]
        env["js"].ignore_candidate(cid)
        assert env["js"].refresh_candidates(DAY, 30, TARGET) == []
        assert env["sr"].get_by_name("数据工程 / 数据清洗") is None

    def test_accepted_candidate_enters_market_signal(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        env["js"].accept_candidate(cands[0]["id"], name="数据工程", tier="A")
        market = env["ss"].refresh_market(DAY)
        assert market["source"] == "daily_summary"
        # alias 生效后新技能名可被引用：直接以正式名再录一份
        env["js"].save_summary("2026-09-15", "数据工程 6", 10, TARGET)
        market = env["ss"].refresh_market("2026-09-15")
        assert "数据工程" in market["skills"]

    def test_candidate_no_linked_topics(self, conn):
        env = _env(conn)
        env["js"].save_summary(DAY, "数据构建/清洗 9", 30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        env["js"].accept_candidate(cands[0]["id"], name="数据工程", tier="A")
        skill = env["sr"].get_by_name("数据工程")
        assert skill["linked_topics"] == []


# ================= 课程缺口 =================

class TestCurriculumGap:
    def _accept(self, env, name="数据工程", raw="数据构建/清洗"):
        env["js"].save_summary(DAY, f"{raw} 9", 30, TARGET)
        cands = env["js"].refresh_candidates(DAY, 30, TARGET)
        env["js"].accept_candidate(cands[0]["id"], name=name, tier="A")
        return env["sr"].get_by_name(name)

    def test_gap_marked_when_no_topic(self, conn):
        env = _env(conn)
        self._accept(env)
        env["ss"].refresh_market(DAY)
        gaps = {g["skill"]: g for g in env["ss"].curriculum_gap_skills()}
        assert "数据工程" in gaps
        assert gaps["数据工程"]["frequency_30d"] > 0

    def test_gap_not_when_has_topic(self, conn):
        env = _env(conn)
        self._accept(env)
        # 给该技能链一个 topic
        pid = env["conn"].execute(
            "SELECT id FROM study_phases LIMIT 1").fetchone()["id"]
        env["plan_repo"].create_topic(phase_id=pid, name="数据工程主题")
        env["ss"].link_topic_by_name("数据工程", "数据工程主题")
        env["ss"].refresh_market(DAY)
        gaps = {g["skill"] for g in env["ss"].curriculum_gap_skills()}
        assert "数据工程" not in gaps

    def test_gap_does_not_create_tasks(self, conn):
        env = _env(conn)
        self._accept(env)
        env["ss"].refresh_market(DAY)
        env["ss"].curriculum_gap_skills()
        assert env["conn"].execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    def test_gap_requires_market_signal(self, conn):
        env = _env(conn)
        # 直接建一个无 topic 的技能，但市场没有它的频率
        env["sr"].upsert_by_name("孤立技能")
        env["ss"].refresh_market(DAY)
        gaps = {g["skill"] for g in env["ss"].curriculum_gap_skills()}
        assert "孤立技能" not in gaps


# ================= 保存 summary 不建 skill =================

def test_save_summary_does_not_create_unmatched_skill(conn):
    env = _env(conn)
    env["js"].save_summary(DAY, "数据构建/清洗 9\nDPO/RLHF/GRPO/PPO 7",
                           30, TARGET)
    assert env["sr"].get_by_name("数据构建/清洗") is None
    assert env["sr"].get_by_name("DPO/RLHF/GRPO/PPO") is None
