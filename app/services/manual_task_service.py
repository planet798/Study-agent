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
    ):
        self.repo = repo
        self.assessment_repo = assessment_repo
        # 复用 StudyPlanService.link_task_knowledge_point（topic -> kp 唯一实现）
        self.study_plan_service = study_plan_service

    # ================= 普通 To-do =================

    def create_todo(
        self,
        title: str,
        description: str = "",
        estimated_minutes: int = 0,
        scheduled_date: str | None = None,
        category: str = "学习",
        priority: int = 1,
    ) -> Task:
        """创建普通学习任务：不创建 knowledge_point，不进入验收链路。"""
        return self.repo.create(
            title=title,
            scheduled_date=scheduled_date,
            description=description,
            category=category,
            estimated_minutes=estimated_minutes,
            priority=priority,
            source="manual",
            task_type=MANUAL_TODO,
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
    ) -> Task:
        """创建正式知识学习任务（manual knowledge）。

        - 传入 topic_id：关联已有 study_topic，复用 link_task_knowledge_point
          建立 topic -> knowledge_point 关联；
        - 不传 topic_id：创建/复用一个独立临时知识点（topic_id=NULL），
          直接写在 task.knowledge_point_id 上；
        - 两种情况均可进入 Assessment（done ≠ mastered）。
        """
        if topic_id is not None:
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
            )
            if self.study_plan_service is not None:
                linked = self.study_plan_service.link_task_knowledge_point(task)
                if linked is not None:
                    task = linked
            return task

        kp_id = None
        if self.assessment_repo is not None:
            kp = self.assessment_repo.get_or_create_manual_knowledge_point(
                title, description
            )
            kp_id = kp["id"] if kp is not None else None
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
        )

    # ================= 幂等临时知识点 =================

    def get_or_create_manual_knowledge_point(
        self, name: str, description: str = ""
    ) -> dict | None:
        """按规范化名称幂等取得/创建临时知识点（无 assessment_repo 时返回 None）。"""
        if self.assessment_repo is None:
            return None
        return self.assessment_repo.get_or_create_manual_knowledge_point(
            name, description
        )
