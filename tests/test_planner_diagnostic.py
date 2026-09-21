"""v1 stabilization：Planner Diagnostic（只读）测试。"""

from __future__ import annotations

import json

from app.diagnostics.planner_diagnostic import (
    format_planner_diagnostic,
    run_planner_diagnostic,
)
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    current_phase_topics,
    make_requirement_project,
    topic_in_current_phase,
)


def _data_version(conn) -> int:
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


def _counts(conn) -> dict:
    tables = (
        "tasks", "planner_decisions", "capability_evidence",
        "practice_topic_evidence", "practice_projects",
        "practice_topic_requirements", "knowledge_points",
        "assessment_attempts",
    )
    return {
        t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        for t in tables
    }


class TestDiagnosticStructure:
    def test_all_active_routes(self, practice_readiness_env):
        env = practice_readiness_env
        data = run_planner_diagnostic(env.conn, plan_date=DEFAULT_PLAN_DATE)
        assert data["plan_date"] == DEFAULT_PLAN_DATE
        names = {r["route_name"] for r in data["routes"]}
        assert any("R1" in n or "LLM Fundamentals" in n for n in names)
        assert all("archived" not in r or not r["archived"] for r in data["routes"])
        for r in data["routes"]:
            assert set(r) >= {
                "route_id", "route_name", "priority", "planning_enabled",
                "current_phase", "topics", "candidate_topic_ids",
            }

    def test_tier2_normal_route(self, practice_readiness_env):
        env = practice_readiness_env
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE,
            route_key="R1_LLM_FUNDAMENTALS",
        )
        assert len(data["routes"]) == 1
        r = data["routes"][0]
        assert r["current_phase"]
        assert r["topics"]
        assert all(t["tier"] == 2 for t in r["topics"])
        # 无 blocker → candidate = 全部 normal topics
        assert set(r["candidate_topic_ids"]) == {
            t["topic_id"] for t in r["topics"]
        }

    def test_tier0_practice_blocker(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R3_LLM_INFRA"
        )
        r = data["routes"][0]
        vllm = next(
            t for t in r["topics"] if t["topic_id"] == topics[0].id
        )
        assert vllm["tier"] == 0
        assert vllm["tier_label"] == "practice_blocker"
        assert any("Qwen LoRA" in reason for reason in vllm["reasons"])
        # 只把最高 Tier 给 AI
        assert r["candidate_topic_ids"] == [topics[0].id]

    def test_legal_pool_excludes_future_phase(self, practice_readiness_env):
        env = practice_readiness_env
        quant = env.plan_repo.find_topic_by_name_in_route(
            env.r3.id, "Quantization"
        )
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R3_LLM_INFRA"
        )
        r = data["routes"][0]
        assert quant.id not in {t["topic_id"] for t in r["topics"]}

    def test_route_filter(self, practice_readiness_env):
        env = practice_readiness_env
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R2_LLM_POST_TRAINING"
        )
        assert [r["route_key"] for r in data["routes"]] == [
            "R2_LLM_POST_TRAINING"
        ]

    def test_format_text(self, practice_readiness_env):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R3_LLM_INFRA"
        )
        text = format_planner_diagnostic(data)
        assert "Planner Diagnostic" in text
        assert "Candidates" in text
        assert "只读诊断" in text


class TestDiagnosticReadOnly:
    def test_no_writes(self, practice_readiness_env):
        env = practice_readiness_env
        make_requirement_project(env, env.r3.id, ["vLLM 与 PagedAttention"])
        before = _counts(env.conn)
        dv_before = _data_version(env.conn)
        run_planner_diagnostic(env.conn, plan_date=DEFAULT_PLAN_DATE)
        run_planner_diagnostic(env.conn, plan_date=DEFAULT_PLAN_DATE)
        after = _counts(env.conn)
        assert before == after
        # planner_decisions 绝不新增
        assert after["planner_decisions"] == 0

    def test_no_secret_or_output_details(self, practice_readiness_env):
        from tests.practice_capability_helpers import add_output

        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_output(env, project, "repository", "repo",
                   uri="https://private.example.com/secret-repo")
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R3_LLM_INFRA"
        )
        blob = json.dumps(data, ensure_ascii=False)
        assert "private.example.com" not in blob
        assert "secret-repo" not in blob
        assert "details_json" not in blob
        assert "uri" not in blob.lower().replace("security", "")
        assert "sk-" not in blob

    def test_no_ai_client(self):
        import inspect

        import app.diagnostics.planner_diagnostic as mod

        src = inspect.getsource(mod)
        assert "AIClient" not in src
        assert "AdaptiveAIClient" not in src
        assert "chat(" not in src


class TestDiagnosticRouteGate:
    def test_paused_route_marked(self, practice_readiness_env):
        env = practice_readiness_env
        env.route_repo.set_planning_enabled(env.r3.id, False)
        data = run_planner_diagnostic(
            env.conn, plan_date=DEFAULT_PLAN_DATE, route_key="R3_LLM_INFRA"
        )
        assert data["routes"][0]["planning_enabled"] is False

    def test_archived_route_excluded(self, practice_readiness_env):
        env = practice_readiness_env
        env.route_repo.archive(env.r3.id)
        data = run_planner_diagnostic(env.conn, plan_date=DEFAULT_PLAN_DATE)
        keys = {r["route_key"] for r in data["routes"]}
        assert "R3_LLM_INFRA" not in keys
