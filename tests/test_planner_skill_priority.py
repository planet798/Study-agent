"""Phase C（规则层）：JD/Skill 优先级进入 generate_daily_tasks 与 build_context。

覆盖（与清单映射）：
1 无 JD 行为兼容
2 有 JD 时 PlanningContext 获得 skill_priorities
3 jd_gap_skills 正确生成
4 JD must 比 plus 权重高
5 weak point + JD must 联合提权
6 mastered 技能不重复
7 blocked 技能不被生成
8 JD 高优先级不能跳 phase
10 future 7 day 预览不建任务
16 daily_limit 不被突破
18 无 assessment evidence 不误判 mastery
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.jd_service import JdService
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

PHASE_START = "2026-09-01"
TODAY = "2026-09-08"


class _FakePlanner:
    def is_configured(self):
        return False


def _build_env(conn, plan_repo, with_jd=False, max_minutes=180):
    """mini 计划 + 技能 + 主题映射。

    主题：
      T1 = PyTorch……（S/learning，无前置）order0
      T2 = Transformer……（S/learning，无前置）order1
      T3 = RAG 全流程搭建（S/not_started，前置 Embedding+LLM 基础 → blocked）order2
      T4 = Ranking 排序（S/not_started，前置 推荐系统基础+Embedding → blocked）order3
    """
    plan = plan_repo.create_plan(
        name="mini", start_date=PHASE_START, end_date="2026-12-31",
        description="mini",
    )
    phase = plan_repo.create_phase(
        plan_id=plan.id, name="mini-phase", start_date=PHASE_START,
        end_date="2026-12-31", goals="g",
    )
    t1 = plan_repo.create_topic(
        phase_id=phase.id, name="PyTorch 张量与自动求导（Tensor / autograd）",
        estimated_minutes=30, priority=3, order_index=0,
    )
    t2 = plan_repo.create_topic(
        phase_id=phase.id, name="Transformer：Attention / MHA / FFN",
        estimated_minutes=30, priority=3, order_index=1,
    )
    t3 = plan_repo.create_topic(
        phase_id=phase.id, name="RAG 全流程搭建",
        estimated_minutes=30, priority=2, order_index=2,
    )
    t4 = plan_repo.create_topic(
        phase_id=phase.id, name="Ranking 排序",
        estimated_minutes=30, priority=2, order_index=3,
    )

    skill_repo = SkillRepository(conn)
    skill_repo.create(name="PyTorch", tier="S", status="learning")
    skill_repo.create(name="Transformer", tier="S", status="learning")
    skill_repo.create(name="RAG", tier="S", status="not_started",
                      prerequisites=["Embedding", "LLM 基础"])
    skill_repo.create(name="Ranking", tier="S", status="not_started",
                      prerequisites=["推荐系统基础", "Embedding"])
    skill_repo.create(name="Embedding", tier="S", status="not_started",
                      shared_connector=True)
    skill_repo.create(name="LLM 基础", tier="S", status="learning")
    skill_repo.create(name="推荐系统基础", tier="S", status="not_started")
    skill_repo.create(name="Python", tier="S", status="mastered")

    def _link(skill_name, topic_id):
        skill_repo.update(
            skill_repo.get_by_name(skill_name)["id"], linked_topics=[topic_id]
        )

    _link("PyTorch", t1.id)
    _link("Transformer", t2.id)
    _link("RAG", t3.id)
    _link("Ranking", t4.id)
    # 注意：Python(已掌握) 不默认绑定到 T1，避免干扰无 JD 基线；
    # 需要“已掌握不重复”的测试会在此处单独绑定。

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
        jd_service = JdService(
            JdRepository(conn), skill_repo, skill_service
        )

    return {
        "conn": conn, "plan_repo": plan_repo, "sps": sps,
        "skill_repo": skill_repo, "assessment_repo": assessment_repo,
        "skill_service": skill_service, "jd_service": jd_service,
        "phase": phase, "t1": t1, "t2": t2, "t3": t3, "t4": t4,
    }


def _gen(env, dt):
    return env["sps"].generate_daily_tasks(dt)


JD_TEXT = "熟悉 Transformer、PyTorch 与 RAG、Ranking 排序；有 Transformer 经验者优先"


class TestNoJdCompatibility:
    def test_context_without_skill_service_is_empty(self, conn, plan_repo):
        # 不注入 skill_service/jd_service：字段全部为空，行为与旧版一致
        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=_FakePlanner(),
        )
        ctx = dp.build_context("2026-09-07")
        assert ctx.skill_priorities == []
        assert ctx.jd_gap_skills == []
        assert ctx.prerequisite_blocked == []
        assert ctx.weekly_focus == []

    def test_no_jd_generation_works_and_gate_still_guards(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=False, max_minutes=180)
        res = _gen(env, TODAY)
        ids = {t.topic_id for t in res["generated"]}
        assert env["t1"].id in ids
        assert env["t2"].id in ids
        # 前置未满足的 RAG / Ranking 不被生成（即使没有任何 JD 也如此）
        assert env["t3"].id not in ids
        assert env["t4"].id in res["skipped_gate"]


class TestContextFields:
    def test_context_gets_skill_priorities_with_jd(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True)
        env["jd_service"].add_jd(JD_TEXT)
        from app.database.repository import TaskRepository

        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=_FakePlanner(),
            study_plan_service=env["sps"],
            assessment_repo=env["assessment_repo"],
            skill_service=env["skill_service"],
            jd_service=env["jd_service"],
        )
        ctx = dp.build_context("2026-09-07")
        names = {s.skill for s in ctx.skill_priorities}
        assert "Transformer" in names
        assert all(s.reason for s in ctx.skill_priorities)
        assert len(ctx.weekly_focus) == 7

    def test_jd_gap_skills_correct(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True)
        env["jd_service"].add_jd(JD_TEXT)
        from app.database.repository import TaskRepository

        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=_FakePlanner(),
            study_plan_service=env["sps"],
            assessment_repo=env["assessment_repo"],
            skill_service=env["skill_service"],
            jd_service=env["jd_service"],
        )
        ctx = dp.build_context("2026-09-07")
        gap = {g.skill: g for g in ctx.jd_gap_skills}
        assert "Transformer" in gap
        assert gap["Transformer"].jd_must_count >= 1
        # mastered 的 Python 不进入缺口
        assert "Python" not in gap
        # RAG 未满足前置 → blocked 标记
        rag = gap.get("RAG")
        if rag is not None:
            assert rag.blocked is True

    def test_no_mastery_no_false_inference(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True)
        env["jd_service"].add_jd(JD_TEXT)
        from app.database.repository import TaskRepository

        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=_FakePlanner(),
            study_plan_service=env["sps"],
            assessment_repo=env["assessment_repo"],
            skill_service=env["skill_service"],
            jd_service=env["jd_service"],
        )
        ctx = dp.build_context("2026-09-07")
        assert ctx.knowledge_evidence == []  # 无验收 → 不出现
        for g in ctx.jd_gap_skills:
            assert not any(g.mastery is not None and g.mastery < 0.5
                           for _ in [g])  # mastery 为 None（未验收）而非误判“不会”


class TestOrdering:
    def test_jd_must_beats_plus_same_tier(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True, max_minutes=30)
        # JD：Transformer 为 must，PyTorch 为 plus
        jd_service = env["jd_service"]
        jd_service.add_jd(
            "熟悉 Transformer，掌握其 Self-Attention；有 PyTorch 使用经验优先"
        )
        res = _gen(env, TODAY)
        assert res["generated"][0].topic_id == env["t2"].id  # Transformer

    def test_weak_point_plus_jd_must_combined(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True, max_minutes=30)
        jd_service = env["jd_service"]
        jd_service.add_jd("熟悉 PyTorch 与 Transformer，两者均为核心要求")
        # 给 T2(Transformer) 关联一个弱知识点（weak_points）
        kp = env["assessment_repo"].create_knowledge_point(
            "transformer.weak", topic_id=env["t2"].id
        )
        env["assessment_repo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.35,
            last_assessed_at="2026-09-08T10:00:00",
        )
        res = _gen(env, TODAY)
        # JD must 两者同频；T2 还有 weak point → 联合提权 → T2 优先
        assert res["generated"][0].topic_id == env["t2"].id

    def test_mastered_evidence_not_repeated_despite_jd(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True, max_minutes=180)
        env["jd_service"].add_jd("要求熟悉 PyTorch、Transformer")
        # 给 T1(PyTorch)关联高掌握+最近验收良好 → 不因 JD 高频重复基础任务
        kp = env["assessment_repo"].create_knowledge_point(
            "pytorch.mastered", topic_id=env["t1"].id,
        )
        env["assessment_repo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.9, review_count=2,
            last_assessed_at="2026-09-19T10:00:00",
        )
        att = env["assessment_repo"].create_attempt(
            kp["id"], "[]", task_id=None,
        )
        env["assessment_repo"].update_attempt(
            att["id"], judge_status="judged", result_level="excellent",
            mastery_estimate=0.9,
        )
        res = _gen(env, TODAY)
        generated_ids = {t.topic_id for t in res["generated"]}
        assert env["t1"].id not in generated_ids

    def test_plain_mastered_status_does_not_block_phase(self, conn, plan_repo):
        """career_context 静态 seed 的 mastered（无验收证据）不应卡住当前阶段。"""
        env = _build_env(conn, plan_repo, with_jd=True, max_minutes=30)
        # Python(静态 mastered，无证据) 绑定 T1：不应导致 T1 永久不可生成
        env["skill_repo"].update(
            env["skill_repo"].get_by_name("Python")["id"],
            linked_topics=[env["t1"].id],
        )
        res = _gen(env, TODAY)
        assert env["t1"].id in {t.topic_id for t in res["generated"]}

    def test_blocked_skill_not_generated_even_with_high_jd(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True, max_minutes=180)
        env["jd_service"].add_jd("优先招聘熟悉 RAG 与 Ranking 的候选人")
        res = _gen(env, TODAY)
        ids = {t.topic_id for t in res["generated"]}
        assert env["t3"].id not in ids  # RAG（blocked）
        assert env["t4"].id not in ids  # Ranking（blocked）
        assert env["t3"].id in res["skipped_gate"]

    def test_jd_high_priority_does_not_jump_phase(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True)
        sps = env["sps"]
        assert sps.get_current_phase(TODAY).name == "mini-phase"
        env["jd_service"].add_jd("需要 Agent、vLLM、Docker 等高级技能高级技能")
        # 阶段仍由日期锚定，不被 JD 改变
        assert sps.get_current_phase(TODAY).name == "mini-phase"
        res = _gen(env, TODAY)
        assert all(t.topic_id in {env["t1"].id, env["t2"].id,
                                  env["t3"].id, env["t4"].id}
                   for t in res["generated"])

    def test_daily_limit_not_exceeded(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=False, max_minutes=30)
        res = _gen(env, TODAY)
        total = sum(t.estimated_minutes for t in res["generated"])
        assert total <= 30
        assert len(res["generated"]) <= 1


class TestWeeklyPreviewNoWrite:
    def test_weekly_focus_no_tasks_created(self, conn, plan_repo):
        env = _build_env(conn, plan_repo, with_jd=True)
        env["jd_service"].add_jd(JD_TEXT)
        from app.database.repository import TaskRepository

        repo = TaskRepository(conn)
        before = len(repo.list_by_date("2026-09-09"))
        env["jd_service"].preview_weekly_priorities(TODAY, days=7)
        env["skill_service"].select_active_candidates()
        assert len(repo.list_by_date("2026-09-09")) == before
