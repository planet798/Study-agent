"""补齐正式 skill「LLM + Recommendation」测试。

覆盖：career_context 定义、存量库幂等 sync、不覆盖既有状态、topic 映射、
coverage 前/后、coverage≠mastery、无环、gate 阻挡/解锁、当前阶段不提前生成、
MarketSignal 识别、Planner context 包含、市场高频不绕 gate。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.database.assessment_repository import AssessmentRepository
from app.database.jd_summary_repository import JdDailySummaryRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.jd_summary_service import JdSummaryService
from app.services.market_signal import MarketSignal
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

SKILL = "LLM + Recommendation"
TOPIC = "LLM + Recommendation 基础"
TODAY = "2026-09-14"


def _env(conn, sync=True):
    repo = TaskRepository(conn)
    prepo = StudyPlanRepository(conn)
    arepo = AssessmentRepository(conn)
    sr = SkillRepository(conn)
    js = JdSummaryService(JdDailySummaryRepository(conn), sr)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo,
                      market_signal=MarketSignal(js))
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo,
                           skill_service=ss)
    sps.ensure_default_plan()
    if sync:
        ss.sync_skills_from_career_context()
        ss.sync_skill_topic_links()
    return {"conn": conn, "repo": repo, "prepo": prepo, "arepo": arepo,
            "sr": sr, "js": js, "ss": ss, "sps": sps}


def _topic(env, name):
    plan = env["prepo"].get_active_plan()
    for p in env["prepo"].list_phases(plan.id):
        for t in env["prepo"].list_topics(p.id):
            if t.name == name:
                return t
    return None


def _done(env, name):
    t = _topic(env, name)
    task = env["repo"].create(title=name, scheduled_date="2027-05-01",
                              source="generated", topic_id=t.id)
    env["repo"].mark_done(task.id)


class TestSkillPool:
    def test_career_context_defines_skill(self):
        d = json.loads(Path("docs/career_context.json").read_text("utf-8"))
        assert SKILL in d["skill_pool"]["S"]
        assert d["skill_dependencies"][SKILL] == [
            "推荐系统基础", "LLM 基础", "Ranking", "Rerank", "RAG"]

    def test_existing_db_gets_skill(self, conn):
        env = _env(conn, sync=False)
        env["sr"].upsert_by_name("Python")
        before = len(env["sr"].list_all())
        res = env["ss"].sync_skills_from_career_context()
        assert SKILL in res["added"]
        assert res["total"] == before + len(res["added"])
        assert env["sr"].get_by_name(SKILL) is not None

    def test_sync_idempotent(self, conn):
        env = _env(conn)
        assert env["ss"].sync_skills_from_career_context()["added"] == []
        before = len(env["sr"].list_all())
        assert len(env["sr"].list_all()) == before

    def test_does_not_overwrite_existing(self, conn):
        env = _env(conn, sync=False)
        env["sr"].upsert_by_name("Python", status="mastered")
        env["sr"].update(env["sr"].get_by_name("Python")["id"],
                         mastery_ref="kp:1",
                         jd_frequency={"must": 5, "plus": 1, "total_jds": 9},
                         priority_score=1.234)
        before = env["sr"].get_by_name("Python")
        env["ss"].sync_skills_from_career_context()
        after = env["sr"].get_by_name("Python")
        assert after["status"] == before["status"] == "mastered"
        assert after["mastery_ref"] == "kp:1"
        assert after["jd_frequency"] == before["jd_frequency"]
        assert after["priority_score"] == 1.234
        assert env["arepo"].list_attempts() == []


class TestMappingAndGate:
    def test_topic_mapping(self, conn):
        env = _env(conn)
        s = env["sr"].get_by_name(SKILL)
        assert s["linked_topics"] == [_topic(env, TOPIC).id]

    def test_status_initial_not_started(self, conn):
        env = _env(conn)
        assert env["sr"].get_by_name(SKILL)["status"] == "not_started"

    def test_blocked_by_prerequisites(self, conn):
        env = _env(conn)
        s = env["sr"].get_by_name(SKILL)
        assert env["ss"].is_blocked(s) is True
        missing = env["ss"].missing_prerequisites(s)
        assert "推荐系统基础" in missing and "Ranking" in missing
        assert "Rerank" in missing and "RAG" in missing

    def test_unblocked_when_prereqs_covered(self, conn):
        env = _env(conn)
        for n in ("推荐系统基础", "Ranking", "Rerank", "RAG", "LLM 基础"):
            env["sr"].update(env["sr"].get_by_name(n)["id"], status="mastered")
        assert env["ss"].is_blocked(env["sr"].get_by_name(SKILL)) is False

    def test_dependency_graph_acyclic(self, conn):
        env = _env(conn)
        skills = {s["name"]: s for s in env["sr"].list_all()}
        color = {n: 0 for n in skills}
        cycles = []

        def dfs(n, stack):
            color[n] = 1
            stack.append(n)
            for p in (skills[n]["prerequisites"] or []):
                if p not in skills:
                    continue
                if color[p] == 1:
                    cycles.append(stack[stack.index(p):] + [p])
                elif color[p] == 0:
                    dfs(p, stack)
            stack.pop()
            color[n] = 2

        for n in skills:
            if color[n] == 0:
                dfs(n, [])
        assert cycles == []


class TestCoverage:
    def test_coverage_false_before(self, conn):
        env = _env(conn)
        assert SKILL not in env["ss"].refresh_coverage()

    def test_coverage_true_after_topic_done(self, conn):
        env = _env(conn)
        _done(env, TOPIC)
        assert SKILL in env["ss"].refresh_coverage()

    def test_coverage_not_mastery(self, conn):
        env = _env(conn)
        _done(env, TOPIC)
        env["ss"].refresh_coverage()
        s = env["sr"].get_by_name(SKILL)
        assert s["status"] == "not_started"
        assert env["ss"]._mastery_for_skill(s) is None
        assert env["arepo"].list_attempts() == []


class TestMarketAndPlanner:
    def test_market_signal_recognizes_skill(self, conn):
        env = _env(conn)
        p = env["js"].preview_summary(f"{SKILL} 6\nPython 4", 20, "internship")
        names = {r["name"] for r in p["matched"]}
        assert SKILL in names
        env["js"].save_summary("2027-05-01", f"{SKILL} 6\nPython 4", 20,
                               "internship")
        market = MarketSignal(env["js"]).compute("2027-05-01")
        assert market["skills"][SKILL]["freq14"] == 0.3

    def test_market_high_does_not_bypass_gate(self, conn):
        env = _env(conn)
        env["js"].save_summary("2027-05-01", f"{SKILL} 20", 20, "internship")
        env["ss"].refresh_market("2027-05-01")
        s = env["sr"].get_by_name(SKILL)
        assert env["ss"].is_blocked(s) is True  # 高频仍 blocked

    def test_planner_context_includes_gap(self, conn):
        env = _env(conn)
        env["js"].save_summary("2027-05-01", f"{SKILL} 20", 20, "internship")
        dps = DailyPlannerService(
            env["repo"], env["prepo"], planner=None,
            study_plan_service=env["sps"], assessment_repo=env["arepo"],
            skill_service=env["ss"], jd_service=None)
        ctx = dps.build_context("2027-04-30")  # plan_next_date = 2027-05-01
        gaps = {g.skill: g for g in ctx.jd_gap_skills}
        assert SKILL in gaps
        assert gaps[SKILL].blocked is True
        assert gaps[SKILL].market_14d == 1.0

    def test_current_phase_does_not_generate_it(self, conn):
        env = _env(conn)
        # 复刻真实库：阶段二全部完成
        plan = env["prepo"].get_active_plan()
        ph2 = next(p for p in env["prepo"].list_phases(plan.id)
                   if p.name == "阶段二：深度学习与 LLM 基础")
        for t in env["prepo"].list_topics(ph2.id):
            _done(env, t.name)
        assert env["sps"].get_current_phase(TODAY).name == "阶段三：LLM 应用"
        res = env["sps"].generate_daily_tasks(TODAY)
        assert _topic(env, TOPIC).id not in res["selected"]
