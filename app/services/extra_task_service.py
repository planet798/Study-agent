"""额外学习任务服务（Phase 5）。

设计目标：用户在当天正式任务全部完成后仍有时间时，可以主动请求“额外学习”。
- 额外任务来源优先围绕：当前阶段核心技能、薄弱知识点、当前学习内容；
  不越出职业路线。
- 严格与正式任务隔离：task_type='extra'、source='extra'，不推进
  study phase/topic，不影响第二天自动规划（跨天后未完成 extra 会像
  普通任务一样被日期切换归档）。
- 支持难度：basic(基础巩固) / practice(实践) / challenge(挑战)。
- 每日上限、同日去重、未完成任务去重。
"""

from __future__ import annotations

from ..database.assessment_repository import AssessmentRepository
from ..database.repository import TaskRepository
from ..database.schema import STATUS_DONE
from ..utils.date_utils import today as _today

# 默认每日额外任务上限（可配置，不硬编码在调用处）
DEFAULT_MAX_DAILY_EXTRA = 2

# 难度标签与建议时长
DIFFICULTY_LABELS = {
    "basic": "基础巩固",
    "practice": "实践",
    "challenge": "挑战",
}
DIFFICULTY_MINUTES = {
    "basic": 15,
    "practice": 30,
    "challenge": 45,
}
_DIFFICULTIES = tuple(DIFFICULTY_LABELS)

# 薄弱知识点阈值：mastery_estimate 低于该值视为薄弱
_WEAK_MASTERY_THRESHOLD = 0.5


