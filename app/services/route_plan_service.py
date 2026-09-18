"""手动路线学习结构（StudyPlan / Phase / Topic）业务层（Phase C）。

用于“查看路线”里手动搭建学习结构：
- 为某条 learning route 创建最小 StudyPlan；
- 手动添加阶段 / 知识点；
- 安全删除（有历史 task / kp / assessment 的 topic 绝不物理删除）；
- 计算路线进度（已完成 Topic / 总 Topic）。

不生成 task、不调用 AI、不接多路线 Scheduler。
"""

from __future__ import annotations

from ..database.study_plan_repository import StudyPlan, StudyPlanRepository


class RouteStructureError(ValueError):
    """路线结构编辑校验失败。"""


class RoutePlanService:
    def __init__(self, plan_repo: StudyPlanRepository, task_repo,
                 assessment_repo=None):
        self.plan_repo = plan_repo
        self.task_repo = task_repo
        self.assessment_repo = assessment_repo

    # ================= Plan =================

    def get_plan(self, route_id: int) -> StudyPlan | None:
        return self.plan_repo.get_plan_by_route(route_id)

    def ensure_manual_plan(
        self,
        route_id: int,
        route_name: str,
        start_date: str = "",
        end_date: str = "",
    ) -> StudyPlan:
        """为路线创建最小 plan（幂等）。默认名 "<路线名称>学习计划"。"""
        existing = self.plan_repo.get_plan_by_route(route_id)
        if existing is not None:
            return existing
        return self.plan_repo.create_plan(
            name=f"{route_name}学习计划",
            description="手动创建的路线学习计划",
            start_date=start_date or "2026-09-01",
            end_date=end_date or "2099-12-31",
            route_id=route_id,
        )

    def get_structure(self, route_id: int) -> StudyPlan | None:
        plan = self.plan_repo.get_plan_by_route(route_id)
        if plan is None:
            return None
        return self.plan_repo.get_plan_with_phases(plan.id)

    # ================= Phase =================

    def add_phase(
        self,
        route_id: int,
        name: str,
        goal: str = "",
        order_index: int | None = None,
        description: str = "",
        start_date: str = "",
        end_date: str = "",
    ):
        name = (name or "").strip()
        if not name:
            raise RouteStructureError("阶段名称不能为空")
        plan = self.plan_repo.get_plan_by_route(route_id)
        if plan is None:
            raise RouteStructureError("该路线还没有学习计划，请先创建学习计划")
        if order_index is None:
            order_index = len(self.plan_repo.list_phases(plan.id)) + 1
        # 手动路线不强制绝对日期：未填时用 plan 范围兜底
        return self.plan_repo.create_phase(
            plan_id=plan.id,
            name=name,
            description=description,
            start_date=start_date or plan.start_date,
            end_date=end_date or plan.end_date,
            priority=1,
            goals=goal,
            order_index=int(order_index),
        )

    def delete_phase(self, phase_id: int) -> None:
        phase = self.plan_repo.get_phase(phase_id)
        if phase is None:
            raise RouteStructureError("阶段不存在")
        topics = self.plan_repo.list_topics(phase_id)
        if topics:
            raise RouteStructureError("该阶段下仍有知识点，请先清理知识点再删除阶段")
        self.plan_repo.delete_phase(phase_id)

    # ================= Topic =================

    def add_topic(
        self,
        route_id: int,
        phase_id: int,
        name: str,
        description: str = "",
        estimated_minutes: int = 30,
        priority: int = 1,
        order_index: int | None = None,
    ):
        name = (name or "").strip()
        if not name:
            raise RouteStructureError("知识点标题不能为空")
        phase = self.plan_repo.get_phase(phase_id)
        if phase is None:
            raise RouteStructureError("阶段不存在")
        plan = self.plan_repo.get_plan(phase.plan_id)
        if plan is None or plan.route_id != route_id:
            raise RouteStructureError("阶段不属于该路线，拒绝添加知识点")
        if order_index is None:
            order_index = len(self.plan_repo.list_topics(phase_id)) + 1
        return self.plan_repo.create_topic(
            phase_id=phase_id,
            name=name,
            description=description or name,
            estimated_minutes=int(estimated_minutes),
            priority=int(priority),
            order_index=int(order_index),
        )

    def topic_has_history(self, topic_id: int) -> bool:
        """Topic 是否已有学习记录（task 或 knowledge_point）。"""
        row = self.task_repo.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE topic_id = ?", (int(topic_id),)
        ).fetchone()
        if (row[0] or 0) > 0:
            return True
        row = self.task_repo.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE topic_id = ?",
            (int(topic_id),),
        ).fetchone()
        return (row[0] or 0) > 0

    def delete_topic(self, topic_id: int, route_id: int | None = None) -> None:
        """删除 Topic（仅当没有任何 task / kp / assessment 历史）。"""
        topic = self.plan_repo.get_topic(topic_id)
        if topic is None:
            raise RouteStructureError("知识点不存在")
        if route_id is not None:
            phase = self.plan_repo.get_phase(topic.phase_id)
            plan = self.plan_repo.get_plan(phase.plan_id) if phase else None
            if plan is None or plan.route_id != route_id:
                raise RouteStructureError("知识点不属于该路线")
        if self.topic_has_history(topic_id):
            raise RouteStructureError(
                "该知识点已有学习记录，不能直接删除。"
            )
        self.plan_repo.delete_topic(topic_id)

    # ================= 进度 =================

    def done_topic_ids(self) -> set[int]:
        """材料已覆盖的 topic（存在 done 学习任务），不等于 mastery。"""
        return {
            int(r[0]) for r in self.task_repo.conn.execute(
                "SELECT DISTINCT topic_id FROM tasks "
                "WHERE status = 'done' AND topic_id IS NOT NULL"
            ).fetchall()
        }

    def route_progress(self, route_id: int) -> dict:
        """已完成 Topic / 总 Topic（done task 视为材料已覆盖，不改 mastery）。"""
        structure = self.get_structure(route_id)
        if structure is None:
            return {"done": 0, "total": 0, "phases": 0, "has_plan": False}
        total = 0
        done_ids = self.done_topic_ids()
        done = 0
        for phase in structure.phases:
            for topic in phase.topics:
                total += 1
                if topic.id in done_ids:
                    done += 1
        return {
            "done": done,
            "total": total,
            "phases": len(structure.phases),
            "has_plan": True,
        }
