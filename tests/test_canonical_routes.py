"""Canonical 六技术路线 seed 测试（Phase 1）。"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.learning_route_repository import (
    ROUTE_KEY_JOB_PREP,
    ROUTE_KEY_LEGACY_SEARCH_LLM,
)
from app.services import canonical_routes as C
from app.services.canonical_route_service import CanonicalRouteService
from app.services.learning_route_service import LearningRouteService
from app.services.route_migration_service import RouteMigrationService


def _svc(env):
    return CanonicalRouteService(
        env.conn, env.route_repo, env.plan_repo, env.skill_repo
    )


class TestRouteKeyUniqueness:
    def test_route_key_partial_unique_index(self, conn):
        """route_key 唯一（partial index）；NULL 可重复。"""
        repo = None
        from app.database.learning_route_repository import LearningRouteRepository

        repo = LearningRouteRepository(conn)
        repo.create("A", route_key="UNIQUE_KEY")
        with pytest.raises(sqlite3.IntegrityError):
            repo.create("B", route_key="UNIQUE_KEY")
        # NULL 可以有多条（用户手路线）
        repo.create("C", route_key=None)
        repo.create("D", route_key=None)

    def test_get_by_key(self, conn):
        from app.database.learning_route_repository import LearningRouteRepository

        repo = LearningRouteRepository(conn)
        r = repo.create("X", route_key="MY_KEY")
        assert repo.get_by_key("MY_KEY").id == r.id
        assert repo.get_by_key("NOPE") is None


class TestCanonicalSeed:
    def test_seeds_group_and_six_routes(self, six_route_env):
        res = _svc(six_route_env).ensure_all()
        assert set(res["route_ids"]) == set(C.CANONICAL_LEARNING_KEYS)
        group = six_route_env.route_repo.get_by_key(ROUTE_KEY_JOB_PREP)
        assert group is not None and group.is_group
        for key in C.CANONICAL_LEARNING_KEYS:
            route = six_route_env.route_repo.get_by_key(key)
            assert route is not None
            assert route.parent_id == group.id
            assert route.status == "active"
            assert route.source == "system"
            assert route.is_learning

    def test_priorities(self, six_route_env):
        _svc(six_route_env).ensure_all()
        expected = {
            "R1_LLM_FUNDAMENTALS": 5,
            "R2_LLM_POST_TRAINING": 5,
            "R3_LLM_INFRA": 3,
            "R4_AI_AGENT": 5,
            "R5_RECOMMENDATION_SEARCH": 4,
            "R6_CS_FUNDAMENTALS": 3,
        }
        for key, priority in expected.items():
            assert six_route_env.route_repo.get_by_key(key).priority == priority

    def test_seed_idempotent(self, six_route_env):
        svc = _svc(six_route_env)
        first = svc.ensure_all()
        second = svc.ensure_all()
        assert first["route_ids"] == second["route_ids"]
        assert first["plan_ids"] == second["plan_ids"]
        # topic 数量稳定
        counts1 = {
            k: len(six_route_env.plan_repo.list_topics_by_route(rid))
            for k, rid in first["route_ids"].items()
        }
        counts2 = {
            k: len(six_route_env.plan_repo.list_topics_by_route(rid))
            for k, rid in second["route_ids"].items()
        }
        assert counts1 == counts2

    def test_six_independent_plans(self, six_route_env):
        res = _svc(six_route_env).ensure_all()
        plan_ids = list(res["plan_ids"].values())
        assert len(set(plan_ids)) == 6
        for key, plan_id in res["plan_ids"].items():
            plan = six_route_env.plan_repo.get_plan(plan_id)
            assert plan.route_id == res["route_ids"][key]
            assert plan.status == "active"

    def test_one_active_plan_per_route(self, six_route_env):
        """v15 partial unique index：同 route 不能有两个 active plan。"""
        res = _svc(six_route_env).ensure_all()
        route_id = res["route_ids"]["R1_LLM_FUNDAMENTALS"]
        with pytest.raises(sqlite3.IntegrityError):
            six_route_env.plan_repo.create_plan(
                name="dup", start_date=C.PLAN_START_DATE,
                end_date=C.PLAN_END_DATE, status="active", route_id=route_id,
            )


class TestLegacyArchive:
    def test_legacy_route_archived_but_not_deleted(self, six_route_env):
        legacy_id = six_route_env.legacy_route.id
        _svc(six_route_env).ensure_all()
        legacy = six_route_env.route_repo.get_by_key(ROUTE_KEY_LEGACY_SEARCH_LLM)
        assert legacy is not None
        assert legacy.id == legacy_id
        assert legacy.status == "archived"
        assert legacy.planning_enabled is False

    def test_legacy_not_reused_as_r1(self, six_route_env):
        _svc(six_route_env).ensure_all()
        r1 = six_route_env.route_repo.get_by_key("R1_LLM_FUNDAMENTALS")
        legacy = six_route_env.route_repo.get_by_key(ROUTE_KEY_LEGACY_SEARCH_LLM)
        assert r1.id != legacy.id
        assert r1.name != legacy.name

    def test_default_learning_route_compat(self, six_route_env):
        """legacy get_default_learning_route 仍返回旧路线（兼容）。"""
        _svc(six_route_env).ensure_all()
        repo = six_route_env.route_repo
        legacy_route = repo.get_default_learning_route()
        assert legacy_route is not None
        assert legacy_route.route_key == ROUTE_KEY_LEGACY_SEARCH_LLM

    def test_service_helpers(self, six_route_env):
        _svc(six_route_env).ensure_all()
        svc = LearningRouteService(six_route_env.route_repo)
        assert svc.get_route_by_key("R4_AI_AGENT") is not None
        canonical = svc.get_canonical_routes()
        assert [r.route_key for r in canonical] == list(C.CANONICAL_LEARNING_KEYS)
        assert svc.get_job_prep_group() is not None


class TestRouteIsolation:
    def test_canonical_route_topics_do_not_mix(self, six_route_env):
        res = _svc(six_route_env).ensure_all()
        r1_topics = {t.name for t in six_route_env.plan_repo.list_topics_by_route(
            res["route_ids"]["R1_LLM_FUNDAMENTALS"])}
        r4_topics = {t.name for t in six_route_env.plan_repo.list_topics_by_route(
            res["route_ids"]["R4_AI_AGENT"])}
        r5_topics = {t.name for t in six_route_env.plan_repo.list_topics_by_route(
            res["route_ids"]["R5_RECOMMENDATION_SEARCH"])}
        assert "Function Calling / Tool Calling" in r4_topics
        assert "Function Calling / Tool Calling" not in r1_topics
        assert "推荐系统整体架构" in r5_topics
        assert "推荐系统整体架构" not in r4_topics
        assert "Transformer：Attention / MHA / FFN" in r1_topics

    def test_current_phase_by_route(self, six_route_env):
        res = _svc(six_route_env).ensure_all()
        from app.services.study_plan_service import StudyPlanService

        repo = six_route_env.repo
        plan_repo = six_route_env.plan_repo
        for key, route_id in res["route_ids"].items():
            sps = StudyPlanService(
                repo, plan_repo, route_id=route_id,
                learning_route_repo=six_route_env.route_repo,
            )
            phase = sps.get_current_phase("2026-01-05")
            assert phase is not None, key
            assert phase.name == C.CANONICAL_PHASES[key][0]["name"]


class TestSchedulerIntegration:
    def test_plannable_includes_all_six_not_legacy(self, six_route_env):
        _svc(six_route_env).ensure_all()
        from app.services.route_scheduler import GlobalDailyScheduler

        scheduler = GlobalDailyScheduler(
            six_route_env.repo, six_route_env.plan_repo,
            six_route_env.route_repo,
        )
        plannable = scheduler.plannable_routes("2026-01-05")
        keys = {r.route_key for r in plannable}
        assert set(C.CANONICAL_LEARNING_KEYS).issubset(keys)
        assert ROUTE_KEY_LEGACY_SEARCH_LLM not in keys

    def test_scheduler_respects_global_budget(self, six_route_env):
        _svc(six_route_env).ensure_all()
        from app.services.route_scheduler import GlobalDailyScheduler

        scheduler = GlobalDailyScheduler(
            six_route_env.repo, six_route_env.plan_repo,
            six_route_env.route_repo, budget=3,
        )
        result = scheduler.generate("2026-01-05")
        assert len(result["created_ids"]) <= 3
        assert result["remaining_minutes"] >= 0
        total_minutes = sum(t.estimated_minutes for t in result["created"])
        assert total_minutes <= result["max_minutes"]


class TestPromptRuntimeContext:
    def test_canonical_prompts_have_no_old_route_hardcode(self):
        from app.ai.prompt_defaults import DEFAULT_PROMPT_DEFINITIONS

        for d in DEFAULT_PROMPT_DEFINITIONS:
            assert "搜广推" not in d.default_template
            assert "搜广推 + LLM" not in d.default_template

    def test_planner_preview_uses_route_name_dynamically(self, conn, prompt_registry):
        from app.ai.planner_context import PlanningContext
        from app.ai.prompts import build_planner_user_vars
        from app.ai.prompt_registry import PromptOverrideRepository

        ctx = PlanningContext(current_date="2026-01-05")
        ctx.route_name = "R4 AI Agent"
        ctx.route_goal = "构建 Agent"
        ctx.current_daily_limit = 180
        text = prompt_registry.render(
            "planner.user", build_planner_user_vars(ctx, None)
        )
        assert "R4 AI Agent" in text
        assert "搜广推" not in text


class TestActivePlanDedupe:
    def test_dedupe_active_plans(self, conn):
        from app.database import schema
        from app.database.study_plan_repository import StudyPlanRepository

        # 去掉 partial index，制造同 route 多 active plan
        conn.execute("DROP INDEX IF EXISTS idx_study_plans_active_route")
        route_repo = None
        from app.database.learning_route_repository import LearningRouteRepository

        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        r = route_repo.create("TMP", route_key="TMP_DEDUPE")
        p1 = plan_repo.create_plan(
            "old", C.PLAN_START_DATE, C.PLAN_END_DATE, route_id=r.id)
        plan_repo.create_plan(
            "new", C.PLAN_START_DATE, C.PLAN_END_DATE, route_id=r.id)
        archived = schema._dedupe_active_plans(conn)
        assert archived == 1
        actives = [
            p for p in plan_repo.list_plans(status="active")
            if p.route_id == r.id
        ]
        assert len(actives) == 1
        # 确定性：无 tasks 时保留 id 更大的（更新）plan
        assert actives[0].id != p1.id
        # 重建索引后仍可工作
        schema._migrate_v15(conn)


class TestRoutesPageUI:
    def test_canonical_and_archived_display(self, qtbot, six_route_env):
        from app.ui.routes_page import LearningRoutesPage

        res = _svc(six_route_env).ensure_all()
        page = LearningRoutesPage(six_route_env.route_service
                                  if hasattr(six_route_env, "route_service")
                                  else _route_service(six_route_env))
        qtbot.addWidget(page)
        texts = _all_labels(page)
        assert "R1 LLM Fundamentals" in texts
        assert "R6 CS Fundamentals" in texts
        # 默认不显示已归档
        assert "搜广推 + LLM" not in texts
        page._on_toggle_archived()
        texts_after = _all_labels(page)
        assert "搜广推 + LLM" in texts_after


def _route_service(env):
    from app.services.learning_route_service import LearningRouteService

    return LearningRouteService(env.route_repo, skill_repo=env.skill_repo)


def _all_labels(widget):
    out = []
    from PySide6.QtWidgets import QLabel

    for label in widget.findChildren(QLabel):
        out.append(label.text())
    return out
