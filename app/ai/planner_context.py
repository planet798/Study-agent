"""AI 规划上下文数据结构。

把数据库原始内容整理成给 AI 的结构化信息：
- 不直接塞原始行；
- 只包含与"规划下一天任务"相关的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DaySummary:
    """某一天的学习统计摘要（recent_7_days 的元素）。"""

    date: str
    total_tasks: int = 0
    completed_tasks: int = 0
    not_done_tasks: int = 0
    postponed_tasks: int = 0
    completion_rate: float = 0.0
    estimated_minutes: int = 0
    completed_minutes: int = 0


@dataclass
class ContextTopic:
    """当前阶段的可用主题（可选的 topic_id / 标题 / 时长 / 优先级）。"""

    topic_id: int
    title: str
    description: str = ""
    estimated_minutes: int = 0
    priority: int = 1


@dataclass
class ContextTask:
    """历史/延期任务摘要。"""

    task_id: int
    title: str
    status: str
    scheduled_date: str | None = None
    postpone_count: int = 0
    reason: str | None = None


@dataclass
class KnowledgeEvidence:
    """单个知识点的真实验收证据摘要（Phase 8）。

    只有确实有验收证据的知识点才会进入 knowledge_evidence：
    - 不会用默认 0.0 冒充“已经证明不会”；
    - mastery_estimate 是 AI 根据真实作答得到的估计，不是绝对事实。
    """

    knowledge_point_id: int
    name: str
    topic_id: int | None = None
    topic: str = ""
    mastery_estimate: float | None = None
    weak_points: tuple[str, ...] = ()
    last_assessed_at: str | None = None
    review_count: int = 0
    next_review_date: str | None = None
    recent_result_level: str | None = None


@dataclass
class PlanningContext:
    """传给 AI 用于规划下一天的完整上下文。"""

    current_date: str
    current_phase: str = ""
    phase_goal: str = ""
    available_topics: list[ContextTopic] = field(default_factory=list)
    recent_7_days: list[DaySummary] = field(default_factory=list)
    unfinished_tasks: list[ContextTask] = field(default_factory=list)
    postponed_tasks: list[ContextTask] = field(default_factory=list)
    completed_tasks: list[ContextTask] = field(default_factory=list)
    knowledge_evidence: list[KnowledgeEvidence] = field(default_factory=list)
    estimated_minutes: int = 0
    actual_completed_minutes: int = 0
    current_daily_limit: int = 180

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的 dict（供 prompt 拼装 / planner_decisions 落库）。"""
        return {
            "current_date": self.current_date,
            "current_phase": self.current_phase,
            "phase_goal": self.phase_goal,
            "available_topics": [
                {
                    "topic_id": t.topic_id,
                    "title": t.title,
                    "description": t.description,
                    "estimated_minutes": t.estimated_minutes,
                    "priority": t.priority,
                }
                for t in self.available_topics
            ],
            "recent_7_days": [
                {
                    "date": d.date,
                    "total_tasks": d.total_tasks,
                    "completed_tasks": d.completed_tasks,
                    "not_done_tasks": d.not_done_tasks,
                    "postponed_tasks": d.postponed_tasks,
                    "completion_rate": d.completion_rate,
                    "estimated_minutes": d.estimated_minutes,
                    "completed_minutes": d.completed_minutes,
                }
                for d in self.recent_7_days
            ],
            "unfinished_tasks": [self._task_to_dict(t) for t in self.unfinished_tasks],
            "postponed_tasks": [self._task_to_dict(t) for t in self.postponed_tasks],
            "completed_tasks": [self._task_to_dict(t) for t in self.completed_tasks],
            "knowledge_evidence": [
                {
                    "knowledge_point_id": e.knowledge_point_id,
                    "name": e.name,
                    "topic_id": e.topic_id,
                    "topic": e.topic,
                    "mastery_estimate": e.mastery_estimate,
                    "weak_points": list(e.weak_points),
                    "last_assessed_at": e.last_assessed_at,
                    "review_count": e.review_count,
                    "next_review_date": e.next_review_date,
                    "recent_result_level": e.recent_result_level,
                }
                for e in self.knowledge_evidence
            ],
            "estimated_minutes": self.estimated_minutes,
            "actual_completed_minutes": self.actual_completed_minutes,
            "current_daily_limit": self.current_daily_limit,
        }

    @staticmethod
    def _task_to_dict(t: ContextTask) -> dict:
        return {
            "task_id": t.task_id,
            "title": t.title,
            "status": t.status,
            "scheduled_date": t.scheduled_date,
            "postpone_count": t.postpone_count,
            "reason": t.reason,
        }

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False)
