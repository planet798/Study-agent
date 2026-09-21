"""PracticeReadinessService（Phase 6）。

职责：把 PracticeProject 的 Learning Requirement 与当前 Capability / 课程结构
比对，回答两个问题：

1. Project Readiness：这个项目还缺哪些能力？（只读、可展示）
2. Planner Actionable：哪些缺口真的能变成“今天的学习任务”？

职责边界（重要）：
- 本 Service 不做 Route 调度（GlobalDailyScheduler 负责），不改 route.priority；
- 不写任何 capability evidence，不修改 mastery / review；
- 不重复实现 prerequisite gate：通过注入的 blocked_topic_provider 读取；
- 不做 legal gate：`legal_topics` 由调用方（现有 route planner）决定。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..database.capability_repository import CapabilityEvidenceRepository
from ..database.practice_repository import (
    PracticeProjectRepository,
    PracticeRequirementRepository,
)
from ..database.repository import TaskRepository
from ..database.learning_route_repository import LearningRouteRepository
from ..database.study_plan_repository import StudyPlanRepository
from ..database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_NOT_DONE
from ..utils.date_utils import today as _today
from .capability import (
    AWARE,
    EXPERIMENT,
    UNLEARNED,
    capability_label,
    capability_name,
)
from .practice import (
    STATUS_ARCHIVED,
    STATUS_IN_PROGRESS,
    requirement_target_label,
)

# ---------- reason codes（§17） ----------

REASON_SATISFIED = "satisfied"
REASON_PROJECT_NOT_IN_PROGRESS = "project_not_in_progress"
REASON_ROUTE_PAUSED = "route_paused"
REASON_ROUTE_ARCHIVED = "route_archived"
REASON_TOPIC_NOT_CURRENTLY_AVAILABLE = "topic_not_currently_available"
REASON_PREREQUISITE_BLOCKED = "prerequisite_blocked"
REASON_HAS_ACTIVE_TASK = "has_active_task"
REASON_CANCELLED_TODAY = "cancelled_today"
REASON_NO_INCOMPLETE_COMPONENT = "no_incomplete_component"
REASON_NEEDS_ASSESSMENT = "needs_assessment"
REASON_NEEDS_EXPERIMENT_EVIDENCE = "needs_experiment_evidence"
REASON_ACTIONABLE = "actionable_learning_component"

REASON_LABELS: dict[str, str] = {
    REASON_SATISFIED: "已满足",
    REASON_PROJECT_NOT_IN_PROGRESS: "项目未在进行中",
    REASON_ROUTE_PAUSED: "路线已暂停自动规划",
    REASON_ROUTE_ARCHIVED: "路线已归档",
    REASON_TOPIC_NOT_CURRENTLY_AVAILABLE: "Topic 不在当前阶段（等待前置课程）",
    REASON_PREREQUISITE_BLOCKED: "前置技能未满足",
    REASON_HAS_ACTIVE_TASK: "今天已有同 Topic 任务",
    REASON_CANCELLED_TODAY: "今天已移除过该 Topic",
    REASON_NO_INCOMPLETE_COMPONENT: "没有可安排的下一学习活动",
    REASON_NEEDS_ASSESSMENT: "课程活动已完成，建议进行验收",
    REASON_NEEDS_EXPERIMENT_EVIDENCE: "实验活动已完成，需要实验成果证据",
    REASON_ACTIONABLE: "可安排下一学习活动",
}


def reason_label(code: str | None) -> str:
    return REASON_LABELS.get(code or "", code or "")


@dataclass
class PracticeRequirementStatus:
    requirement_id: int
    project_id: int
    project_name: str
    project_status: str
    topic_id: int
    topic_name: str
    route_id: int | None
    route_name: str
    target_capability_level: int
    target_capability_label: str
    current_capability_level: int
    current_capability_label: str
    capability_gap: int
    satisfied: bool
    planner_actionable: bool = False
    next_component_id: int | None = None
    next_activity_kind: str = ""
    next_activity_label: str = ""
    reason_code: str = REASON_SATISFIED
    note: str = ""

    @property
    def reason_label(self) -> str:
        return reason_label(self.reason_code)

    def to_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_status": self.project_status,
            "topic_id": self.topic_id,
            "topic_name": self.topic_name,
            "route_id": self.route_id,
            "route_name": self.route_name,
            "target_capability_level": self.target_capability_level,
            "target_capability_label": self.target_capability_label,
            "current_capability_level": self.current_capability_level,
            "current_capability_label": self.current_capability_label,
            "capability_gap": self.capability_gap,
            "satisfied": self.satisfied,
            "planner_actionable": self.planner_actionable,
            "next_component_id": self.next_component_id,
            "next_activity_kind": self.next_activity_kind,
            "next_activity_label": self.next_activity_label,
            "reason_code": self.reason_code,
            "reason_label": reason_label(self.reason_code),
            "note": self.note,
        }


class PracticeReadinessService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        requirement_repo: PracticeRequirementRepository | None = None,
        project_repo: PracticeProjectRepository | None = None,
        plan_repo: StudyPlanRepository | None = None,
        route_repo: LearningRouteRepository | None = None,
        capability_service=None,
        capability_repo: CapabilityEvidenceRepository | None = None,
        topic_learning_service=None,
        task_repo: TaskRepository | None = None,
        current_phase_provider: Callable[[int, str], object | None] | None = None,
        blocked_topic_provider: Callable[[int, str], set] | None = None,
    ):
        self.conn = conn
        self.requirements = requirement_repo or PracticeRequirementRepository(conn)
        self.projects = project_repo or PracticeProjectRepository(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)
        self.route_repo = route_repo or LearningRouteRepository(conn)
        self.capability_service = capability_service
        self.capability_repo = (
            capability_repo
            or getattr(capability_service, "repo", None)
            or CapabilityEvidenceRepository(conn)
        )
        self.topic_learning_service = topic_learning_service
        self.task_repo = task_repo or TaskRepository(conn)
        self._phase_provider = current_phase_provider
        self._blocked_provider = blocked_topic_provider

    # ================= Requirement CRUD（薄封装，保证只有 linked topic 能配置） =================

    def set_requirement(
        self,
        project_id: int,
        topic_id: int,
        target_capability_level: int,
        note: str = "",
    ) -> dict:
        """创建/更新 requirement；Topic 必须已关联该项目（不自动建立关联）。"""
        from .practice import is_valid_requirement_target

        if not is_valid_requirement_target(target_capability_level):
            raise ValueError(
                "目标能力等级只能是 1~4（不能选择 PROJECT）"
            )
        if self.projects.get(project_id) is None:
            raise ValueError("project_not_found")
        if int(topic_id) not in self.projects.list_topic_ids(project_id):
            raise ValueError("topic_not_linked_to_project")
        return self.requirements.create_or_update(
            project_id=project_id,
            topic_id=topic_id,
            target_capability_level=int(target_capability_level),
            note=note,
        )

    def deactivate_requirement(self, requirement_id: int) -> Optional[dict]:
        return self.requirements.deactivate(requirement_id)

    def list_project_requirements(
        self, project_id: int, plan_date: str | None = None
    ) -> list[PracticeRequirementStatus]:
        plan_date = plan_date or _today()
        out = []
        for req in self.requirements.list_by_project(project_id, active_only=True):
            out.append(self.get_status(req, plan_date=plan_date))
        return out

    def get_requirement(self, project_id: int, topic_id: int) -> Optional[dict]:
        return self.requirements.get(project_id, topic_id)

    # ================= Readiness =================

    def _current_level(self, topic_id: int) -> int:
        kp = self.conn.execute(
            "SELECT id FROM knowledge_points WHERE topic_id = ? "
            "ORDER BY id ASC LIMIT 1",
            (int(topic_id),),
        ).fetchone()
        if kp is None:
            return UNLEARNED
        return int(self.capability_repo.max_level_by_kp(int(kp[0])))

    def _resolve_phase_topics(self, route_id: int, plan_date: str) -> set[int]:
        if self._phase_provider is not None:
            try:
                phase = self._phase_provider(route_id, plan_date)
            except Exception:  # noqa: BLE001
                phase = None
            if phase is None:
                return set()
            return {int(t.id) for t in (getattr(phase, "topics", None) or [])}
        # 兜底：按日期锚定的 phase（不做 curriculum 自动推进）
        try:
            plan = self.plan_repo.get_plan_by_route(int(route_id))
            if plan is None:
                return set()
            topics: set[int] = set()
            for phase in self.plan_repo.list_phases(plan.id):
                if phase.start_date <= plan_date <= (phase.end_date or phase.start_date):
                    for t in self.plan_repo.list_topics(phase.id):
                        topics.add(int(t.id))
            return topics
        except Exception:  # noqa: BLE001
            return set()

    def _blocked_topics(self, route_id: int, plan_date: str) -> set[int]:
        if self._blocked_provider is None:
            return set()
        try:
            return {int(x) for x in self._blocked_provider(route_id, plan_date)}
        except Exception:  # noqa: BLE001
            return set()

    def _same_day_conflict(
        self, topic_id: int, route_id: int | None, plan_date: str
    ) -> str:
        try:
            tasks = self.task_repo.list_by_date(plan_date)
        except Exception:  # noqa: BLE001
            return ""
        for t in tasks:
            if t.topic_id != int(topic_id):
                continue
            if route_id is not None and t.route_id not in (None, int(route_id)):
                continue
            if t.status == STATUS_CANCELLED:
                return REASON_CANCELLED_TODAY
            if t.status in (STATUS_ACTIVE, STATUS_NOT_DONE):
                return REASON_HAS_ACTIVE_TASK
        return ""

    def get_status(
        self, requirement: dict, plan_date: str | None = None
    ) -> PracticeRequirementStatus:
        plan_date = plan_date or _today()
        project = self.projects.get(requirement["project_id"]) or {}
        topic_id = int(requirement["topic_id"])
        topic = self.plan_repo.get_topic(topic_id)
        topic_name = getattr(topic, "name", "") or ""
        route_id = self.plan_repo.get_route_id_for_topic(topic_id)
        route = self.route_repo.get(route_id) if route_id is not None else None
        route_name = getattr(route, "name", "") or ""
        target = int(requirement["target_capability_level"])
        current = self._current_level(topic_id)
        gap = max(0, target - current)
        satisfied = current >= target

        status = PracticeRequirementStatus(
            requirement_id=int(requirement["id"]),
            project_id=int(requirement["project_id"]),
            project_name=project.get("name", ""),
            project_status=project.get("status", ""),
            topic_id=topic_id,
            topic_name=topic_name,
            route_id=route_id,
            route_name=route_name,
            target_capability_level=target,
            target_capability_label=requirement_target_label(target),
            current_capability_level=current,
            current_capability_label=capability_label(current),
            capability_gap=gap,
            satisfied=satisfied,
            note=requirement.get("note") or "",
        )
        if satisfied:
            status.reason_code = REASON_SATISFIED
            return status

        if project.get("status") != STATUS_IN_PROGRESS:
            status.reason_code = REASON_PROJECT_NOT_IN_PROGRESS
            return status

        if route is None:
            status.reason_code = REASON_TOPIC_NOT_CURRENTLY_AVAILABLE
            return status
        if route.is_archived:
            status.reason_code = REASON_ROUTE_ARCHIVED
            return status
        if not route.planning_enabled:
            status.reason_code = REASON_ROUTE_PAUSED
            return status

        phase_topics = self._resolve_phase_topics(route_id, plan_date)
        if topic_id not in phase_topics:
            status.reason_code = REASON_TOPIC_NOT_CURRENTLY_AVAILABLE
            return status
        if topic_id in self._blocked_topics(route_id, plan_date):
            status.reason_code = REASON_PREREQUISITE_BLOCKED
            return status

        tl = self.topic_learning_service
        if tl is None or not tl.has_profile(topic_id):
            status.reason_code = REASON_NO_INCOMPLETE_COMPONENT
            return status

        component = tl.get_next_required_component(topic_id)
        if component is None:
            # 课程活动已完成，但 capability 仍有 gap → 不能重复生成学习任务
            if target >= EXPERIMENT and self._experiment_done(tl, topic_id):
                status.reason_code = REASON_NEEDS_EXPERIMENT_EVIDENCE
            else:
                status.reason_code = REASON_NEEDS_ASSESSMENT
            return status

        conflict = self._same_day_conflict(topic_id, route_id, plan_date)
        if conflict:
            status.reason_code = conflict
            return status

        from .learning_activity import activity_label

        status.next_component_id = int(component["id"])
        status.next_activity_kind = component["activity_kind"]
        status.next_activity_label = activity_label(component["activity_kind"])
        status.planner_actionable = True
        status.reason_code = REASON_ACTIONABLE
        return status

    def _experiment_done(self, tl, topic_id: int) -> bool:
        try:
            for c in tl.get_components(topic_id):
                if c["activity_kind"] == "experiment":
                    return bool(tl.is_component_complete(c["id"]))
        except Exception:  # noqa: BLE001
            return False
        return False

    # ================= 项目 / 路线视图 =================

    def get_project_readiness(
        self, project_id: int, plan_date: str | None = None
    ) -> dict:
        plan_date = plan_date or _today()
        project = self.projects.get(project_id) or {}
        statuses = self.list_project_requirements(project_id, plan_date=plan_date)
        satisfied = [s for s in statuses if s.satisfied]
        actionable = [s for s in statuses if s.planner_actionable]
        drives = project.get("status") == STATUS_IN_PROGRESS
        return {
            "project_id": int(project_id),
            "project_name": project.get("name", ""),
            "project_status": project.get("status", ""),
            "drives_planner": drives,
            "satisfied_count": len(satisfied),
            "total_count": len(statuses),
            "pending_count": len(statuses) - len(satisfied),
            "actionable_count": len(actionable),
            "statuses": statuses,
        }

    def list_route_blockers(
        self, route_id: int, plan_date: str | None = None
    ) -> list[PracticeRequirementStatus]:
        """该路线下所有未满足的 requirement（含 non-actionable），用于 UI。"""
        plan_date = plan_date or _today()
        out = []
        for req in self.requirements.list_active():
            topic_id = int(req["topic_id"])
            if self.plan_repo.get_route_id_for_topic(topic_id) != int(route_id):
                continue
            status = self.get_status(req, plan_date=plan_date)
            if status.satisfied:
                continue
            out.append(status)
        return out

    def list_actionable_blockers_by_route(
        self, route_id: int, plan_date: str | None = None
    ) -> list[PracticeRequirementStatus]:
        return [
            s for s in self.list_route_blockers(route_id, plan_date)
            if s.planner_actionable
        ]

    def list_project_statuses(
        self, plan_date: str | None = None
    ) -> dict[int, list[PracticeRequirementStatus]]:
        plan_date = plan_date or _today()
        out: dict[int, list[PracticeRequirementStatus]] = {}
        for req in self.requirements.list_active():
            out.setdefault(int(req["project_id"]), []).append(
                self.get_status(req, plan_date=plan_date)
            )
        return out

    # ================= 兼容别名（§77 API 命名） =================

    def list_project_requirements_status(
        self, project_id: int, plan_date: str | None = None
    ) -> list[PracticeRequirementStatus]:
        return self.list_project_requirements(project_id, plan_date)

    def get_requirement_status(
        self, requirement: dict, plan_date: str | None = None
    ) -> PracticeRequirementStatus:
        return self.get_status(requirement, plan_date)
