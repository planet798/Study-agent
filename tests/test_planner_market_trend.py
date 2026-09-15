"""Step 6：市场趋势 → Planner（PlanningContext / prompt / fallback）测试。

覆盖：上下文包含市场趋势、prompt 说明“目标岗位样本不代表全市场”、
blocked 高频不生成、前置获得传播、AI 不可用/非法→fallback 仍用市场、
不跳阶段、daily_limit、无 summary 兼容旧 individual JD、不删除历史 JD /
不修改 career_context。
"""

from __future__ import annotations

from app.ai.planner_context import PlanningContext
from app.ai.prompts import build_planner_user_prompt
from app.ai.interface import AIServiceError
from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.database.study_plan_repository import (
    PlannerDecisionRepository,
    StudyPlanRepository,
)
from app.services.daily_planner_service import DailyPlannerService
from app.services.jd_service import JdService
from app.services.jd_summary_service import JdSummaryService
from app.services.market_signal import MarketSignal
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-09-10"
NEXT = "2026-09-11"


def _env(conn, with_summary_service=True):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    prepo = StudyPlanRepository(conn)
    plan = prepo.create_plan(name="p", start_date="2026-09-01",
                             end_date="2026-12-31")
    phase = prepo.create_phase(plan_id=plan.id, name="阶段一",
                               start_date="2026-09-01",
                               end_date="2026-09-30")
    # 主题：越靠后 order 越大（默认排序对 Embedding 不利，靠市场翻盘）
    t_tr = prepo.create_topic(phase_id=phase.id, name="Transformer 主题",
                              estimated_minutes=45, priority=1, order_index=0)
    t_emb = prepo.create_topic(phase_id=phase.id, name="Embedding 主题",
                               estimated_minutes=45, priority=1, order_index=1)
    t_rag = prepo.create_topic(phase_id=phase.id, name="RAG 主题",
                               estimated_minutes=45, priority=1, order_index=2)
    sr = SkillRepository(conn)
    for n in ("Python", "PyTorch", "Transformer", "Embedding", "Recall",
              "Ranking", "RAG", "LLM 基础", "推荐系统基础"):
        sr.upsert_by_name(n)
    sr.update(sr.get_by_name("Transformer")["id"], status="learning")
    sr.update(sr.get_by_name("Embedding")["id"], status="not_started")
    sr.update(sr.get_by_name("RAG")["id"], status="not_started",
              prerequisites=["LLM 基础", "Embedding"])
    js = (JdSummaryService(JdDailySummaryRepository(conn), sr)
          if with_summary_service else None)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo,
                      market_signal=MarketSignal(js) if js else None)
    ss.link_topic_by_name("Transformer", "Transformer 主题")
    ss.link_topic_by_name("Embedding", "Embedding 主题")
    ss.link_topic_by_name("RAG", "RAG 主题")
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo,
                           skill_service=ss)
    jd = JdService(JdRepository(conn), sr, ss)
    return {"conn": conn, "repo": repo, "arepo": arepo, "prepo": prepo,
            "sr": sr, "ss": ss, "js": js, "sps": sps, "jd": jd,
            "t_tr": t_tr, "t_emb": t_emb, "t_rag": t_rag}


class _FakePlanner:
    def __init__(self, plan=None, configured=True):
        self._plan = plan
        self._configured = configured

    def is_configured(self):
        return self._configured

    def plan_next_day(self, context):
        if self._plan is None:
            raise AIServiceError("boom")
        return self._plan


def _dps(env, planner=None):
    return DailyPlannerService(
        env["repo"], env["prepo"], planner=planner,
        study_plan_service=env["sps"], assessment_repo=env["arepo"],
        skill_service=env["ss"], jd_service=env["jd"],
    )


# ================= 1~2、15：PlanningContext 市场字段 =================

