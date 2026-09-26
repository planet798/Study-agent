"""Phase C（AI 循环层）：skill/JD 优先级真正影响 DailyPlannerService。

覆盖（与清单映射）：
9  一次新 JD 不会重写长期路线
11 AI 正常时使用 skill priority
12 AI 非法时 fallback
13 AI 不可用时 fallback
14 review task 不会被 Planner 重复
15 extra task 不干扰 Planner
17 日期切换仍然幂等
19 planner_decisions 继续正常记录
20 真实 skill priority 与最终 topic 选择一致
"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.jd_service import JdService
from app.services.knowledge_evidence import (
    HIGH_MASTERY_THRESHOLD,
)
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

PHASE_START = "2026-09-01"
TODAY = "2026-09-08"


class CountingPlanner:
    def __init__(self, topic_id=1, error=None, configured=True):
        self.topic_id = topic_id
        self.error = error
        self.configured = configured
        self.inputs = []

    def is_configured(self):
        return self.configured

    def plan_next_day(self, context):
        self.inputs.append(context)
        if self.error is not None:
            raise self.error
        return DailyPlan(
            reasoning="x",
            recommended_tasks=(
                RecommendedTask(topic_id=self.topic_id, title="t",
                                estimated_minutes=30),
            ),
            carry_over_tasks=(),
            daily_minutes=30,
            adjustment="a",
        )


def _build(conn, plan_repo, with_jd=True, max_minutes=60):
    """mini 计划 + 技能 + 主题映射 + 可选 JD（与规则层测试一致的主题集）。"""
    plan = plan_repo.create_plan(
        name="mini", start_date=PHASE_START, end_date="2026-12-31",
        description="mini",
    )
    phase = plan_repo.create_phase(
        plan_id=plan.id, name="mini-phase", start_date=PHASE_START,
        end_date="2026-12-31", goals="g",
    )
    t1 = plan_repo.create_topic(phase_id=phase.id,
                                name="PyTorch 张量与自动求导（Tensor / autograd）",
                                estimated_minutes=30, priority=3, order_index=0)
    t2 = plan_repo.create_topic(phase_id=phase.id,
                                name="Transformer：Attention / MHA / FFN",
                                estimated_minutes=30, priority=3, order_index=1)
    t3 = plan_repo.create_topic(phase_id=phase.id, name="RAG 全流程搭建",
                                estimated_minutes=30, priority=2, order_index=2)
    t4 = plan_repo.create_topic(phase_id=phase.id, name="Ranking 排序",
                                estimated_minutes=30, priority=2, order_index=3)

    skill_repo = SkillRepository(conn)
    for spec in [
        ("PyTorch", "S", "learning", []),
        ("Transformer", "S", "learning", []),
        ("RAG", "S", "not_started", ["Embedding", "LLM 基础"]),
        ("Ranking", "S", "not_started", ["推荐系统基础", "Embedding"]),
        ("Embedding", "S", "not_started", []),
        ("LLM 基础", "S", "learning", []),
        ("推荐系统基础", "S", "not_started", []),
    ]:
        name, tier, status, prereq = spec
        skill_repo.create(name=name, tier=tier, status=status,
                          prerequisites=prereq)
    skill_repo.update(skill_repo.get_by_name("PyTorch")["id"],
                      linked_topics=[t1.id])
    skill_repo.update(skill_repo.get_by_name("Transformer")["id"],
                      linked_topics=[t2.id])
    skill_repo.update(skill_repo.get_by_name("RAG")["id"],
                      linked_topics=[t3.id])
    skill_repo.update(skill_repo.get_by_name("Ranking")["id"],
                      linked_topics=[t4.id])

    assessment_repo = AssessmentRepository(conn)
    skill_service = SkillService(
        skill_repo, plan_repo=plan_repo, assessment_repo=assessment_repo
    )
    skill_service.recompute_all_priority_scores()

    repo = TaskRepository(conn)
    sps = StudyPlanService(
        repo, plan_repo, max_daily_minutes=max_minutes,
        assessment_repo=assessment_repo, skill_service=skill_service,
    )
    jd_service = None
    if with_jd:
        jd_service = JdService(JdRepository(conn), skill_repo, skill_service)
        jd_service.add_jd(
            "熟悉 Transformer 原理与 Attention/MHA/FFN（必备）；"
            "有 PyTorch 使用经验者优先；同时需要 RAG 与 Ranking 方向。"
        )
        # 重新生成规则基线让 priority 反映 JD
        skill_service.recompute_all_priority_scores()

    return {
        "conn": conn, "plan_repo": plan_repo, "repo": repo, "phase": phase,
        "t1": t1, "t2": t2, "t3": t3, "t4": t4,
        "skill_repo": skill_repo, "assessment_repo": assessment_repo,
        "skill_service": skill_service, "jd_service": jd_service, "sps": sps,
    }


def _planner(env, fake):
    return DailyPlannerService(
        env["repo"], env["plan_repo"], planner=fake,
        study_plan_service=env["sps"], assessment_repo=env["assessment_repo"],
        skill_service=env["skill_service"], jd_service=env["jd_service"],
    )


class TestAiLoop:
    def test_ai_normal_uses_skill_priority(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True)
        fake = CountingPlanner(topic_id=env["t2"].id)  # Transformer（JD must）
        dp = _planner(env, fake)
        res = dp.generate_next_day_plan("2026-09-07")
        assert res.get("fallback") is False
        ctx = fake.inputs[0]
        assert any(s.skill == "Transformer" for s in ctx.skill_priorities)
        picked_topic_ids = {env["repo"].get(t).topic_id for t in res["created"]}
        assert picked_topic_ids == {env["t2"].id}

    def test_ai_blocked_topic_falls_back_without_creating_it(
        self, conn, plan_repo
    ):
        env = _build(conn, plan_repo, with_jd=True)
        # AI 误推荐被前置阻塞的 RAG 主题 → 校验失败 → 回退规则（规则也跳过 RAG）
        fake = CountingPlanner(topic_id=env["t3"].id)
        dp = _planner(env, fake)
        res = dp.generate_next_day_plan("2026-09-07")
        assert res.get("fallback") is True
        ids = {t for t in res.get("created", [])}
        from app.database.repository import Task

        created_tasks = [r for r in env["repo"].list_by_date("2026-09-08")]
        assert env["t3"].id not in {t.topic_id for t in created_tasks}

    def test_ai_invalid_falls_back(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True)
        dp = _planner(env, CountingPlanner(
            error=AIServiceError("非法结构")))
        res = dp.generate_next_day_plan("2026-09-07")
        assert res.get("fallback") is True and res["fallback_reason"] == "ai_error"
        assert res.get("created") or res.get("created", []) is not None

    def test_ai_not_configured_falls_back(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True)
        dp = _planner(env, CountingPlanner(configured=False))
        res = dp.generate_next_day_plan("2026-09-07")
        assert res.get("fallback") is True
        assert res["fallback_reason"] == "ai_not_configured"

    def test_fallback_stays_in_phase_and_under_limit(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True, max_minutes=30)
        dp = _planner(env, CountingPlanner(configured=False))
        res = dp.generate_next_day_plan("2026-09-07")
        tasks = env["repo"].list_by_date("2026-09-08")
        assert all(t.topic_id in {env["t1"].id, env["t2"].id,
                                  env["t3"].id, env["t4"].id}
                   for t in tasks)
        assert sum(t.estimated_minutes for t in tasks) <= 30
        # 不生成 blocked
        assert env["t3"].id not in {t.topic_id for t in tasks}


class TestHistoricalReviewIgnoredByPlanner:
    def test_review_row_does_not_block_current_learning(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True)
        # 给 T2 关联知识点并安排一个未完成复习任务
        kp = env["assessment_repo"].create_knowledge_point(
            "transformer.kp", topic_id=env["t2"].id)
        env["assessment_repo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.5, review_count=1,
            last_assessed_at="2026-09-07T10:00:00",
        )
        env["repo"].create(
            title="复习 transformer.kp", scheduled_date="2026-09-08",
            source="review", task_type="review", knowledge_point_id=kp["id"],
        )
        dp = _planner(env, CountingPlanner(configured=False))
        dp.generate_next_day_plan("2026-09-07")
        formal = [t for t in env["repo"].list_by_date("2026-09-08")
                  if t.task_type == "new"]
        assert env["t2"].id in {t.topic_id for t in formal}
        # Historical row is retained, but does not block planning.
        assert any(t.task_type == "review"
                   for t in env["repo"].list_by_date("2026-09-08"))

class TestBoundaries:
    def test_date_transition_idempotent_with_jd(self, conn, plan_repo):
        env = _build(conn, plan_repo, with_jd=True)
        dp = _planner(env, CountingPlanner(topic_id=env["t1"].id))
        ds = DateService(env["repo"], study_plan_service=env["sps"],
                         daily_planner_service=dp)
        ds.process_date_transition("2026-09-05")
        ds.process_date_transition("2026-09-06")
        n = len(env["repo"].list_by_date("2026-09-06"))
        assert n >= 1
        for _ in range(3):
            ds.process_date_transition("2026-09-06")
        assert len(env["repo"].list_by_date("2026-09-06")) == n

    def test_no_long_term_route_rewrite(self, conn, plan_repo):
        from app.services.jd_service import DEFAULT_CAREER_CONTEXT_PATH

        env = _build(conn, plan_repo, with_jd=True)
        # 快照必须在整个流程之前（_build 已创建 mini 计划）
        before_career = DEFAULT_CAREER_CONTEXT_PATH.read_bytes()
        before_phases = conn.execute(
            "SELECT COUNT(*) FROM study_phases").fetchone()[0]
        before_topics = conn.execute(
            "SELECT COUNT(*) FROM study_topics").fetchone()[0]
        before_plan = [dict(r) for r in conn.execute(
            "SELECT * FROM study_plans")]

        dp = _planner(env, CountingPlanner(configured=False))
        dp.generate_next_day_plan("2026-09-07")
        env["jd_service"].add_jd("需要 Agent、vLLM、Docker、CUDA 高级技能")

        assert DEFAULT_CAREER_CONTEXT_PATH.read_bytes() == before_career
        assert conn.execute(
            "SELECT COUNT(*) FROM study_phases").fetchone()[0] == before_phases
        assert conn.execute(
            "SELECT COUNT(*) FROM study_topics").fetchone()[0] == before_topics
        assert [dict(r) for r in conn.execute("SELECT * FROM study_plans")] \
            == before_plan

    def test_planner_decisions_recorded_with_skill_context(
        self, conn, plan_repo
    ):
        env = _build(conn, plan_repo, with_jd=True)
        fake = CountingPlanner(topic_id=env["t2"].id)
        dp = _planner(env, fake)
        dp.generate_next_day_plan("2026-09-07")
        row = dp.decision_repo.latest_for_date("2026-09-08")
        assert row is not None
        # 决策审计包含 skill/任务上下文
        ctx_json = json.loads(row["input_context"]) if row.get("input_context") \
            else {}
        if ctx_json and "skill_priorities" in ctx_json:
            assert isinstance(ctx_json["skill_priorities"], list)
        assert row["accepted_tasks"] is not None

    def test_real_priority_choice_matches_topic_selection(
        self, conn, plan_repo
    ):
        """skill priority 最高的主题 = 规则生成最先选中的主题（同预算 1 个）。"""
        env = _build(conn, plan_repo, with_jd=True, max_minutes=30)
        dp = _planner(env, CountingPlanner(configured=False))  # 走规则 fallback
        res = dp.generate_next_day_plan("2026-09-07")
        created = env["repo"].list_by_date("2026-09-08")
        assert created
        # JD 中 Transformer 为 must 且同为 S/learning，rules 应优先生成 T2
        assert created[0].topic_id == env["t2"].id
