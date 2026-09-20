"""学习路线（LearningRoute）业务层（Phase B）。

职责：
- 路线树创建 / 同级重名校验 / parent cycle 防护；
- priority(1~5) / planning_enabled（暂停规划）/ status（归档）语义；
- route ↔ skill 多对多分配；
- 默认学习路线解析。

本阶段**不接** Planner / UI / Review：只提供数据能力。
"""

from __future__ import annotations

from ..database.learning_route_repository import (
    PRIORITY_MAX,
    PRIORITY_MIN,
    ROUTE_SOURCE_MANUAL,
    ROUTE_STATUS_ACTIVE,
    ROUTE_STATUS_ARCHIVED,
    ROUTE_TYPE_GROUP,
    ROUTE_TYPE_LEARNING,
    LearningRoute,
    LearningRouteRepository,
)


class RouteValidationError(ValueError):
    """路线业务规则校验失败。"""


class LearningRouteService:
    def __init__(self, route_repo: LearningRouteRepository, skill_repo=None):
        self.route_repo = route_repo
        self.skill_repo = skill_repo

    # ================= 创建 =================

    def create_group(
        self,
        name: str,
        parent_id: int | None = None,
        description: str = "",
        goal: str = "",
        priority: int = 3,
    ) -> LearningRoute:
        return self._create(
            name, parent_id=parent_id, description=description, goal=goal,
            route_type=ROUTE_TYPE_GROUP, priority=priority, planning_enabled=False,
        )

    def create_learning_route(
        self,
        name: str,
        parent_id: int | None = None,
        description: str = "",
        goal: str = "",
        priority: int = 3,
        planning_enabled: bool = True,
    ) -> LearningRoute:
        return self._create(
            name, parent_id=parent_id, description=description, goal=goal,
            route_type=ROUTE_TYPE_LEARNING, priority=priority,
            planning_enabled=planning_enabled,
        )

    def _create(
        self,
        name: str,
        parent_id: int | None,
        description: str,
        goal: str,
        route_type: str,
        priority: int,
        planning_enabled: bool,
    ) -> LearningRoute:
        name = (name or "").strip()
        if not name:
            raise RouteValidationError("路线名称不能为空")
        self._validate_priority(priority)
        if parent_id is not None:
            parent = self.route_repo.get(parent_id)
            if parent is None:
                raise RouteValidationError(f"父路线不存在: id={parent_id}")
            if parent.is_archived:
                raise RouteValidationError("不能把路线挂到已归档的父路线下")
        # 同级重名（不同父可同名；root 也要检查，因 SQLite UNIQUE(NULL) 不生效）
        if self.route_repo.get_by_name_under_parent(name, parent_id) is not None:
            raise RouteValidationError(f"同一父路线下已存在同名路线: {name!r}")
        return self.route_repo.create(
            name=name,
            parent_id=parent_id,
            description=description,
            goal=goal,
            route_type=route_type,
            priority=priority,
            planning_enabled=planning_enabled,
            source=ROUTE_SOURCE_MANUAL,
        )

    # ================= 树 / 读取 =================

    def list_tree(self) -> list[LearningRoute]:
        """返回完整路线树（root -> children 递归）。"""
        def build(route: LearningRoute) -> LearningRoute:
            route.children = [
                build(child) for child in self.route_repo.list_children(route.id)
            ]
            return route

        return [build(root) for root in self.route_repo.list_roots()]

    def list_learning_routes(self, status: str | None = None) -> list[LearningRoute]:
        return self.route_repo.list_learning_routes(status=status)

    def get(self, route_id: int) -> LearningRoute | None:
        return self.route_repo.get(route_id)

    def get_default_learning_route(self) -> LearningRoute | None:
        """[DEPRECATED / legacy] 旧默认路线（搜广推 + LLM）。

        新体系是 Multi-Route，请改用 :meth:`get_route_by_key` /
        :meth:`get_canonical_routes`；本方法仅供 legacy 兼容。
        """
        return self.route_repo.get_default_learning_route()

    # ================= canonical route（Phase 1） =================

    def get_route_by_key(self, route_key: str) -> LearningRoute | None:
        """按稳定 route_key 获取系统路线（canonical / legacy）。"""
        return self.route_repo.get_by_key(route_key)

    def get_canonical_routes(self) -> list[LearningRoute]:
        """按 R1–R6 固定顺序返回 canonical learning routes（缺失的跳过）。"""
        from .canonical_routes import CANONICAL_LEARNING_KEYS

        return self.route_repo.list_by_keys(list(CANONICAL_LEARNING_KEYS))

    def get_job_prep_group(self) -> LearningRoute | None:
        from .canonical_routes import ROUTE_KEY_JOB_PREP

        return self.route_repo.get_by_key(ROUTE_KEY_JOB_PREP)

    # ================= 父子关系 =================

    def set_parent(self, route_id: int, parent_id: int | None) -> LearningRoute:
        route = self._require(route_id)
        if parent_id is not None:
            parent = self.route_repo.get(parent_id)
            if parent is None:
                raise RouteValidationError(f"父路线不存在: id={parent_id}")
            if self._would_create_cycle(route_id, parent_id):
                raise RouteValidationError("不能把节点挂到自己的子孙下（会形成环）")
        if self.route_repo.get_by_name_under_parent(route.name, parent_id) is not None:
            raise RouteValidationError(f"同一父路线下已存在同名路线: {route.name!r}")
        return self.route_repo.update(route_id, parent_id=parent_id)

    def _walk_up(self, route_id: int) -> list[int]:
        """从 route_id 向上走到 root，返回经过的 id 列表（防环保险）。"""
        seen: list[int] = []
        current: int | None = route_id
        guard = 0
        while current is not None and guard < 1000:
            if current in seen:
                break
            seen.append(current)
            route = self.route_repo.get(current)
            if route is None:
                break
            current = route.parent_id
            guard += 1
        return seen

    def _would_create_cycle(self, route_id: int, parent_id: int) -> bool:
        """如果把 route_id 的 parent 设为 parent_id，是否会形成环。"""
        if route_id == parent_id:
            return True
        return route_id in self._walk_up(parent_id)

    # ================= priority =================

    @staticmethod
    def _validate_priority(priority: int) -> int:
        try:
            value = int(priority)
        except (TypeError, ValueError):
            raise RouteValidationError(f"priority 必须是整数: {priority!r}")
        if not (PRIORITY_MIN <= value <= PRIORITY_MAX):
            raise RouteValidationError(
                f"priority 必须在 {PRIORITY_MIN}~{PRIORITY_MAX} 之间"
            )
        return value

    def set_priority(self, route_id: int, priority: int) -> LearningRoute:
        self._require(route_id)
        value = self._validate_priority(priority)
        return self.route_repo.set_priority(route_id, value)

    # ================= 暂停 / 恢复自动规划 =================

    def pause_planning(self, route_id: int) -> LearningRoute:
        """只停止给该路线生成新知识；不改 status / progress / mastery / review。"""
        self._require(route_id)
        return self.route_repo.set_planning_enabled(route_id, False)

    def resume_planning(self, route_id: int) -> LearningRoute:
        """恢复自动规划：仅 active 的 learning route 可恢复；group 不参与 Planner。"""
        route = self._require(route_id)
        if route.route_type != ROUTE_TYPE_LEARNING:
            raise RouteValidationError("只有 learning route 才能恢复自动规划")
        if route.status != ROUTE_STATUS_ACTIVE:
            raise RouteValidationError("已归档路线不能直接恢复自动规划（请先 restore）")
        return self.route_repo.set_planning_enabled(route_id, True)

    # ================= 归档 / 恢复 =================

    def update_route_info(
        self,
        route_id: int,
        name: str | None = None,
        goal: str | None = None,
        description: str | None = None,
        priority: int | None = None,
    ) -> LearningRoute:
        """更新路线基础信息（名称 / 目标 / 描述 / 优先级）。"""
        route = self._require(route_id)
        fields: dict = {}
        if name is not None:
            clean = name.strip()
            if not clean:
                raise RouteValidationError("路线名称不能为空")
            if clean != route.name and self.route_repo.get_by_name_under_parent(
                clean, route.parent_id
            ) is not None:
                raise RouteValidationError(f"同一父路线下已存在同名路线: {clean!r}")
            fields["name"] = clean
        if goal is not None:
            fields["goal"] = goal
        if description is not None:
            fields["description"] = description
        if priority is not None:
            fields["priority"] = self._validate_priority(priority)
        if not fields:
            return route
        return self.route_repo.update(route_id, **fields)

    def archive_route(self, route_id: int) -> LearningRoute:
        """归档：status=archived + planning_enabled=0 + archived_at；不删任何数据。

        group route：如果还有未归档子路线，拒绝归档（避免结构矛盾）。
        """
        route = self._require(route_id)
        if route.route_type == ROUTE_TYPE_GROUP:
            active_children = [
                c for c in self.route_repo.list_children(route_id)
                if c.status != ROUTE_STATUS_ARCHIVED
            ]
            if active_children:
                names = "、".join(c.name for c in active_children)
                raise RouteValidationError(
                    f"该分组下仍有未归档子路线（{names}），请先处理子路线"
                )
        return self.route_repo.archive(route_id)

    def restore_route(self, route_id: int) -> LearningRoute:
        """恢复为 active，但 planning_enabled 保持 0（需显式恢复自动规划）。"""
        self._require(route_id)
        return self.route_repo.restore(route_id)

    # ================= route <-> skill =================

    def assign_skill(self, route_id: int, skill_id: int) -> bool:
        self._require(route_id)
        if self.skill_repo is not None and self.skill_repo.get(skill_id) is None:
            raise RouteValidationError(f"技能不存在: id={skill_id}")
        return self.route_repo.assign_skill(route_id, skill_id)

    def unassign_skill(self, route_id: int, skill_id: int) -> bool:
        self._require(route_id)
        return self.route_repo.unassign_skill(route_id, skill_id)

    def get_route_skills(self, route_id: int) -> list[dict]:
        self._require(route_id)
        if self.skill_repo is None:
            return []
        out = []
        for skill_id in self.route_repo.list_skill_ids(route_id):
            skill = self.skill_repo.get(skill_id)
            if skill is not None:
                out.append(skill)
        return out

    # ================= 内部 =================

    def _require(self, route_id: int) -> LearningRoute:
        route = self.route_repo.get(route_id)
        if route is None:
            raise RouteValidationError(f"路线不存在: id={route_id}")
        return route
