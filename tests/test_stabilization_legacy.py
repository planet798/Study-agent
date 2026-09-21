"""v1 stabilization：P2 legacy 技术债清理测试。

覆盖：
- 生产规划路径不再调用 get_default_learning_route；
- canonical 存在时 legacy default plan seed 被隔离；
- 全局按 name 的 KP lookup 已删除；
- extra / exploration 无生产创建入口；
- legacy 常量已显式命名。
"""

from __future__ import annotations

import inspect

import pytest

# 生产规划链模块（必须完全不依赖 legacy default route）
PLANNER_PATH_MODULES = (
    "app.services.daily_planner_service",
    "app.services.route_scheduler",
    "app.services.study_plan_service",
    "app.services.planner_feedback",
    "app.services.practice_readiness",
)


class TestNoDefaultRouteDependency:
    def test_planner_path_does_not_call_default_route(self):
        import importlib

        for name in PLANNER_PATH_MODULES:
            mod = importlib.import_module(name)
            src = inspect.getsource(mod)
            assert "get_default_learning_route" not in src, name

    def test_only_legacy_callers_remain(self):
        import subprocess

        out = subprocess.run(
            ["grep", "-rn", "get_default_learning_route", "app/", "--include=*.py"],
            capture_output=True, text=True,
        ).stdout
        allowed = (
            "app/services/route_migration_service.py",
            "app/services/learning_route_service.py",
            "app/database/learning_route_repository.py",
        )
        for line in out.strip().splitlines():
            assert any(line.startswith(a) for a in allowed), line

    def test_legacy_method_marked(self):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )

        doc = inspect.getdoc(
            LearningRouteRepository.get_default_learning_route
        )
        assert "LEGACY COMPATIBILITY ONLY" in doc


class TestLegacyPlanSeedIsolation:
    def test_ensure_default_plan_skips_when_canonical_present(self, conn):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.skill_repository import SkillRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.canonical_route_service import CanonicalRouteService
        from app.services.study_plan_service import StudyPlanService

        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        skill_repo = SkillRepository(conn)
        sps = StudyPlanService(repo, plan_repo, learning_route_repo=route_repo)
        sps.ensure_default_plan()  # 首次（canonical 未建）→ legacy seed
        legacy_plan = plan_repo.get_active_plan(route_id=sps._resolved_route_id())
        assert legacy_plan is not None

        CanonicalRouteService(
            conn, route_repo, plan_repo, skill_repo
        ).ensure_all()
        assert sps._canonical_routes_present() is True

        # canonical 已存在：不得创建 / 恢复 / 同步任何 legacy plan
        plans_before = {p.id for p in plan_repo.list_plans()}
        phases_before = plan_repo.list_phases(legacy_plan.id)
        names_before = [p.name for p in phases_before]
        result = sps.ensure_default_plan()
        plans_after = {p.id for p in plan_repo.list_plans()}
        assert plans_before == plans_after
        names_after = [
            p.name for p in plan_repo.list_phases(legacy_plan.id)
        ]
        assert names_before == names_after
        # 返回现有 legacy plan（或 None），绝不新建
        assert result is None or result.id in plans_before

    def test_resolved_route_none_on_canonical(self, conn):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.skill_repository import SkillRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.canonical_route_service import CanonicalRouteService
        from app.services.study_plan_service import StudyPlanService

        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        CanonicalRouteService(
            conn, route_repo, plan_repo, SkillRepository(conn)
        ).ensure_all()
        sps = StudyPlanService(repo, plan_repo, learning_route_repo=route_repo)
        # canonical DB：全局 service 不再隐式猜默认路线
        assert sps._resolved_route_id() is None

    def test_resolved_route_binds_legacy_system_route(self, conn):
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.repository import TaskRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.services.study_plan_service import StudyPlanService

        repo = TaskRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        route_repo = LearningRouteRepository(conn)
        sps = StudyPlanService(repo, plan_repo, learning_route_repo=route_repo)
        route_id = sps._resolved_route_id()
        assert route_id is not None
        system = [
            r for r in route_repo.list_learning_routes()
            if getattr(r, "source", "") == "system"
        ]
        assert [r.id for r in system] == [route_id]

    def test_legacy_constant_renamed(self):
        import app.services.study_plan_service as sps

        assert hasattr(sps, "LEGACY_DEFAULT_PHASES")
        assert not hasattr(sps, "_DEFAULT_PHASES")
        src = inspect.getsource(sps)
        assert "LEGACY COMPATIBILITY ONLY" in src


class TestDeadApiRemoved:
    def test_global_name_lookup_removed(self):
        from app.database.assessment_repository import AssessmentRepository

        assert not hasattr(AssessmentRepository, "get_knowledge_point_by_name")
        assert hasattr(
            AssessmentRepository, "get_knowledge_point_by_name_and_route"
        )

    def test_no_source_calls_global_name_lookup(self):
        import subprocess

        out = subprocess.run(
            ["grep", "-rn", "get_knowledge_point_by_name(", "app/",
             "--include=*.py"],
            capture_output=True, text=True,
        ).stdout
        # 只允许 by_name_and_route
        for line in out.strip().splitlines():
            assert "by_name_and_route" in line, line


class TestExtraAndExploration:
    def test_no_production_creator_for_extra(self):
        import subprocess

        out = subprocess.run(
            ["grep", "-rnE", "task_type=(\"|')extra", "app/",
             "--include=*.py"],
            capture_output=True, text=True,
        ).stdout
        assert out.strip() == ""
        out2 = subprocess.run(
            ["grep", "-rnE", "source=(\"|')extra", "app/", "--include=*.py"],
            capture_output=True, text=True,
        ).stdout
        assert out2.strip() == ""

    def test_extra_only_in_legacy_cleanup_and_filter(self):
        import app.services.date_service as ds
        import app.services.task_service as ts

        for mod in (ds, ts):
            src = inspect.getsource(mod)
            assert "LEGACY" in src.upper()

    def test_exploration_has_no_production_entry(self):
        import subprocess

        out = subprocess.run(
            ["grep", "-rnE", "exploration|课外|额外学习", "app/",
             "--include=*.py"],
            capture_output=True, text=True,
        ).stdout
        for line in out.strip().splitlines():
            # 只允许注释 / 文档字符串 / legacy 清理说明
            body = line.split(":", 2)[-1].strip()
            assert (
                body.startswith("#")
                or body.startswith('"""')
                or body.startswith("-")
                or "LEGACY" in body
                or "已移除" in body
                or "生产创建入口" in body
            ), line


class TestSkillScopeLegacyCompat:
    def test_canonical_route_without_bindings_is_restricted(
        self, practice_readiness_env
    ):
        from app.services.daily_planner_service import DailyPlannerService
        from app.database.assessment_repository import AssessmentRepository

        env = practice_readiness_env
        sps = env.sps_for(env.r6.id)
        planner = DailyPlannerService(
            env.repo, env.plan_repo, study_plan_service=sps,
            assessment_repo=AssessmentRepository(env.conn),
            scope_tasks_by_route=True,
        )
        allowed = planner._allowed_skill_names()
        # canonical 路线：无绑定 → 空集合（不允许全部技能）
        assert allowed is not None
        assert allowed == set()