class ExtraTaskService:
    def __init__(
        self,
        repo: TaskRepository,
        study_plan_service=None,
        assessment_repo: AssessmentRepository | None = None,
        max_daily_extra: int = DEFAULT_MAX_DAILY_EXTRA,
    ):
        self.repo = repo
        # 可选：用于读取当前阶段核心技能主题
        self.study_plan_service = study_plan_service
        # 可选：用于读取薄弱知识点
        self.assessment_repo = assessment_repo
        self.max_daily_extra = max_daily_extra

    # ================= 候选来源 =================

    def _candidate_sources(self, today: str) -> list[dict]:
        """构建候选（薄弱知识点优先 → 当前阶段主题）。"""

        candidates: list[dict] = []
        seen: set[tuple] = set()

        def _add(kind: str, ref_id: int, title: str,
                 route_id: int | None = None) -> None:
            key = (kind, ref_id)
            if key not in seen:
                seen.add(key)
                candidates.append(
                    {"kind": kind, "ref_id": ref_id, "title": title,
                     "route_id": route_id}
                )

        # 1) 薄弱知识点（已有验收证据且掌握度较低）
        if self.assessment_repo is not None:
            weak = [
                kp for kp in self.assessment_repo.list_knowledge_points()
                if kp.get("last_assessed_at")
                and (kp.get("mastery_estimate") or 0.0) < _WEAK_MASTERY_THRESHOLD
            ]
            weak.sort(key=lambda kp: (kp.get("mastery_estimate") or 0.0))
            for kp in weak[:3]:
                _add("knowledge_point", kp["id"], kp["name"],
                     route_id=kp.get("route_id"))

        # 2) 当前阶段核心技能主题（按阶段主题顺序）
        if self.study_plan_service is not None:
            phase = self.study_plan_service.get_current_phase(today)
            if phase is not None:
                for topic in phase.topics:
                    route_id = None
                    try:
                        route_id = self.study_plan_service.plan_repo \
                            .get_route_id_for_topic(topic.id)
                    except Exception:  # noqa: BLE001 - 推导失败保持 NULL
                        route_id = None
                    if route_id is None:
                        try:
                            route_id = self.study_plan_service._resolved_route_id()
                        except Exception:  # noqa: BLE001
                            route_id = None
                    _add("topic", topic.id, topic.name, route_id=route_id)

        # 3) 最近在学的知识点（无薄弱时兜底）
        if self.assessment_repo is not None:
            all_kp = self.assessment_repo.list_knowledge_points()
            if all_kp:
                latest = all_kp[-1]
                _add("knowledge_point", latest["id"], latest["name"],
                     route_id=latest.get("route_id"))

        return candidates

    # ================= 去重 =================

    def _source_has_unfinished_extra(self, cand: dict) -> bool:
        """该候选来源是否已存在未完成的 extra 任务。"""
        ref_id = cand["ref_id"]
        if cand["kind"] == "knowledge_point":
            tasks = self.repo.list_by_knowledge_point(ref_id)
        else:
            tasks = self.repo.list_by_topic_id(ref_id)
        return any(t.task_type == "extra" and t.status != STATUS_DONE for t in tasks)

    def _source_has_extra_on_date(self, cand: dict, date_str: str) -> bool:
        """该候选来源当天是否已生成过 extra 任务。"""
        for t in self.repo.list_by_date(date_str):
            if t.task_type != "extra":
                continue
            if t.title == cand["title"]:
                return True
            if cand["kind"] == "knowledge_point" and t.knowledge_point_id == cand["ref_id"]:
                return True
            if cand["kind"] == "topic" and t.topic_id == cand["ref_id"]:
                return True
        return False

    # ================= 生成 =================

    def _existing_extra_count(self, date_str: str) -> int:
        """当天已经存在的 extra 任务数量（task_type='extra' 且日期==today）。"""
        return sum(
            1 for t in self.repo.list_by_date(date_str) if t.task_type == "extra"
        )

    def generate_extra_tasks(
        self,
        today: str | None = None,
        difficulty: str = "practice",
    ) -> dict:
        """为用户主动请求生成当天额外任务。

        每日上限按“当天累计”计算：
        remaining = max_daily_extra - 当天已有 extra 数；
        本词最多只生成 remaining 个；remaining <= 0 直接返回预算耗尽。

        :return: {"created": [Task], "skipped_duplicate": [cand_title],
                  "skipped_budget": [cand_title], "difficulty": str,
                  "remaining": int}
        """
        date = today or _today()
        difficulty = difficulty if difficulty in _DIFFICULTIES else "practice"

        candidates = self._candidate_sources(date)
        existing = self._existing_extra_count(date)
        remaining = max(0, self.max_daily_extra - existing)

        if not candidates or remaining <= 0:
            return {
                "created": [],
                "skipped_duplicate": [],
                "skipped_budget": [],
                "difficulty": difficulty,
                "remaining": remaining,
            }

        created = []
        skipped_duplicate = []
        skipped_budget = []
        for cand in candidates:
            if len(created) >= remaining:
                skipped_budget.append(cand["title"])
                continue
            if self._source_has_unfinished_extra(cand):
                skipped_duplicate.append(cand["title"])
                continue
            if self._source_has_extra_on_date(cand, date):
                skipped_duplicate.append(cand["title"])
                continue
            created.append(self._create_extra_task(cand, date, difficulty))

        return {
            "created": created,
            "skipped_duplicate": skipped_duplicate,
            "skipped_budget": skipped_budget,
            "difficulty": difficulty,
            "remaining": max(0, remaining - len(created)),
        }

    def _create_extra_task(self, cand: dict, date_str: str, difficulty: str):
        label = DIFFICULTY_LABELS[difficulty]
        minutes = DIFFICULTY_MINUTES[difficulty]
        task = self.repo.create(
            title=f"【额外·{label}】{cand['title']}",
            description="用户主动的额外学习任务，不推进正式学习计划。",
            category="额外学习",
            estimated_minutes=minutes,
            priority=1,
            source="extra",
            task_type="extra",
            topic_id=cand["ref_id"] if cand["kind"] == "topic" else None,
            knowledge_point_id=(
                cand["ref_id"] if cand["kind"] == "knowledge_point" else None
            ),
            scheduled_date=date_str,
            difficulty=difficulty,
            route_id=cand.get("route_id"),
        )
        # 复用 StudyPlanService 的统一 topic -> knowledge_point 关联实现，
        # 让“只有 topic_id”的额外任务创建后就直接获得 kp（幂等）。
        if self.study_plan_service is not None:
            try:
                linked = self.study_plan_service.link_task_knowledge_point(task)
                if linked is not None:
                    task = linked
            except Exception:  # noqa: BLE001 - 关联失败不影响额外任务生成
                pass
        return task
