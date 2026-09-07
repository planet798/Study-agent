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

from ..database.assessment_repository import AssessmentRepository
from ..database.repository import TaskRepository
from ..database.schema import STATUS_DONE
from ..utils.date_utils import add_days, now_iso, today as _today

# 每日复习预算（可配置，不硬编码在调用处）
DEFAULT_MAX_DAILY_REVIEWS = 5
DEFAULT_REVIEW_MINUTES = 15

# 首次验收的初始间隔
_INITIAL_INTERVALS = {"excellent": 7, "good": 3, "ok": 2, "poor": 1}
_MAX_INTERVAL = 30


class ReviewService:
    def __init__(
        self,
        repo: TaskRepository,
        assessment_repo: AssessmentRepository,
        max_daily_reviews: int = DEFAULT_MAX_DAILY_REVIEWS,
        review_minutes: int = DEFAULT_REVIEW_MINUTES,
    ):
        self.repo = repo
        self.assessment_repo = assessment_repo
        self.max_daily_reviews = max_daily_reviews
        self.review_minutes = review_minutes

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
        """该知识点是否已存在未完成的复习任务（active / not_done）。"""
        for task in self.repo.list_by_knowledge_point(knowledge_point_id):
            if task.task_type == "review" and task.status != STATUS_DONE:
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
