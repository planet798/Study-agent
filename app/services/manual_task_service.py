"""手动添加今日学习任务（Phase A）。

解决“今日学习任务不能只依赖 Agent 自动规划”：用户可以在当天主动添加：

1. 普通学习任务（manual todo）
   - source='manual'、task_type='manual'；
   - 不关联 topic / knowledge_point；
   - 不进入 Assessment / mastery / Review 链路。

2. 正式知识学习任务（manual knowledge）
   - source='manual'、task_type='new'（复用现有“新知识”语义）；
   - 可关联已有 study_topic（复用 topic -> knowledge_point 的安全幂等实现）；
   - 或新建“临时知识点”（knowledge_points，topic_id=NULL，不污染 study_phases）；
   - 完成后仍必须 Assessment 才形成掌握证据（done ≠ mastered）。

本阶段不引入 learning_routes / 多路线模型。
"""

from __future__ import annotations

from ..database.repository import Task, TaskRepository

MANUAL_TODO = "manual"
MANUAL_KNOWLEDGE = "new"


class ManualTaskService:
    def __init__(
        self,
        repo: TaskRepository,
        assessment_repo=None,
        study_plan_service=None,
        topic_learning_service=None,
    ):
        self.repo = repo
        self.assessment_repo = assessment_repo
        # 复用 StudyPlanService.link_task_knowledge_point（topic -> kp 唯一实现）
        self.study_plan_service = study_plan_service
        # Phase 2：Topic Learning Activity
        self.topic_learning_service = topic_learning_service

    def _tl(self):
        if self.topic_learning_service is None:
            from ..database.topic_learning_repository import (
                TopicLearningComponentRepository,
            )
            from .topic_learning_profile_service import (
                TopicLearningProfileService,
            )

            self.topic_learning_service = TopicLearningProfileService(
                self.repo.conn,
                TopicLearningComponentRepository(self.repo.conn),
            )
        return self.topic_learning_service

    def resolve_activity(
        self,
        topic_id: int | None,
        component_id: int | None,
        activity_kind: str | None,
    ) -> tuple[int | None, str | None]:
        """解析 (component_id, activity_kind)，并校验一致性。

        - 显式 component_id：必须属于 topic_id，activity_kind 跟随 component。
        - 仅 activity_kind（无 component）：自定义学习方式（component_id=None）。
        - 都不给且 topic 有 profile：默认 = topic 的 next required component。
        """
        from .learning_activity import is_valid_activity_kind

        if activity_kind is not None and not is_valid_activity_kind(activity_kind):
            raise ValueError(f"非法 learning_activity_kind: {activity_kind!r}")
        if component_id is not None:
            comp = self._tl().get_component(component_id)
            if comp is None:
                raise ValueError(f"学习活动不存在: id={component_id}")
            if topic_id is None or comp["topic_id"] != topic_id:
                raise ValueError("学习活动不属于该 Topic")
            return comp["id"], comp["activity_kind"]
        if activity_kind is not None:
            return None, activity_kind
        if topic_id is not None:
            comp = self._tl().get_next_required_component(topic_id)
            if comp is not None:
                return comp["id"], comp["activity_kind"]
        return None, None

    # ================= 普通 To-do =================

    def create_todo(
        self,
        title: str,
        description: str = "",
        estimated_minutes: int = 0,
        scheduled_date: str | None = None,
        category: str = "学习",
        priority: int = 1,
        route_id: int | None = None,
    ) -> Task:
        """创建普通学习任务：不创建 knowledge_point，不进入验收链路。

        route_id 仅用于组织 / 筛选 / 统计（可为 NULL = 未分类 / 指定任意 active route）。
        """
        return self.repo.create(
            title=title,
            scheduled_date=scheduled_date,
            description=description,
            category=category,
            estimated_minutes=estimated_minutes,
            priority=priority,
            source="manual",
            task_type=MANUAL_TODO,
            route_id=route_id,
        )

    # ================= 正式知识学习任务 =================

    def create_knowledge_task(
        self,
        title: str,
        description: str = "",
        estimated_minutes: int = 0,
        scheduled_date: str | None = None,
        topic_id: int | None = None,
        category: str = "学习",
        priority: int = 1,
        route_id: int | None = None,
        component_id: int | None = None,
        learning_activity_kind: str | None = None,
    ) -> Task:
        """创建正式知识学习任务（manual knowledge）。

        - 传入 topic_id：关联已有 study_topic，task.route_id 强制等于该 topic 的
          route（不接受调用方覆盖）；复用 link_task_knowledge_point。
        - component_id / learning_activity_kind：见 resolve_activity。
        - 不传 topic_id：创建/复用一个 (name, route_id) 维度的临时知识点，
          task.route_id = kp.route_id；component_id 必须为 None。
        - 两种情况均可进入 Assessment（done ≠ mastered）。
        """
        component_id, learning_activity_kind = self.resolve_activity(
            topic_id, component_id, learning_activity_kind
        )
        if topic_id is not None:
            topic_route_id = None
            if self.study_plan_service is not None:
                topic_route_id = self.study_plan_service.plan_repo \
                    .get_route_id_for_topic(topic_id)
            task = self.repo.create(
                title=title,
                scheduled_date=scheduled_date,
                description=description,
                category=category,
                estimated_minutes=estimated_minutes,
                priority=priority,
                source="manual",
                task_type=MANUAL_KNOWLEDGE,
                topic_id=topic_id,
                route_id=topic_route_id,
                component_id=component_id,
                learning_activity_kind=learning_activity_kind,
            )
            if self.study_plan_service is not None:
                linked = self.study_plan_service.link_task_knowledge_point(task)
                if linked is not None:
                    task = linked
            return task

        kp_id = None
        kp_route_id = route_id
        if self.assessment_repo is not None:
            kp = self.assessment_repo.get_or_create_manual_knowledge_point(
                title, description, route_id=route_id
            )
            kp_id = kp["id"] if kp is not None else None
            kp_route_id = kp.get("route_id") if kp is not None else route_id
        return self.repo.create(
            title=title,
            scheduled_date=scheduled_date,
            description=description,
            category=category,
            estimated_minutes=estimated_minutes,
            priority=priority,
            source="manual",
            task_type=MANUAL_KNOWLEDGE,
            knowledge_point_id=kp_id,
            route_id=kp_route_id,
            component_id=None,
            learning_activity_kind=learning_activity_kind,
        )

    # ================= 幂等临时知识点 =================

    def get_or_create_manual_knowledge_point(
        self, name: str, description: str = "", route_id: int | None = None
    ) -> dict | None:
        """按 (规范化名称, route_id) 幂等取得/创建临时知识点。"""
        if self.assessment_repo is None:
            return None
        return self.assessment_repo.get_or_create_manual_knowledge_point(
            name, description, route_id=route_id
        )
