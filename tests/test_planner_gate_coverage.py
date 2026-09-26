"""每日规划 hard gate 回归测试。

根因：当前 phase 的剩余主题全部被前置门禁阻塞（前置技能只有 done 任务、
从无验收证据）。修复：前置“材料已覆盖”（linked_topics 全部有 done 任务）
可作为门禁解锁条件，但**绝不等同掌握**（不写 mastery / 不影响 Review）。
"""

from __future__ import annotations


from app.ai.interface import AIServiceError
from app.ai.planner_context import PlanningContext
from app.ai.schemas import DailyPlan, RecommendedTask, parse_daily_plan
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import (
    PlannerDecisionRepository,
    StudyPlanRepository,
)
from app.services.daily_planner_service import DailyPlannerService
from app.services.jd_summary_service import JdSummaryService
from app.services.market_signal import MarketSignal
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.utils.date_utils import add_days

TODAY = "2026-09-14"


def _env(conn, phase_a_done=True):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    prepo = StudyPlanRepository(conn)
    plan = prepo.create_plan(name="p", start_date="2026-09-01",
                             end_date="2026-12-31")
    ph_a = prepo.create_phase(plan_id=plan.id, name="阶段A",
                              start_date="2026-09-01",
                              end_date="2026-09-30")
    ph_b = prepo.create_phase(plan_id=plan.id, name="阶段B",
                              start_date="2026-10-01",
                              end_date="2026-12-31")
    t_a = prepo.create_topic(phase_id=ph_a.id, name="Pre 主题")
    t_b = prepo.create_topic(phase_id=ph_b.id, name="Post 主题")
    sr = SkillRepository(conn)
    sr.upsert_by_name("Pre")
    sr.upsert_by_name("Post")
    sr.update(sr.get_by_name("Post")["id"], prerequisites=["Pre"])
    js = JdSummaryService(JdDailySummaryRepository(conn), sr)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo,
                      market_signal=MarketSignal(js))
    ss.link_topic_by_name("Pre", "Pre 主题")
    ss.link_topic_by_name("Post", "Post 主题")
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo,
                           skill_service=ss)
    if phase_a_done:
        done = repo.create(title="Pre 完成", scheduled_date="2026-09-10",
                           source="generated", topic_id=t_a.id)
        repo.mark_done(done.id)
    return {"conn": conn, "repo": repo, "arepo": arepo, "prepo": prepo,
            "sr": sr, "ss": ss, "js": js, "sps": sps, "ph_a": ph_a,
            "ph_b": ph_b, "t_a": t_a, "t_b": t_b}


class _Planner:
    def __init__(self, plan=None, configured=True):
        self._plan, self._configured = plan, configured

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
        skill_service=env["ss"],
    )


# ================= 1~4、10~12：生成 / 幂等 / replan =================