class TestPlanningContextMarket:
    def test_context_contains_market_trends(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9\nRanking 6", 10)
        ctx = _dps(env).build_context(TODAY)
        assert ctx.market_source == "daily_summary"
        assert ctx.market_sample_count_30d == 10
        names = {m.skill: m for m in ctx.market_trends}
        assert "Embedding" in names
        assert names["Embedding"].market_30d == 0.9

    def test_no_summary_market_source_none(self, conn):
        env = _env(conn)
        ctx = _dps(env).build_context(TODAY)
        assert ctx.market_source == "none"
        assert ctx.market_trends == []

    def test_skill_priority_has_market_fields(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        ctx = _dps(env).build_context(TODAY)
        emb = next((p for p in ctx.skill_priorities if p.skill == "Embedding"),
                   None)
        assert emb is not None
        assert emb.market_30d == 0.9
        assert emb.market_source == "daily_summary"
        assert emb.stage_alignment == "current"


# ================= 16~18：prompt =================

class TestPrompt:
    def test_prompt_contains_market_section(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9\nRecall 8", 10)
        ctx = _dps(env).build_context(TODAY)
        text = build_planner_user_prompt(ctx)
        assert "近30天目标岗位技术趋势" in text
        assert "Embedding" in text
        assert "近 30 天样本：10" in text

    def test_prompt_marks_sample_not_whole_market(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        ctx = _dps(env).build_context(TODAY)
        text = build_planner_user_prompt(ctx)
        assert "不代表全行业需求" in text

    def test_prompt_no_market_when_no_summary(self, conn):
        env = _env(conn)
        ctx = _dps(env).build_context(TODAY)
        assert "目标岗位技术趋势" not in build_planner_user_prompt(ctx)


# ================= 10~13：fallback 使用市场 / gate =================

class TestFallbackMarket:
    def test_high_market_reorders_fallback(self, conn):
        env = _env(conn)
        # 无市场：默认 Transformer 在前（order 0）
        res0 = env["sps"].generate_daily_tasks(NEXT)
        assert res0["selected"][0] == env["t_tr"].id
        # 清理当天任务，加入 Embedding 高市场
        for t in env["repo"].list_by_date(NEXT):
            env["repo"].conn.execute("DELETE FROM tasks WHERE id=?", (t.id,))
        env["repo"].conn.commit()
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        res1 = env["sps"].generate_daily_tasks(NEXT)
        assert res1["selected"][0] == env["t_emb"].id

    def test_blocked_high_demand_not_generated_and_prereq_boosted(self, conn):
        env = _env(conn)
        # RAG 高需求但缺 LLM 基础 / Embedding
        env["js"].save_summary(NEXT, "RAG 9\nEmbedding 3", 10)
        env["ss"].refresh_market(NEXT)
        res = env["sps"].generate_daily_tasks(NEXT)
        assert env["t_rag"].id not in res["selected"]
        assert env["t_rag"].id in res["skipped_gate"]
        # 前置 Embedding 获得市场 + 传播需求
        emb = env["sr"].get_by_name("Embedding")
        assert env["ss"].market_factor(emb) > 0.3

    def test_ai_unavailable_fallback_uses_market(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        dps = _dps(env, planner=_FakePlanner(configured=False))
        out = dps.generate_next_day_plan(TODAY)
        assert out["fallback"] is True
        assert out["fallback_reason"] == "ai_not_configured"
        created = env["repo"].list_by_date(NEXT)
        assert any(t.topic_id == env["t_emb"].id for t in created)

    def test_ai_error_fallback(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        dps = _dps(env, planner=_FakePlanner(plan=None))
        out = dps.generate_next_day_plan(TODAY)
        assert out["fallback"] and out["fallback_reason"] == "ai_error"

    def test_ai_invalid_topic_falls_back(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        plan = DailyPlan(
            reasoning="r",
            recommended_tasks=(RecommendedTask(
                topic_id=99999, title="非法主题", description="x",
                estimated_minutes=30, priority=1),),
            carry_over_tasks=(),
            daily_minutes=30,
            adjustment="a",
        )
        dps = _dps(env, planner=_FakePlanner(plan=plan))
        out = dps.generate_next_day_plan(TODAY)
        assert out["fallback"] is True
        assert out["fallback_reason"] == "validation_failed"

    def test_planner_does_not_jump_phase(self, conn):
        env = _env(conn)
        env["js"].save_summary(NEXT, "RAG 9", 10)
        dps = _dps(env, planner=_FakePlanner(configured=False))
        dps.generate_next_day_plan(TODAY)
        phase_topic_ids = {t.id for t in env["sps"].get_current_phase(NEXT).topics}
        for t in env["repo"].list_by_date(NEXT):
            assert t.topic_id in phase_topic_ids

    def test_daily_limit_respected(self, conn):
        env = _env(conn)
        dps = _dps(env, planner=_FakePlanner(configured=False))
        dps.generate_next_day_plan(TODAY)
        total = sum(t.estimated_minutes
                    for t in env["repo"].list_by_date(NEXT))
        assert total <= dps.max_daily_minutes


# ================= 26~30：边界不变量 =================

class TestBoundaries:
    def test_no_summary_individual_jd_fallback_still_works(self, conn):
        env = _env(conn)
        # 只有 individual JD，没有 daily summary
        env["jd"].add_jd("实习：熟悉 Embedding 与 PyTorch")
        env["ss"].refresh_market(NEXT)
        assert env["ss"].market()["source"] == "none"
        emb = env["sr"].get_by_name("Embedding")
        assert env["ss"].market_factor(emb) > 0.0  # 用回退的 individual JD

    def test_old_individual_jd_not_deleted(self, conn):
        env = _env(conn)
        env["jd"].add_jd("实习：熟悉 PyTorch")
        before = len(JdRepository(conn).list_all())
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        env["ss"].refresh_market(NEXT)
        assert len(JdRepository(conn).list_all()) == before

    def test_career_context_file_unchanged(self, conn):
        from pathlib import Path
        p = Path("docs/career_context.json")
        before = p.read_text(encoding="utf-8")
        env = _env(conn)
        env["js"].save_summary(NEXT, "Embedding 9", 10)
        env["ss"].refresh_market(NEXT)
        env["sps"].generate_daily_tasks(NEXT)
        assert p.read_text(encoding="utf-8") == before

    def test_save_summary_does_not_touch_active_tasks(self, conn):
        env = _env(conn)
        t = env["repo"].create(title="今日任务", scheduled_date=TODAY,
                               source="generated", task_type="new")
        env["js"].save_summary(TODAY, "Embedding 9", 10)
        assert env["repo"].get(t.id).status == "active"
        assert env["repo"].list_by_date(TODAY)[0].title == "今日任务"

    def test_mastered_high_market_not_repeated_via_evidence(self, conn):
        """已掌握（高掌握 + 最近 good）不重复：Phase 8 证据仍然有效。"""
        env = _env(conn)
        sr, arepo = env["sr"], env["arepo"]
        sr.update(sr.get_by_name("Transformer")["id"], status="mastered")
        kp = arepo.create_knowledge_point(
            "transformer.core", topic_id=env["t_tr"].id)
        arepo.update_knowledge_point(
            kp["id"], mastery_estimate=0.95,
            last_assessed_at="2026-09-08T10:00:00")
        at = arepo.create_attempt(kp["id"], "[]")
        arepo.update_attempt(at["id"], judge_status="judged",
                             result_level="good")
        from app.services.knowledge_evidence import skip_topic_ids
        assert env["t_tr"].id in skip_topic_ids(env["repo"], arepo)
        env["js"].save_summary(NEXT, "Transformer 9", 10)
        res = env["sps"].generate_daily_tasks(NEXT)
        assert env["t_tr"].id not in res["selected"]
        assert env["t_tr"].id in res["skipped_done"]
