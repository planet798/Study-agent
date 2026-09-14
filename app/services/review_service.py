"""复习调度服务。

职责：
- 根据 knowledge_points.next_review_date 识别到期知识点；
- 只为“已有学习/验收证据”的知识点生成复习任务；
- 生成复习任务（tasks: source='review', task_type='review'）并写 review_schedule，
  建立 knowledge_point_id / task_id / assessment_attempts 之间关联；
- 去重：同一天不重复、存在未完成复习任务不重复；
- 每日复习预算：限制自动生成的复习知识点数量；
- 验收完成后：根据 result_level 动态调整间隔并安排下一次复习。

间隔规则（简单、可解释，Ebbinghaus 只是设计思想，不是严格心理学模型）：
  首次（interval_days == 0）：
    excellent=7, good=3, ok=2, poor=1
  之后：
    excellent → min(30, interval*3)
    good      → min(30, interval*2)
    ok        → min(30, interval+1)
    poor      → max(1,  interval//2)
"""

from __future__ import annotations

import json
from datetime import date as _date

from ..database.assessment_repository import AssessmentRepository
from ..database.repository import TaskRepository
from ..database.schema import STATUS_DONE
from ..utils.date_utils import add_days, now_iso, today as _today
from .task_content import build_retention_task_content

# 每日复习预算（可配置，不硬编码在调用处）
DEFAULT_MAX_DAILY_REVIEWS = 5
DEFAULT_REVIEW_MINUTES = 15

# 每日巩固（Daily Retention）：在到期复习不足时补足“今日复习”总量。
DEFAULT_RETENTION_TARGET = 3
DEFAULT_RETENTION_MINUTES = 10
RETENTION_SOURCE = "daily_retention"

# 首次验收的初始间隔
_INITIAL_INTERVALS = {"excellent": 7, "good": 3, "ok": 2, "poor": 1}
_MAX_INTERVAL = 30

# 弱项判定（简单确定性规则）：验收等级偏低或掌握度低于阈值。
_WEAK_RESULTS = {"poor", "ok"}
_STRONG_RESULTS = {"good", "excellent"}
_WEAK_MASTERY_THRESHOLD = 0.6


