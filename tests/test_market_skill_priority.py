"""Step 6：近期市场信号 → SkillService priority 测试。"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.jd_summary_service import JdSummaryService
from app.services.market_signal import MarketSignal, confidence, combine_signal
from app.services.skill_service import SkillService

D = "2026-09-14"

SKILLS = ("Python", "PyTorch", "Transformer", "Embedding", "Recall",
          "Ranking", "RAG", "LLM 基础", "推荐系统基础", "SQL")


def _env(conn):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date="2026-09-01",
                                 end_date="2026-12-31")
    p1 = plan_repo.create_phase(plan_id=plan.id, name="阶段一",
                                start_date="2026-09-01",
                                end_date="2026-09-30")
    p2 = plan_repo.create_phase(plan_id=plan.id, name="阶段二",
                                start_date="2026-10-01",
                                end_date="2026-12-31")
    t_cur = plan_repo.create_topic(phase_id=p1.id, name="Transformer 主题")
    t_fut = plan_repo.create_topic(phase_id=p2.id, name="SQL 主题")
    sr = SkillRepository(conn)
    for n in SKILLS:
        sr.upsert_by_name(n)
    arepo = AssessmentRepository(conn)
    js = JdSummaryService(JdDailySummaryRepository(conn), sr)
    ss = SkillService(sr, plan_repo=plan_repo, assessment_repo=arepo,
                      market_signal=MarketSignal(js))
    ss.link_topic_by_name("Transformer", "Transformer 主题")
    ss.link_topic_by_name("SQL", "SQL 主题")
    return {"conn": conn, "plan_repo": plan_repo, "p1": p1, "p2": p2,
            "sr": sr, "arepo": arepo, "js": js, "ss": ss}


# ================= 1~3：来源选择 / 不 double count =================

class TestSourceSelection:
    def test_no_summary_falls_back_to_individual_jd(self, conn):
        env = _env(conn)
        sr, ss = env["sr"], env["ss"]
        py = sr.get_by_name("Python")
        sr.update(py["id"], jd_frequency={"must": 5, "plus": 0, "total_jds": 10})
        market = ss.refresh_market(D)
        assert market["source"] == "none"
        assert ss.market_factor(sr.get_by_name("Python")) == pytest.approx(0.4)

    def test_summary_used_when_present(self, conn):
        env = _env(conn)
        env["js"].save_summary(D, "Python 8\nPyTorch 7", 10)
        market = env["ss"].refresh_market(D)
        assert market["source"] == "daily_summary"
        assert market["skills"]["Python"]["freq14"] == pytest.approx(0.8)
        assert env["ss"].market_factor(
            env["sr"].get_by_name("Python")) == pytest.approx(0.8)

    def test_no_double_count_individual_and_summary(self, conn):
        env = _env(conn)
        sr, ss = env["sr"], env["ss"]
        # individual JD 非常高
        py = sr.get_by_name("Python")
        sr.update(py["id"], jd_frequency={"must": 10, "plus": 0, "total_jds": 10})
        # summary 只有 50%
        env["js"].save_summary(D, "Python 5", 10)
        ss.refresh_market(D)
        assert ss.market_factor(sr.get_by_name("Python")) == pytest.approx(0.5)


# ================= 4~6：14/30 天组合与样本量 =================

class TestWindowCombination:
    def test_confidence_monotonic(self):
        assert confidence(0) == 0.0
        assert confidence(20) == pytest.approx(0.5)
        assert confidence(80) == pytest.approx(0.8)

    def test_combine_formula(self):
        # n14=20 → conf 0.5 → 0.5*0.8 + 0.5*0.2 = 0.5
        assert combine_signal(0.8, 0.2, 20, 30) == pytest.approx(0.5)
        assert combine_signal(0.8, 0.2, 0, 30) == pytest.approx(0.2)
        assert combine_signal(0.0, 0.0, 0, 0) == 0.0

    def test_small_14d_smoothed_by_30d(self, conn):
        env = _env(conn)
        # 30 天窗口里 10 个岗位，Python 2 次（20%）；最后一天 1 个岗位 Python 1 次
        env["js"].save_summary("2026-08-25", "Python 2", 10)
        env["js"].save_summary(D, "Python 1", 1)
        market = env["ss"].refresh_market(D)
        rec = market["skills"]["Python"]
        assert market["sample_count_14d"] == 1
        assert rec["freq14"] == pytest.approx(1.0)
        # 平滑后应明显低于 1.0（被 30 天压低）
        assert rec["signal"] < 0.5
        assert rec["signal"] == pytest.approx(
            combine_signal(rec["freq14"], rec["freq30"],
                           market["sample_count_14d"],
                           market["sample_count_30d"]), abs=1e-4)


# ================= 7~10：priority 行为 =================

class TestPriorityEffects:
    def _score(self, env, name):
        return env["ss"].compute_skill_score(env["sr"].get_by_name(name))

    def test_high_market_raises_priority(self, conn):
        env = _env(conn)
        base = self._score(env, "Embedding")["score"]
        env["js"].save_summary(D, "Embedding 9", 10)
        env["ss"].refresh_market(D)
        after = self._score(env, "Embedding")["score"]
        assert after > base
        assert after - base == pytest.approx(0.30 * 0.9, abs=1e-6)

    def test_mastered_still_downweighted(self, conn):
        env = _env(conn)
        sr = env["sr"]
        sr.update(sr.get_by_name("Python")["id"], status="mastered")
        sr.update(sr.get_by_name("Embedding")["id"], status="not_started")
        env["js"].save_summary(D, "Python 9\nEmbedding 9", 10)
        env["ss"].refresh_market(D)
        py = self._score(env, "Python")
        emb = self._score(env, "Embedding")
        assert py["market_term"] == pytest.approx(emb["market_term"])
        assert py["active_term"] < emb["active_term"]
        assert py["score"] < emb["score"]

    def test_weak_plus_market_joint_boost(self, conn):
        env = _env(conn)
        sr, arepo = env["sr"], env["arepo"]
        sr.update(sr.get_by_name("Ranking")["id"], status="not_started")
        # 无 mastery vs 薄弱 mastery（weak）
        env["js"].save_summary(D, "Ranking 8", 10)
        env["ss"].refresh_market(D)
        no_evidence = self._score(env, "Ranking")["score"]
        kp = arepo.create_knowledge_point("ranking.core")
        arepo.update_knowledge_point(kp["id"], mastery_estimate=0.3,
                                     last_assessed_at="2026-09-10T10:00:00")
        sr.update(sr.get_by_name("Ranking")["id"], mastery_ref=f"kp:{kp['id']}")
        weak = env["ss"].explain_skill(sr.get_by_name("Ranking"))
        assert weak["weak"] is True
        assert weak["score"] > no_evidence

    def test_explanation_fields(self, conn):
        env = _env(conn)
        env["js"].save_summary(D, "Embedding 9\nRanking 6", 10)
        env["ss"].refresh_market(D)
        e = env["ss"].explain_skill(env["sr"].get_by_name("Embedding"))
        for key in ("market_source", "market_14d", "market_30d", "market_signal",
                    "sample_count_14d", "prerequisite_demand_boost",
                    "stage_alignment", "weak", "blocked", "reasons"):
            assert key in e, key
        assert e["market_source"] == "daily_summary"
        assert e["market_14d"] == pytest.approx(0.9)


# ================= 11~14：前置传播 / 阶段适配 =================

class TestPropagationAndStage:
    def _wire_prereqs(self, env):
        sr = env["sr"]
        sr.update(sr.get_by_name("Ranking")["id"],
                  prerequisites=["推荐系统基础", "Embedding"])
        sr.update(sr.get_by_name("Embedding")["id"], prerequisites=["PyTorch"])
        sr.update(sr.get_by_name("PyTorch")["id"], prerequisites=["Transformer"])
        sr.update(sr.get_by_name("Transformer")["id"], prerequisites=["LLM 基础"])

    def test_blocked_high_demand_propagates_to_prereqs(self, conn):
        env = _env(conn)
        self._wire_prereqs(env)
        env["js"].save_summary(D, "Ranking 7", 10)  # signal 0.7
        env["ss"].refresh_market(D)
        boost = env["ss"]._prereq_boost
        assert boost["推荐系统基础"] == pytest.approx(0.35)
        assert boost["Embedding"] == pytest.approx(0.35)
        # 传播两层：PyTorch 获得更弱影响
        assert boost["PyTorch"] == pytest.approx(0.175)
        # 第三层不再传播
        assert "Transformer" not in boost

    def test_propagation_raises_prereq_market_factor(self, conn):
        env = _env(conn)
        self._wire_prereqs(env)
        env["js"].save_summary(D, "Ranking 7", 10)
        env["ss"].refresh_market(D)
        emb = env["sr"].get_by_name("Embedding")
        assert env["ss"].market_factor(emb) == pytest.approx(0.35)

    def test_propagation_not_infinite_with_deep_chain(self, conn):
        env = _env(conn)
        self._wire_prereqs(env)
        env["js"].save_summary(D, "Ranking 7", 10)
        env["ss"].refresh_market(D)
        # LLM 基础 是 Transformer 的前置（第 4 层）→ 不应获得任何 boost
        assert "LLM 基础" not in env["ss"]._prereq_boost

    def test_stage_alignment_labels(self, conn):
        env = _env(conn)
        assert env["ss"].stage_alignment("Transformer", env["p1"])[0] == "current"
        assert env["ss"].stage_alignment("SQL", env["p1"])[0] == "next"
        assert env["ss"].stage_alignment("Python", env["p1"])[0] == "unknown"

    def test_far_future_skill_not_top_candidate(self, conn):
        env = _env(conn)
        env["ss"].refresh_market(D)
        cands = env["ss"].select_active_candidates(current_phase=env["p1"])
        names = [c["name"] for c in cands]
        assert "Transformer" in names and "SQL" in names
        # 当前阶段相关技能排在很远的 SQL 之前（gate ok 也不越位）
        assert names.index("Transformer") < names.index("SQL")
        assert next(c for c in cands if c["name"] == "Transformer")[
            "stage_alignment"] == "current"
