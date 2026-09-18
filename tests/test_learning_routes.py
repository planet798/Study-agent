"""Phase B：LearningRoute 数据层 / 业务规则测试。

覆盖需求 37 的 3~15、33：
- 默认路线种子 / 同名拒绝 / parent cycle / priority / pause / resume /
  archive / restore / archive 不删数据 / route_skills 唯一 / skill 多路线。
"""

from __future__ import annotations

import pytest

from app.database.learning_route_repository import (
    DEFAULT_ROUTE_LEARNING_NAME,
    DEFAULT_ROUTE_PARENT_NAME,
    ROUTE_STATUS_ARCHIVED,
    ROUTE_TYPE_GROUP,
    ROUTE_TYPE_LEARNING,
    LearningRouteRepository,
)
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_route_service import (
    LearningRouteService,
    RouteValidationError,
)


@pytest.fixture()
def route_repo(conn):
    return LearningRouteRepository(conn)


@pytest.fixture()
def skill_repo(conn):
    return SkillRepository(conn)


@pytest.fixture()
def routes(route_repo, skill_repo):
    return LearningRouteService(route_repo, skill_repo=skill_repo)


# ================= 3~4：默认种子 =================

class TestDefaultSeed:
    def test_default_routes_seeded(self, route_repo):
        parent = route_repo.get_by_name_under_parent(DEFAULT_ROUTE_PARENT_NAME, None)
        assert parent is not None
        assert parent.route_type == ROUTE_TYPE_GROUP
        assert parent.planning_enabled is False

        child = route_repo.get_by_name_under_parent(
            DEFAULT_ROUTE_LEARNING_NAME, parent.id
        )
        assert child is not None
        assert child.route_type == ROUTE_TYPE_LEARNING
        assert child.planning_enabled is True
        assert child.priority == 3

    def test_get_default_learning_route(self, route_repo):
        default = route_repo.get_default_learning_route()
        assert default is not None
        assert default.name == DEFAULT_ROUTE_LEARNING_NAME

    def test_seed_idempotent(self, route_repo):
        # 迁移已 seed；再 ensure 一次不应重复
        assert len(route_repo.list_all()) == 2
        default = route_repo.get_default_learning_route()
        assert route_repo.get_by_name_under_parent(
            DEFAULT_ROUTE_LEARNING_NAME, default.parent_id
        ).id == default.id
        assert len(route_repo.list_all()) == 2


# ================= 17~18：创建校验 =================

class TestCreateRules:
    def test_group_and_learning_creation(self, routes, route_repo):
        root = routes.create_group("求职准备X")
        rl = routes.create_learning_route(
            "强化学习", parent_id=root.id, priority=5, planning_enabled=False
        )
        ds = routes.create_learning_route("数据结构与算法", parent_id=root.id)
        cpp = routes.create_learning_route("C++", parent_id=root.id)
        assert rl.priority == 5
        assert rl.planning_enabled is False
        assert {rl.id, ds.id, cpp.id}.__len__() == 3
        # 彼此独立
        assert rl.parent_id == root.id
        assert ds.id != cpp.id

    def test_empty_name_rejected(self, routes):
        with pytest.raises(RouteValidationError):
            routes.create_group("   ")

    def test_same_parent_duplicate_rejected(self, routes):
        root = routes.create_group("R")
        routes.create_learning_route("C++", parent_id=root.id)
        with pytest.raises(RouteValidationError):
            routes.create_learning_route("C++", parent_id=root.id)

    def test_same_name_under_different_parents_allowed(self, routes):
        a = routes.create_group("A")
        b = routes.create_group("B")
        r1 = routes.create_learning_route("共享名", parent_id=a.id)
        r2 = routes.create_learning_route("共享名", parent_id=b.id)
        assert r1.id != r2.id

    def test_root_duplicate_rejected(self, routes):
        routes.create_group("根路线")
        with pytest.raises(RouteValidationError):
            routes.create_group("根路线")

    def test_missing_parent_rejected(self, routes):
        with pytest.raises(RouteValidationError):
            routes.create_learning_route("X", parent_id=99999)

    def test_cycle_rejected(self, routes):
        a = routes.create_group("A")
        b = routes.create_group("B", parent_id=a.id)
        with pytest.raises(RouteValidationError):
            routes.set_parent(a.id, b.id)
        # 直接挂到自己
        with pytest.raises(RouteValidationError):
            routes.set_parent(a.id, a.id)


# ================= 7：priority =================

