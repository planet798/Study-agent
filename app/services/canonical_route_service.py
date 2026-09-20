"""Canonical 六技术路线 seed（Phase 1，幂等）。

职责：
- 幂等创建 / 对齐 求职准备 group 与 R1–R6 六条 canonical learning route；
- 为每条路线创建独立 StudyPlan + phases + topics（不修改旧 plan.route_id）；
- 标记旧 `搜广推 + LLM` 为 LEGACY_SEARCH_LLM 并 archive（不删除）；
- 修复 alias 指向不存在 skill 的问题（PEFT / MoE / 后训练 / 对齐 / 模型蒸馏）；
- 建立 canonical route_skills（N:N）与 SPLIT_NEW topic → skill 的 linked_topics。

不负责：MOVE 迁移（见 route_migration_service）、Planner/Scheduler 逻辑。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..database.learning_route_repository import (
    ROUTE_STATUS_ARCHIVED,
    ROUTE_SOURCE_SYSTEM,
    ROUTE_TYPE_GROUP,
    ROUTE_TYPE_LEARNING,
    ROUTE_KEY_JOB_PREP,
    ROUTE_KEY_LEGACY_SEARCH_LLM,
    LearningRoute,
    LearningRouteRepository,
)
from ..database.skill_repository import SkillRepository
from ..database.study_plan_repository import StudyPlan, StudyPlanRepository
from ..database.topic_learning_repository import (
    TopicLearningComponentRepository,
)
from . import canonical_routes as C
from .canonical_topic_components import profile_for_topic
from .topic_learning_profile_service import TopicLearningProfileService


class CanonicalRouteService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        route_repo: LearningRouteRepository | None = None,
        plan_repo: StudyPlanRepository | None = None,
        skill_repo: SkillRepository | None = None,
        topic_learning_service: TopicLearningProfileService | None = None,
    ):
        self.conn = conn
        self.route_repo = route_repo or LearningRouteRepository(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)
        self.skill_repo = skill_repo
        self.topic_learning = topic_learning_service or TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )

    # ================= 入口 =================

    def ensure_all(self) -> dict:
        """幂等 seed 全部 canonical 结构 + 安全历史迁移。

        顺序（关键）：
          routes → plans/phases → **MOVE 迁移** → topics → skills/links
        先建 phase 后建 topic，使 MOVE 能把旧 topic reparent 进 canonical phase，
        再由 ensure_topics 只补缺失 topic（不会重复建）。
        """
        pre = self.ensure_pre_migration()
        routes = pre["routes"]
        legacy = pre["legacy"]
        plans = pre["plans"]

        from .route_migration_service import RouteMigrationService

        migration = RouteMigrationService(
            self.conn, self.route_repo, self.plan_repo
        ).apply_if_safe()

        self.ensure_topics(routes)
        self.ensure_extra_skills()
        links = self.ensure_route_skills(routes)
        topic_links = self.ensure_topic_skill_links()
        profiles = self.ensure_topic_profiles(routes)
        backfill = self.topic_learning.backfill_legacy_theory()
        consistency = self.topic_learning.repair_consistency()
        return {
            "group_id": pre["group"].id,
            "route_ids": {k: r.id for k, r in routes.items()},
            "legacy_route_id": legacy.id if legacy else None,
            "plan_ids": {k: p.id for k, p in plans.items()},
            "route_skill_links": links,
            "topic_skill_links": topic_links,
            "activity_profiles": profiles,
            "legacy_theory_backfill": backfill,
            "consistency_repair": consistency,
            "migration": {
                "applied": migration.get("applied"),
                "reason": migration.get("reason"),
                "summary": migration.get("summary"),
            },
        }

    def ensure_topic_profiles(self, routes: dict[str, LearningRoute]) -> int:
        """首次初始化每个 canonical Topic 的 learning component profile。

        已有 profile（用户可能已修改）→ 不覆盖。返回新建 profile 的 Topic 数。
        """
        created = 0
        for key in C.CANONICAL_LEARNING_KEYS:
            route = routes.get(key)
            if route is None:
                continue
            for topic in self.plan_repo.list_topics_by_route(route.id):
                if self.topic_learning.has_profile(topic.id):
                    continue
                spec = profile_for_topic(topic.name, key)
                self.topic_learning.ensure_profile_from_spec(topic.id, spec)
                created += 1
        return created

    def ensure_pre_migration(self) -> dict:
        """只建 routes + plans + phases（不建 topics、不迁移）。

        供 CLI `preview` / 严格 `apply` 先置备迁移目标。
        """
        group = self.ensure_group()
        routes = self.ensure_routes(group.id)
        legacy = self.mark_legacy_route()
        plans = self.ensure_plans(routes, create_topics=False)
        return {"group": group, "routes": routes, "legacy": legacy,
                "plans": plans}

    # ================= group =================

    def ensure_group(self) -> LearningRoute:
        route = self.route_repo.get_by_key(ROUTE_KEY_JOB_PREP)
        if route is None:
            # 兼容 v12 旧 seed：按 name 认领已有 group
            route = self.route_repo.get_by_name_under_parent(
                C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["name"], None
            )
        if route is None:
            return self.route_repo.create(
                name=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["name"],
                parent_id=None,
                route_type=ROUTE_TYPE_GROUP,
                source=ROUTE_SOURCE_SYSTEM,
                priority=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["priority"],
                planning_enabled=False,
                route_key=ROUTE_KEY_JOB_PREP,
                description=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["description"],
            )
        return self.route_repo.update(
            route.id,
            name=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["name"],
            parent_id=None,
            route_type=ROUTE_TYPE_GROUP,
            source=ROUTE_SOURCE_SYSTEM,
            priority=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["priority"],
            planning_enabled=False,
            route_key=ROUTE_KEY_JOB_PREP,
            description=C.CANONICAL_ROUTES[ROUTE_KEY_JOB_PREP]["description"],
        )

    # ================= routes =================

    def ensure_routes(self, group_id: int) -> dict[str, LearningRoute]:
        out: dict[str, LearningRoute] = {}
        for key in C.CANONICAL_LEARNING_KEYS:
            spec = C.CANONICAL_ROUTES[key]
            route = self.route_repo.get_by_key(key)
            if route is None:
                route = self._adopt_existing_route(key, group_id)
            if route is None:
                route = self.route_repo.create(
                    name=spec["name"],
                    parent_id=group_id,
                    route_type=ROUTE_TYPE_LEARNING,
                    source=ROUTE_SOURCE_SYSTEM,
                    status="active",
                    priority=spec["priority"],
                    planning_enabled=True,
                    route_key=key,
                    goal=spec["goal"],
                    description=spec["description"],
                )
            else:
                route = self.route_repo.update(
                    route.id,
                    name=spec["name"],
                    parent_id=group_id,
                    route_type=ROUTE_TYPE_LEARNING,
                    source=ROUTE_SOURCE_SYSTEM,
                    status="active",
                    priority=spec["priority"],
                    planning_enabled=True,
                    route_key=key,
                    goal=spec["goal"],
                    description=spec["description"],
                )
            out[key] = route
        return out

    def _adopt_existing_route(
        self, key: str, group_id: int
    ) -> Optional[LearningRoute]:
        """按 name 认领一个已存在但还没有 route_key 的 learning route。

        仅当名称精确匹配、且尚未被其它 canonical key 占用时认领；
        避免误认用户手工路线（因此只在 name 完全等于 canonical name 时）。
        """
        spec = C.CANONICAL_ROUTES[key]
        route = self.route_repo.get_by_name_under_parent(spec["name"], group_id)
        if route is None:
            return None
        if route.route_key and route.route_key != key:
            return None
        return route

    # ================= legacy =================

    def mark_legacy_route(self) -> Optional[LearningRoute]:
        """把旧 `搜广推 + LLM` 标记 route_key 并 archive（不删除）。"""
        route = self.route_repo.get_by_key(ROUTE_KEY_LEGACY_SEARCH_LLM)
        if route is None:
            # 兼容 v12 seed：旧 learning route 可能挂在 group 下，按名称全局认领
            candidates = [
                r for r in self.route_repo.list_all()
                if r.name == "搜广推 + LLM" and not r.is_group
            ]
            route = candidates[0] if candidates else None
        if route is None:
            return None
        if route.route_key != ROUTE_KEY_LEGACY_SEARCH_LLM:
            route = self.route_repo.update(
                route.id, route_key=ROUTE_KEY_LEGACY_SEARCH_LLM
            )
        if route.status != ROUTE_STATUS_ARCHIVED or route.planning_enabled:
            route = self.route_repo.update(
                route.id,
                status=ROUTE_STATUS_ARCHIVED,
                planning_enabled=False,
            )
        return route

    # ================= plans / phases / topics =================

    def ensure_plans(
        self, routes: dict[str, LearningRoute], create_topics: bool = True
    ) -> dict[str, StudyPlan]:
        out: dict[str, StudyPlan] = {}
        for key in C.CANONICAL_LEARNING_KEYS:
            route = routes[key]
            plan = self.plan_repo.get_plan_by_route(route.id)
            if plan is None:
                plan = self.plan_repo.create_plan(
                    name=f"{C.CANONICAL_ROUTES[key]['name']}学习计划",
                    description=C.CANONICAL_ROUTES[key]["description"],
                    start_date=C.PLAN_START_DATE,
                    end_date=C.PLAN_END_DATE,
                    status="active",
                    route_id=route.id,
                )
            else:
                plan = self.plan_repo.update_plan(
                    plan.id,
                    description=C.CANONICAL_ROUTES[key]["description"],
                ) or plan
            self._ensure_phases(plan.id, key)
            if create_topics:
                self._ensure_topics(plan.id, key)
            out[key] = plan
        return out

    def ensure_topics(self, routes: dict[str, LearningRoute]) -> None:
        """只创建缺失的 canonical topics（幂等，不删除）。"""
        for key in C.CANONICAL_LEARNING_KEYS:
            route = routes[key]
            plan = self.plan_repo.get_plan_by_route(route.id)
            if plan is None:
                continue
            self._ensure_topics(plan.id, key)

    def _ensure_phases(self, plan_id: int, route_key: str) -> None:
        existing_phases = {p.name: p for p in self.plan_repo.list_phases(plan_id)}
        for idx, spec in enumerate(C.CANONICAL_PHASES.get(route_key, [])):
            order_index = idx + 1
            phase = existing_phases.get(spec["name"])
            if phase is None:
                self.plan_repo.create_phase(
                    plan_id=plan_id,
                    name=spec["name"],
                    description=spec.get("goal", ""),
                    start_date=C.PLAN_START_DATE,
                    end_date=C.PLAN_END_DATE,
                    priority=1,
                    goals=spec.get("goal", ""),
                    order_index=order_index,
                )
            else:
                self.plan_repo.update_phase(
                    phase.id,
                    goals=spec.get("goal", ""),
                    start_date=C.PLAN_START_DATE,
                    end_date=C.PLAN_END_DATE,
                    order_index=order_index,
                )

    def _ensure_topics(self, plan_id: int, route_key: str) -> None:
        phases = {p.name: p for p in self.plan_repo.list_phases(plan_id)}
        for spec in C.CANONICAL_PHASES.get(route_key, []):
            phase = phases.get(spec["name"])
            if phase is None:
                continue
            existing_topics = {
                t.name: t for t in self.plan_repo.list_topics(phase.id)
            }
            for t_idx, topic in enumerate(spec["topics"]):
                current = existing_topics.get(topic["name"])
                if current is None:
                    self.plan_repo.create_topic(
                        phase_id=phase.id,
                        name=topic["name"],
                        description=topic["name"],
                        estimated_minutes=int(topic["minutes"]),
                        priority=int(topic["priority"]),
                        order_index=t_idx + 1,
                    )
                else:
                    self.plan_repo.update_topic(
                        current.id, order_index=t_idx + 1
                    )

    # ================= skills =================

    def ensure_extra_skills(self) -> list[str]:
        if self.skill_repo is None:
            return []
        added_or_updated: list[str] = []
        for name, spec in C.CANONICAL_EXTRA_SKILLS.items():
            self.skill_repo.upsert_by_name(
                name,
                tier=spec.get("tier", "C"),
                category=spec.get("category", "core"),
                prerequisites=list(spec.get("prerequisites") or []),
            )
            added_or_updated.append(name)
        return added_or_updated

    def ensure_route_skills(
        self, routes: dict[str, LearningRoute]
    ) -> int:
        """按 CANONICAL_SKILL_ROUTE_MAP 建立 route_skills（N:N，幂等）。"""
        if self.skill_repo is None:
            return 0
        count = 0
        for skill_name, route_keys in C.CANONICAL_SKILL_ROUTE_MAP.items():
            skill = self.skill_repo.get_by_name(skill_name)
            if skill is None:
                continue
            for key in route_keys:
                route = routes.get(key)
                if route is None:
                    continue
                if self.route_repo.assign_skill(route.id, skill["id"]):
                    count += 1
        return count

    def ensure_topic_skill_links(self) -> int:
        """把 canonical（主要是 SPLIT_NEW / 新）Topic 幂等链接到 skill.linked_topics。

        MOVE 的旧 topic_id 不变，历史 linked_topics 关系自然保留；
        这里只补充新 Topic 的关系（缺失才加，绝不删除）。
        """
        if self.skill_repo is None:
            return 0
        # 收集 canonical route 下所有 topic：name -> topic_id
        name_to_ids: dict[str, list[int]] = {}
        for key in C.CANONICAL_LEARNING_KEYS:
            route = self.route_repo.get_by_key(key)
            if route is None:
                continue
            for topic in self.plan_repo.list_topics_by_route(route.id):
                name_to_ids.setdefault(topic.name, []).append(topic.id)

        added = 0
        for topic_name, skill_names in C.CANONICAL_TOPIC_SKILL_LINKS.items():
            topic_ids = name_to_ids.get(topic_name)
            if not topic_ids:
                continue
            for skill_name in skill_names:
                skill = self.skill_repo.get_by_name(skill_name)
                if skill is None:
                    continue
                current = list(skill.get("linked_topics") or [])
                changed = False
                for tid in topic_ids:
                    if tid not in current:
                        current.append(int(tid))
                        changed = True
                if changed:
                    self.skill_repo.update(skill["id"], linked_topics=current)
                    added += 1
        return added
