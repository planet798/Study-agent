"""路线进度 / 知识掌握（Phase E）。

统一在 service 层计算 route-scoped 进度，避免 UI/页面自己拼 SQL。

严格区分：
- 课程覆盖率 covered_topics / total_topics：done 学习任务 → 材料已覆盖；
- 掌握率 mastered / assessed：只有真实 Assessment evidence 才参与，
  没有验收数据时 mastery_rate = None（UI 显示“暂无验收数据”，不显示 0%）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..database.learning_route_repository import LearningRouteRepository
from .knowledge_evidence import (
    HIGH_MASTERY_THRESHOLD,
    WEAK_MASTERY_THRESHOLD,
    _attempt_weak_points,
    _latest_judged_attempt,
)

MASTERED_THRESHOLD = HIGH_MASTERY_THRESHOLD
WEAK_THRESHOLD = WEAK_MASTERY_THRESHOLD

# 知识状态语义（沿用现有 mastery 证据，不产生新算法）
STATUS_NOT_LEARNED = "未学习"
STATUS_LEARNED = "已学习 · 未验收"
STATUS_ASSESSED = "已验收"
STATUS_MASTERED = "已掌握"
STATUS_WEAK = "薄弱"


@dataclass
class KnowledgeStatus:
    knowledge_point_id: int | None
    name: str
    topic_id: int | None
    status: str
    mastery: float | None = None
    weak_points: list[str] = field(default_factory=list)
    last_assessed_at: str | None = None
    # Phase 3：capability（与 mastery 独立）
    capability_level: int = 0
    capability_label: str = ""
    has_capability_evidence: bool = False

    @property
    def mastery_percent(self) -> int | None:
        if self.mastery is None:
            return None
        return int(round(float(self.mastery) * 100))


@dataclass
class RouteProgress:
    route_id: int
    topic_total: int = 0
    topic_covered: int = 0
    assessment_evidence_count: int = 0
    mastered_count: int = 0
    weak_count: int = 0
    # Phase 2：学习活动完成度（不混入 mastery）
    activity_required_total: int = 0
    activity_required_done: int = 0
    activity_optional_total: int = 0
    activity_optional_done: int = 0
    # Phase 3：capability 证据统计（不计算平均 capability）
    capability_evidence_count: int = 0
    capability_level_counts: dict = field(
        default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    )
    knowledge: list[KnowledgeStatus] = field(default_factory=list)

    @property
    def has_assessment(self) -> bool:
        return self.assessment_evidence_count > 0

    @property
    def mastery_rate(self) -> float | None:
        """有验收证据的 kp 中达到 mastered 的比例；无证据返回 None。"""
        if self.assessment_evidence_count <= 0:
            return None
        return round(self.mastered_count / self.assessment_evidence_count, 4)

    @property
    def mastery_percent(self) -> int | None:
        rate = self.mastery_rate
        return None if rate is None else int(round(rate * 100))

    @property
    def coverage_percent(self) -> int:
        if self.topic_total <= 0:
            return 0
        return int(round(self.topic_covered / self.topic_total * 100))


class RouteProgressService:
    def __init__(self, repo, assessment_repo, plan_repo,
                 route_repo: LearningRouteRepository | None = None,
                 topic_learning_service=None, capability_service=None):
        self.repo = repo
        self.assessment_repo = assessment_repo
        self.plan_repo = plan_repo
        self.route_repo = route_repo
        self.topic_learning_service = topic_learning_service
        self.capability_service = capability_service

    # ================= 基础 =================

    def _done_topic_ids(self) -> set[int]:
        return {
            int(r[0]) for r in self.repo.conn.execute(
                "SELECT DISTINCT topic_id FROM tasks "
                "WHERE status = 'done' AND task_type != 'review' "
                "AND topic_id IS NOT NULL"
            ).fetchall()
        }

    def _complete_topic_ids(self) -> set[int]:
        """Component-aware curriculum 完成集合；无 service 时回退旧语义。"""
        if self.topic_learning_service is not None:
            try:
                return self.topic_learning_service.curriculum_complete_topic_ids()
            except Exception:  # noqa: BLE001
                pass
        return self._done_topic_ids()

    def route_topics(self, route_id: int) -> list:
        return self.plan_repo.list_topics_by_route(route_id)

    # ================= 单路线进度 =================

    def get_progress(self, route_id: int, today: str) -> RouteProgress:
        topics = self.route_topics(route_id)
        done = self._complete_topic_ids()
        progress = RouteProgress(route_id=route_id, topic_total=len(topics))
        progress.topic_covered = sum(1 for t in topics if t.id in done)
        if self.topic_learning_service is not None:
            act = self.topic_learning_service.route_activity_summary(route_id)
            progress.activity_required_total = act["required_total"]
            progress.activity_required_done = act["required_done"]
            progress.activity_optional_total = act["optional_total"]
            progress.activity_optional_done = act["optional_done"]
        if self.capability_service is not None:
            summary = self.capability_service.get_route_capability_summary(
                route_id
            )
            progress.capability_evidence_count = summary["total"]

        kps = self.assessment_repo.list_knowledge_points_by_route(route_id)
        topic_names = {t.id: t.name for t in topics}
        done_names = {topic_names[t.id] for t in topics if t.id in done}
        kp_by_topic = {
            kp["topic_id"]: kp for kp in kps if kp.get("topic_id") is not None
        }

        progress.assessment_evidence_count = sum(
            1 for kp in kps if kp.get("last_assessed_at")
        )
        progress.mastered_count = sum(
            1 for kp in kps
            if kp.get("last_assessed_at")
            and float(kp.get("mastery_estimate") or 0.0) >= MASTERED_THRESHOLD
        )
        progress.weak_count = sum(
            1 for kp in kps if self._is_weak(kp)
        )

        # 知识状态列表：先列 topic，再补无 topic 的 manual kp
        seen_kp_ids: set[int] = set()
        for topic in topics:
            kp = kp_by_topic.get(topic.id)
            if kp is not None:
                seen_kp_ids.add(kp["id"])
            progress.knowledge.append(self._knowledge_status(
                name=topic.name, topic_id=topic.id, kp=kp,
                covered=topic.id in done,
            ))
        for kp in kps:
            if kp["id"] in seen_kp_ids:
                continue
            progress.knowledge.append(self._knowledge_status(
                name=kp["name"], topic_id=kp.get("topic_id"), kp=kp,
                covered=kp["name"] in done_names,
            ))
        # Phase 3：附加 capability（与 mastery 独立展示）
        if self.capability_service is not None:
            from .capability import capability_label

            for ks in progress.knowledge:
                if ks.knowledge_point_id is None:
                    continue
                level = self.capability_service.get_current_level(
                    ks.knowledge_point_id
                )
                ks.capability_level = level
                ks.capability_label = capability_label(level) if level else ""
                ks.has_capability_evidence = level > 0
                if level > 0:
                    progress.capability_level_counts[level] = \
                        progress.capability_level_counts.get(level, 0) + 1
        return progress

    def _is_weak(self, kp: dict) -> bool:
        if not kp.get("last_assessed_at"):
            return False
        if float(kp.get("mastery_estimate") or 0.0) < WEAK_THRESHOLD:
            return True
        attempt = _latest_judged_attempt(self.assessment_repo, kp["id"])
        return bool(_attempt_weak_points(attempt))

    def _knowledge_status(self, name, topic_id, kp, covered) -> KnowledgeStatus:
        if kp is None:
            return KnowledgeStatus(
                knowledge_point_id=None, name=name, topic_id=topic_id,
                status=STATUS_LEARNED if covered else STATUS_NOT_LEARNED,
            )
        mastery = kp.get("mastery_estimate")
        mastered = bool(kp.get("last_assessed_at")) and \
            float(mastery or 0.0) >= MASTERED_THRESHOLD
        weak = self._is_weak(kp)
        if mastered:
            status = STATUS_MASTERED
        elif weak:
            status = STATUS_WEAK
        elif kp.get("last_assessed_at"):
            status = STATUS_ASSESSED
        elif covered:
            status = STATUS_LEARNED
        else:
            status = STATUS_NOT_LEARNED
        attempt = (_latest_judged_attempt(self.assessment_repo, kp["id"])
                   if kp.get("last_assessed_at") else None)
        return KnowledgeStatus(
            knowledge_point_id=kp["id"],
            name=name,
            topic_id=topic_id,
            status=status,
            mastery=float(mastery) if kp.get("last_assessed_at") else None,
            weak_points=_attempt_weak_points(attempt),
            last_assessed_at=kp.get("last_assessed_at"),
        )

    def weak_knowledge(self, route_id: int) -> list[dict]:
        return self.assessment_repo.list_weak_by_route(route_id, WEAK_THRESHOLD)

    # ================= 父路线聚合 =================

    def group_summary(self, route_id: int) -> dict:
        """父 group：只聚合子路线计数，不硬算总 mastery %。"""
        children = (
            self.route_repo.list_children(route_id)
            if self.route_repo is not None else []
        )
        return {
            "children_total": len(children),
            "active": sum(1 for c in children if not c.is_archived),
            "paused": sum(
                1 for c in children
                if not c.is_archived and not c.planning_enabled
            ),
            "archived": sum(1 for c in children if c.is_archived),
        }

    # ================= 月总结 route 维度 =================

    def monthly_route_stats(self, start: str, end: str) -> list[dict]:
        """本月按路线（含“未分类”桶）的统计；只基于真实数据。"""
        routes = []
        if self.route_repo is not None:
            routes = self.route_repo.list_learning_routes()
        buckets: list[tuple[int | None, str]] = [
            (r.id, r.name) for r in routes
        ]
        buckets.append((None, "未分类"))

        out: list[dict] = []
        for route_id, name in buckets:
            tasks = [
                t for t in self.repo.list_between(start, end)
                if t.route_id == route_id and t.task_type != "review"
            ]
            done_tasks = sum(1 for t in tasks if t.status == "done")
            assessed = self.assessment_repo.list_assessed_by_route(route_id)
            mastered = self.assessment_repo.list_mastered_by_route(
                route_id, MASTERED_THRESHOLD
            )
            weak = self.assessment_repo.list_weak_by_route(route_id, WEAK_THRESHOLD)
            covered = self._covered_topics_in_route(route_id)
            if not tasks and not assessed and not covered:
                continue  # 本月无任何活动/证据的桶不占位（含空“未分类”）
            activity = {"required_total": 0, "required_done": 0}
            if self.topic_learning_service is not None and route_id is not None:
                try:
                    activity = self.topic_learning_service.\
                        route_activity_summary(route_id)
                except Exception:  # noqa: BLE001
                    activity = {"required_total": 0, "required_done": 0}
            out.append({
                "route_id": route_id,
                "route_name": name,
                "done_tasks": done_tasks,
                "total_tasks": len(tasks),
                "covered_topics": covered,
                "assessment_evidence_count": len(assessed),
                "mastered_count": len(mastered),
                "weak_count": len(weak),
                "weak_topics": [kp["name"] for kp in weak],
                # Phase 2：学习活动完成度（不是 capability）
                "activity_required": activity.get("required_total", 0),
                "activity_completed": activity.get("required_done", 0),
            })
        return out

    def _covered_topics_in_route(self, route_id: int | None) -> int:
        if route_id is None:
            return 0
        done = self._complete_topic_ids()
        return sum(1 for t in self.route_topics(route_id) if t.id in done)