class TestPriority:
    @pytest.mark.parametrize("value", [1, 3, 5])
    def test_valid_priority(self, routes, value):
        r = routes.create_group(f"P{value}", priority=value)
        assert r.priority == value
        assert routes.set_priority(r.id, value).priority == value

    @pytest.mark.parametrize("value", [0, 6, -1])
    def test_invalid_priority(self, routes, value):
        with pytest.raises(RouteValidationError):
            routes.create_group(f"bad{value}", priority=value)
        r = routes.create_group("ok")
        with pytest.raises(RouteValidationError):
            routes.set_priority(r.id, value)


# ================= 8~12：pause / resume / archive / restore =================

class TestLifecycle:
    def test_pause_and_resume_planning(self, routes):
        r = routes.create_learning_route("RL")
        assert r.planning_enabled is True
        paused = routes.pause_planning(r.id)
        assert paused.planning_enabled is False
        assert paused.status == "active"  # pause 不改 status
        resumed = routes.resume_planning(r.id)
        assert resumed.planning_enabled is True

    def test_group_cannot_resume_planning(self, routes):
        g = routes.create_group("G")
        with pytest.raises(RouteValidationError):
            routes.resume_planning(g.id)

    def test_archive_semantics(self, routes):
        r = routes.create_learning_route("RL")
        archived = routes.archive_route(r.id)
        assert archived.status == ROUTE_STATUS_ARCHIVED
        assert archived.planning_enabled is False
        assert archived.archived_at is not None

    def test_restore_keeps_planning_disabled(self, routes):
        r = routes.create_learning_route("RL")
        routes.archive_route(r.id)
        restored = routes.restore_route(r.id)
        assert restored.status == "active"
        # 安全默认：恢复后不自动开始规划
        assert restored.planning_enabled is False
        # 需显式恢复
        assert routes.resume_planning(r.id).planning_enabled is True

    def test_archived_cannot_resume_without_restore(self, routes):
        r = routes.create_learning_route("RL")
        routes.archive_route(r.id)
        with pytest.raises(RouteValidationError):
            routes.resume_planning(r.id)

    def test_cannot_create_under_archived_parent(self, routes):
        g = routes.create_group("G")
        routes.archive_route(g.id)
        with pytest.raises(RouteValidationError):
            routes.create_group("child", parent_id=g.id)

    def test_archive_does_not_delete_data(self, conn, routes, route_repo):
        plan_repo = StudyPlanRepository(conn)
        r = routes.create_learning_route("RL")
        plan = plan_repo.create_plan("RL plan", "2026-09-01", "2026-12-31",
                                     route_id=r.id)
        phase = plan_repo.create_phase(plan.id, "RL phase", "2026-09-01",
                                       "2026-09-30")
        topic = plan_repo.create_topic(phase.id, "MDP")
        routes.archive_route(r.id)
        # 数据全部保留
        assert plan_repo.get_plan(plan.id) is not None
        assert plan_repo.get_phase(phase.id) is not None
        assert plan_repo.get_topic(topic.id) is not None
        assert route_repo.get(r.id).status == ROUTE_STATUS_ARCHIVED

    def test_list_tree(self, routes):
        root = routes.create_group("Root")
        child = routes.create_learning_route("Child", parent_id=root.id)
        tree = routes.list_tree()
        node = next(n for n in tree if n.id == root.id)
        assert [c.id for c in node.children] == [child.id]


# ================= 14~15：route_skills =================

class TestRouteSkills:
    def test_assign_unique(self, routes, skill_repo):
        r = routes.create_learning_route("RL")
        s = skill_repo.create("PyTorch")
        assert routes.assign_skill(r.id, s["id"]) is True
        # 重复 assign 幂等（UNIQUE(route_id, skill_id)）
        assert routes.assign_skill(r.id, s["id"]) is False
        assert routes.route_repo.count_route_skills() == 1

    def test_skill_belongs_to_multiple_routes(self, routes, skill_repo):
        r1 = routes.create_learning_route("A")
        r2 = routes.create_learning_route("B")
        s = skill_repo.create("PyTorch")
        routes.assign_skill(r1.id, s["id"])
        routes.assign_skill(r2.id, s["id"])
        assert routes.route_repo.list_route_ids_for_skill(s["id"]) == [r1.id, r2.id]
        assert [x["name"] for x in routes.get_route_skills(r1.id)] == ["PyTorch"]

    def test_unassign(self, routes, skill_repo):
        r = routes.create_learning_route("RL")
        s = skill_repo.create("PPO")
        routes.assign_skill(r.id, s["id"])
        assert routes.unassign_skill(r.id, s["id"]) is True
        assert routes.get_route_skills(r.id) == []

    def test_assign_unknown_skill_rejected(self, routes):
        r = routes.create_learning_route("RL")
        with pytest.raises(RouteValidationError):
            routes.assign_skill(r.id, 99999)