class TestGeneration:
    def test_no_task_with_available_topic_generates(self, conn):
        env = _env(conn)
        res = env["sps"].generate_daily_tasks(TODAY)
        assert len(res["generated"]) == 1
        assert res["selected"] == [env["t_b"].id]

    def test_done_history_not_deleted(self, conn):
        env = _env(conn)
        before = env["repo"].list_by_date("2026-09-10")
        env["sps"].generate_daily_tasks(TODAY)
        assert env["repo"].list_by_date("2026-09-10") == before

    def test_existing_active_same_topic_not_duplicated(self, conn):
        env = _env(conn)
        env["sps"].generate_daily_tasks(TODAY)
        res2 = env["sps"].generate_daily_tasks(TODAY)
        assert res2["generated"] == []
        assert env["t_b"].id in res2["skipped_duplicate"]

    def test_no_duplicate_tasks_after_repeat(self, conn):
        env = _env(conn)
        for _ in range(3):
            env["sps"].generate_daily_tasks(TODAY)
        assert len(env["repo"].list_by_date(TODAY)) == 1

    def test_all_completed_phase_not_regenerated(self, conn):
        env = _env(conn)
        done = env["repo"].create(title="Post 完成", scheduled_date="2026-09-11",
                                  source="generated", topic_id=env["t_b"].id)
        env["repo"].mark_done(done.id)
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["generated"] == []  # 不重复生成已学完主题

    def test_replan_bypasses_empty_decision(self, conn):
        env = _env(conn)
        repo = PlannerDecisionRepository(conn)
        # 今天已有一个“空”决策（accepted_tasks=[]），但当天没有任务
        repo.create(date=TODAY, input_context="{}", ai_response="{}",
                    accepted_tasks="[]", current_phase_id=env["ph_b"].id,
                    source="fallback_rule")
        dps = _dps(env, planner=_Planner(configured=False))
        out = dps.generate_next_day_plan(add_days(TODAY, -1))  # plan_date=TODAY
        assert out["created"]  # 不被空 decision 永久锁死

    def test_ai_error_falls_back_to_legal_topic(self, conn):
        env = _env(conn)
        dps = _dps(env, planner=_Planner(plan=None))
        out = dps.generate_next_day_plan(add_days(TODAY, -1))
        assert out["fallback"] and out["fallback_reason"] == "ai_error"
        assert out["created"]

    def test_ai_empty_output_rejected_then_fallback_generates(self, conn):
        env = _env(conn)
        # 空 recommended_tasks 在解析层就被拒绝（AIServiceError → fallback）
        import pytest as _pytest
        with _pytest.raises(AIServiceError):
            parse_daily_plan({"reasoning": "r", "recommended_tasks": [],
                              "carry_over_tasks": [], "daily_minutes": 30,
                              "adjustment": "a"})
        out = _dps(env, planner=_Planner(plan=None)).generate_next_day_plan(
            add_days(TODAY, -1))
        assert out["created"]


# ================= 5~9：门禁与市场 =================

class TestGateCoverage:
    def test_prereq_material_completed_unlocks_downstream(self, conn):
        env = _env(conn, phase_a_done=True)
        post = env["sr"].get_by_name("Post")
        assert env["ss"].is_blocked(post) is False
        assert env["ss"].refresh_coverage() >= {"Pre"}

    def test_prereq_material_not_completed_still_blocked(self, conn):
        env = _env(conn, phase_a_done=False)
        post = env["sr"].get_by_name("Post")
        assert env["ss"].is_blocked(post) is True
        res = env["sps"].generate_daily_tasks(TODAY)
        assert env["t_b"].id not in res["selected"]

    def test_coverage_does_not_fake_mastery(self, conn):
        env = _env(conn, phase_a_done=True)
        env["ss"].refresh_coverage()
        pre = env["sr"].get_by_name("Pre")
        detail = env["ss"].compute_skill_score(pre)
        assert detail["mastery_estimate"] is None  # 不伪造掌握
        assert pre["status"] == "not_started"      # 不改状态

    def test_market_factor_zero_does_not_filter(self, conn):
        env = _env(conn)
        # 无任何 JD/市场数据 -> market_factor=0
        env["ss"].refresh_market(TODAY)
        assert env["ss"].market_factor(env["sr"].get_by_name("Post")) == 0.0
        res = env["sps"].generate_daily_tasks(TODAY)
        assert len(res["generated"]) == 1  # 仍生成

    def test_far_stage_skill_does_not_block_current_topic(self, conn):
        env = _env(conn)
        sr = env["sr"]
        sr.upsert_by_name("FarSkill")
        far = sr.get_by_name("FarSkill")
        sr.update(far["id"], status="not_started", linked_topics=[])
        env["ss"].refresh_market(TODAY)
        cands = env["ss"].select_active_candidates(current_phase=env["ph_b"])
        assert any(c["name"] == "Post" for c in cands)
        res = env["sps"].generate_daily_tasks(TODAY)
        assert len(res["generated"]) == 1