class ReviewService:
    def __init__(
        self,
        repo: TaskRepository,
        assessment_repo: AssessmentRepository,
        max_daily_reviews: int = DEFAULT_MAX_DAILY_REVIEWS,
        review_minutes: int = DEFAULT_REVIEW_MINUTES,
        retention_target: int = DEFAULT_RETENTION_TARGET,
        retention_minutes: int = DEFAULT_RETENTION_MINUTES,
        retention_cooldown: int = 2,
        retention_weak_cooldown: int = 1,
        weak_mastery: float = _WEAK_MASTERY_THRESHOLD,
        plan_repo=None,
    ):
        self.repo = repo
        self.assessment_repo = assessment_repo
        self.max_daily_reviews = max_daily_reviews
        self.review_minutes = review_minutes
        self.retention_target = retention_target
        self.retention_minutes = retention_minutes
        self.retention_cooldown = retention_cooldown
        self.retention_weak_cooldown = retention_weak_cooldown
        self.weak_mastery = weak_mastery
        self.plan_repo = plan_repo

    # ================= 间隔调整（纯函数，可独立测试） =================

    @staticmethod
    def next_interval(current_interval_days: int, result_level: str) -> int:
        """根据上次间隔 + 本次验收等级计算下次复习间隔。"""
        current = int(current_interval_days or 0)
        if current <= 0:
            return _INITIAL_INTERVALS.get(result_level, 1)
        if result_level == "excellent":
            return min(_MAX_INTERVAL, current * 3)
        if result_level == "good":
            return min(_MAX_INTERVAL, current * 2)
        if result_level == "ok":
            return min(_MAX_INTERVAL, current + 1)
        if result_level == "poor":
            return max(1, current // 2)
        # 未知等级：维持原间隔，不扩大风险
        return current

    # ================= 到期判断 =================

    def is_due(self, knowledge_point: dict, today: str | None = None) -> bool:
        """判断知识点的复习是否到期。"""
        date = today or _today()
        next_review = knowledge_point.get("next_review_date")
        if not next_review:
            return False
        # 只有已经有过验收证据（掌握度已被评估过）才进入复习
        if knowledge_point.get("last_assessed_at") is None:
            return False
        return next_review <= date

    def due_knowledge_points(self, today: str | None = None) -> list[dict]:
        """返回今天到期的知识点（按到期日升序、掌握度从低到高排序）。"""
        date = today or _today()
        due = [
            kp for kp in self.assessment_repo.list_knowledge_points()
            if self.is_due(kp, date)
        ]
        due.sort(key=lambda kp: (kp["next_review_date"], kp["mastery_estimate"]))
        return due

    # ================= 去重 =================

    def has_unfinished_review_task(self, knowledge_point_id: int) -> bool:
        """该知识点是否已存在未完成的“正式复习”任务（active / not_done）。

        只统计正式到期复习（source='review'）；每日巩固（daily_retention）是
        轻量补足，不应阻塞正式间隔复习。
        """
        for task in self.repo.list_by_knowledge_point(knowledge_point_id):
            if (
                task.task_type == "review"
                and task.status != STATUS_DONE
                and task.source != RETENTION_SOURCE
            ):
                return True
        return False

    def has_pending_review_on_date(
        self, knowledge_point_id: int, date_str: str
    ) -> bool:
        """该知识点在指定日期是否已有 pending 的复习调度。"""
        for s in self.assessment_repo.list_review_schedules_for_kp(
            knowledge_point_id
        ):
            if s["scheduled_date"] == date_str and s["status"] == "pending":
                return True
        return False

    # ================= 生成复习任务 =================

    def generate_due_reviews(self, today: str | None = None) -> dict:
        """为今天到期、且未重复的知识点生成复习任务（受每日预算约束）。

        :return: {"created": [schedule dict], "skipped_duplicate": [kp_id],
                  "skipped_budget": [kp_id]}
        """
        date = today or _today()
        created: list[dict] = []
        skipped_duplicate: list[int] = []
        skipped_budget: list[int] = []

        for kp in self.due_knowledge_points(date):
            if len(created) >= self.max_daily_reviews:
                skipped_budget.append(kp["id"])
                continue
            if self.has_unfinished_review_task(kp["id"]):
                skipped_duplicate.append(kp["id"])
                continue
            if self.has_pending_review_on_date(kp["id"], date):
                skipped_duplicate.append(kp["id"])
                continue

            schedule = self._create_review_task(kp, date)
            created.append(schedule)

        return {
            "created": created,
            "skipped_duplicate": skipped_duplicate,
            "skipped_budget": skipped_budget,
        }

    def _create_review_task(self, kp: dict, date_str: str) -> dict:
        """创建一条复习任务 + 一条 review_schedule。"""
        task = self.repo.create(
            title=f"复习 {kp['name']}",
            description=kp.get("description") or "知识点复习验收",
            category="复习",
            estimated_minutes=self.review_minutes,
            priority=2,
            source="review",
            task_type="review",
            knowledge_point_id=kp["id"],
            scheduled_date=date_str,
        )
        schedule = self.assessment_repo.create_review_schedule(
            knowledge_point_id=kp["id"],
            scheduled_date=date_str,
            interval_days=int(kp.get("interval_days") or 0),
            task_id=task.id,
            source_attempt_id=self._latest_judged_attempt_id(kp["id"]),
            status="pending",
        )
        schedule["task_title"] = task.title
        return schedule

    def _latest_judged_attempt_id(self, knowledge_point_id: int) -> int | None:
        judged = [
            a for a in self.assessment_repo.list_attempts_for_knowledge_point(
                knowledge_point_id
            )
            if a.get("judge_status") == "judged"
        ]
        return judged[-1]["id"] if judged else None

    # ================= 验收完成后更新调度 =================

    def record_assessment_result(
        self, attempt: dict, today: str | None = None
    ) -> None:
        """验收判题成功后：关闭对应复习任务，并安排下一次复习。"""
        date = today or _today()
        kp = self.assessment_repo.get_knowledge_point(
            attempt["knowledge_point_id"]
        )
        if kp is None:
            return
        result_level = attempt.get("result_level")
        if not result_level:
            return

        # 关闭该验收任务对应的 pending 复习调度
        if attempt.get("task_id"):
            schedule = self.assessment_repo.find_pending_review_for_task(
                attempt["task_id"]
            )
            if schedule is not None:
                self.assessment_repo.update_review_schedule(
                    schedule["id"],
                    status="done",
                    completed_at=now_iso(),
                )

        # 计算下一次间隔并写回知识点
        interval = self.next_interval(
            int(kp.get("interval_days") or 0), result_level
        )
        self.assessment_repo.update_knowledge_point(
            kp["id"],
            interval_days=interval,
            next_review_date=add_days(date, interval),
        )

    # ================= 每日巩固复习（Daily Retention） =================
    #
    # 目的：到期复习（assessment 驱动）不足时，补足“今日复习”总量，
    # 帮刚学过的知识做短期巩固。它不取代间隔复习，也不产生任何验收证据：
    #   - 不写/不改 knowledge_points 的 mastery / next_review_date / interval；
    #   - 不写 review_schedule；
    #   - 不创建 assessment_attempts。
    # 与到期复习共用 tasks 表（task_type='review', source='daily_retention'），
    # 所以 UI 仍统一显示在【今日复习】。

    def generate_daily_retention_reviews(
        self, today: str | None = None, target_total: int | None = None
    ) -> dict:
        """在到期复习不足时，为“今日复习”补足每日巩固任务（幂等）。

        总量目标：due + retention <= target_total（默认 3）。
        同一天重复调用不会重复创建（以当日已有任务为准）。

        :return: {"created": [...], "due_count": int,
                  "existing_retention": int, "remaining": int}
        """
        date_str = today or _today()
        target = int(
            target_total if target_total is not None else self.retention_target
        )
        todays = [
            t for t in self.repo.list_by_date(date_str)
            if t.task_type == "review"
        ]
        due_count = sum(1 for t in todays if t.source == "review")
        existing_retention = sum(
            1 for t in todays if t.source == RETENTION_SOURCE
        )
        remaining = target - due_count - existing_retention
        result = {
            "created": [],
            "due_count": due_count,
            "existing_retention": existing_retention,
            "remaining": max(0, remaining),
            "candidates": [],
        }
        if remaining <= 0:
            return result

        used = {t.knowledge_point_id for t in todays if t.knowledge_point_id}
        candidates = self.retention_candidates(
            date_str, exclude_kp_ids=used
        )
        result["candidates"] = [c["knowledge_point_id"] for c in candidates]
        for cand in candidates:
            if len(result["created"]) >= remaining:
                break
            result["created"].append(
                self._create_retention_task(cand, date_str)
            )
        result["remaining"] = max(0, remaining - len(result["created"]))
        return result

    def retention_candidates(
        self, today: str | None = None, exclude_kp_ids=None
    ) -> list[dict]:
        """返回今日可用的每日巩固候选（已排序、已应用冷却）。

        候选只能来自真正学过的正式任务（见
        TaskRepository.list_done_learning_tasks_before）。
        排序信号：weak 优先 → 最近学习优先（1~3 > 4~7 > 8~14 > 14+）
        → 越久未巩固优先 → topic priority → 学习日期。
        """
        date_str = today or _today()
        exclude = {
            int(k) for k in (exclude_kp_ids or []) if k is not None
        }

        learned: dict[int, str] = {}
        for t in self.repo.list_done_learning_tasks_before(date_str):
            kp_id = t.knowledge_point_id
            if kp_id is None or kp_id in exclude:
                continue
            if kp_id not in learned or t.scheduled_date > learned[kp_id]:
                learned[kp_id] = t.scheduled_date

        cooldown_max = max(
            self.retention_cooldown, self.retention_weak_cooldown
        )
        window_start = add_days(date_str, -(cooldown_max * 4 + 1))
        last_retention: dict[int, str] = {}
        for t in self.repo.list_review_tasks_by_source(
            RETENTION_SOURCE, window_start, date_str
        ):
            kp_id = t.knowledge_point_id
            if kp_id is None:
                continue
            if (
                kp_id not in last_retention
                or t.scheduled_date > last_retention[kp_id]
            ):
                last_retention[kp_id] = t.scheduled_date

        evidence = self._latest_evidence_by_kp()
        out: list[dict] = []
        for kp_id, learned_date in learned.items():
            kp = self.assessment_repo.get_knowledge_point(kp_id)
            if kp is None:
                continue
            attempt = evidence.get(kp_id)
            weak = self._is_weak(kp, attempt)
            cooldown = (
                self.retention_weak_cooldown
                if weak
                else self.retention_cooldown
            )
            last_date = last_retention.get(kp_id)
            gap = self._days_between(last_date, date_str) if last_date else None
            if gap is not None and gap < cooldown:
                continue  # 冷却中：避免连续多天重复同一知识点
            out.append({
                "kp": kp,
                "knowledge_point_id": kp_id,
                "weak": weak,
                "attempt": attempt,
                "days_since_learned": self._days_between(learned_date, date_str),
                "gap_since_retention": gap,
                "topic_priority": self._topic_priority(kp),
            })
        out.sort(key=self._retention_sort_key)
        return out

    def _retention_sort_key(self, cand: dict):
        days = cand["days_since_learned"]
        if days <= 3:
            bucket = 0
        elif days <= 7:
            bucket = 1
        elif days <= 14:
            bucket = 2
        else:
            bucket = 3  # 超过 14 天：优先交给正式间隔复习
        gap = cand["gap_since_retention"]
        gap_key = -10 ** 6 if gap is None else -gap
        return (
            0 if cand["weak"] else 1,
            bucket,
            gap_key,
            -int(cand.get("topic_priority") or 0),
            -days,
            cand["knowledge_point_id"],
        )

    def _create_retention_task(self, cand: dict, date_str: str) -> dict:
        """创建每日巩固任务（不写 review_schedule / 不改知识点）。"""
        kp = cand["kp"]
        weak_points = self._weak_points(cand.get("attempt"))
        task = self.repo.create(
            title=f"每日巩固 {kp['name']}",
            description=build_retention_task_content(
                kp["name"], kp.get("description") or "", weak_points
            ),
            category="复习",
            estimated_minutes=self.retention_minutes,
            priority=2,
            source=RETENTION_SOURCE,
            task_type="review",
            knowledge_point_id=kp["id"],
            scheduled_date=date_str,
        )
        return {
            "task_id": task.id,
            "knowledge_point_id": kp["id"],
            "title": task.title,
        }

    def _is_weak(self, kp: dict, attempt: dict | None) -> bool:
        """是否有“弱项”证据（验收等级低或掌握度低于阈值）。

        无验收证据时不视为弱项（只是没评估过）。
        """
        level = (attempt or {}).get("result_level")
        if level in _WEAK_RESULTS:
            return True
        if level in _STRONG_RESULTS:
            return False
        if kp.get("last_assessed_at") and kp.get("mastery_estimate") is not None:
            return float(kp["mastery_estimate"]) < self.weak_mastery
        return False

    def _latest_evidence_by_kp(self) -> dict[int, dict]:
        """每个知识点最新一次已判题的验收记录（无则不在结果中）。"""
        out: dict[int, dict] = {}
        for a in self.assessment_repo.list_attempts():
            if a.get("judge_status") != "judged":
                continue
            kp_id = a.get("knowledge_point_id")
            if kp_id is None:
                continue
            prev = out.get(kp_id)
            if prev is None or a["id"] > prev["id"]:
                out[kp_id] = a
        return out

    def _weak_points(self, attempt: dict | None) -> list[str]:
        raw = (attempt or {}).get("weak_points_json")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
        return []

    def _topic_priority(self, kp: dict) -> int:
        topic_id = kp.get("topic_id")
        if self.plan_repo is None or topic_id is None:
            return 0
        try:
            topic = self.plan_repo.get_topic(topic_id)
        except Exception:  # noqa: BLE001 - 优先级仅用于排序，失败不影响生成
            return 0
        return int(getattr(topic, "priority", 0) or 0) if topic else 0

    @staticmethod
    def _days_between(earlier: str | None, later: str) -> int:
        if not earlier:
            return 0
        try:
            return (_date.fromisoformat(later) - _date.fromisoformat(earlier)).days
        except (TypeError, ValueError):
            return 0
