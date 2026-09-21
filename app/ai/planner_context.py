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
    # Phase 2：下一个未完成的 required learning activity（确定性计算）
    next_activity: str = ""
    next_activity_label: str = ""


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
class SkillPriority:
    """近期应重点的技能（Phase C；SkillService 计算，只影响近期优先级）。"""

    skill: str
    tier: str = ""
    score: float = 0.0
    reason: str = ""
    # Step 6：近期市场信号（近30天）+ 阶段适配（仅说明/排序，不改阶段）
    market_30d: float | None = None
    sample_count: int = 0
    market_source: str = "none"
    stage_alignment: str = "unknown"
    blocked: bool = False
    reasons: tuple[str, ...] = ()


@dataclass
class MarketTrend:
    """近期目标岗位技术趋势（人工样本，不代表全市场）。"""

    skill: str
    market_30d: float = 0.0
    mention_30d: int = 0


@dataclass
class JdGapSkill:
    """JD 强需求但当前未掌握/未开始的技能（企业需求缺口）。"""

    skill: str
    jd_must_count: int = 0
    jd_plus_count: int = 0
    mastery: float | None = None
    blocked: bool = False
    market_30d: float | None = None
    sample_count: int = 0


@dataclass
class PrerequisiteBlocked:
    """前置依赖未满足、被门禁阻塞的技能（不得越级安排）。"""

    skill: str
    missing: list[str] = field(default_factory=list)


@dataclass
class WeeklyFocus:
    """未来某一天应优先的技能（纯规则预览，不写 tasks）。"""

    date: str = ""
    skills: list[str] = field(default_factory=list)


@dataclass
class PlannerFeedback:
    """Phase 6：某个 legal Topic 的结构化优先级信号（确定性来源）。"""

    topic_id: int
    tier: int = 2
    tier_label: str = "normal_curriculum"
    next_activity: str = ""
    next_activity_label: str = ""
    reasons: list[str] = field(default_factory=list)
    project_requirements: list[dict] = field(default_factory=list)
    active_project_blocker_count: int = 0
    max_capability_gap: int = 0


@dataclass
class PlanningContext:
    """传给 AI 用于规划下一天的完整上下文。"""

    current_date: str
    current_phase: str = ""
    # Phase D：多路线上下文（route-scoped）
    route_id: int | None = None
    route_name: str = ""
    route_goal: str = ""
    phase_goal: str = ""
    available_topics: list[ContextTopic] = field(default_factory=list)
    recent_7_days: list[DaySummary] = field(default_factory=list)
    unfinished_tasks: list[ContextTask] = field(default_factory=list)
    postponed_tasks: list[ContextTask] = field(default_factory=list)
    completed_tasks: list[ContextTask] = field(default_factory=list)
    knowledge_evidence: list[KnowledgeEvidence] = field(default_factory=list)
    # Phase C：JD / Skill 优先级上下文
    skill_priorities: list[SkillPriority] = field(default_factory=list)
    jd_gap_skills: list[JdGapSkill] = field(default_factory=list)
    # Step 6：近期目标岗位技术趋势（Daily Summary；无则空）
    market_trends: list[MarketTrend] = field(default_factory=list)
    market_source: str = "none"
    market_sample_count_30d: int = 0
    prerequisite_blocked: list[PrerequisiteBlocked] = field(
        default_factory=list
    )
    weekly_focus: list[WeeklyFocus] = field(default_factory=list)
    estimated_minutes: int = 0
    actual_completed_minutes: int = 0
    current_daily_limit: int = 180
    # Phase 6：Planner Feedback（确定性已排序候选）
    planner_feedback: list[PlannerFeedback] = field(default_factory=list)
    candidate_topic_ids: list[int] = field(default_factory=list)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的 dict（供 prompt 拼装 / planner_decisions 落库）。"""
        return {
            "current_date": self.current_date,
            "route_id": self.route_id,
            "route_name": self.route_name,
            "route_goal": self.route_goal,
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
            "skill_priorities": [
                {
                    "skill": s.skill,
                    "tier": s.tier,
                    "score": s.score,
                    "reason": s.reason,
                    "market_30d": s.market_30d,
                    "sample_count": s.sample_count,
                    "market_source": s.market_source,
                    "stage_alignment": s.stage_alignment,
                    "blocked": s.blocked,
                    "reasons": list(s.reasons),
                }
                for s in self.skill_priorities
            ],
            "jd_gap_skills": [
                {
                    "skill": g.skill,
                    "jd_must_count": g.jd_must_count,
                    "jd_plus_count": g.jd_plus_count,
                    "mastery": g.mastery,
                    "blocked": g.blocked,
                    "market_30d": g.market_30d,
                    "sample_count": g.sample_count,
                }
                for g in self.jd_gap_skills
            ],
            "market_trends": [
                {
                    "skill": m.skill,
                    "market_30d": m.market_30d,
                    "mention_30d": m.mention_30d,
                }
                for m in self.market_trends
            ],
            "market_source": self.market_source,
            "market_sample_count_30d": self.market_sample_count_30d,
            "prerequisite_blocked": [
                {
                    "skill": b.skill,
                    "missing": list(b.missing),
                }
                for b in self.prerequisite_blocked
            ],
            "weekly_focus": [
                {
                    "date": w.date,
                    "skills": list(w.skills),
                }
                for w in self.weekly_focus
            ],
            "estimated_minutes": self.estimated_minutes,
            "actual_completed_minutes": self.actual_completed_minutes,
            "current_daily_limit": self.current_daily_limit,
            "candidate_topic_ids": list(self.candidate_topic_ids),
            "planner_feedback": [
                {
                    "topic_id": f.topic_id,
                    "tier": f.tier,
                    "tier_label": f.tier_label,
                    "next_activity": f.next_activity,
                    "next_activity_label": f.next_activity_label,
                    "reasons": list(f.reasons),
                    "project_requirements": list(f.project_requirements),
                    "active_project_blocker_count": f.active_project_blocker_count,
                    "max_capability_gap": f.max_capability_gap,
                }
                for f in self.planner_feedback
            ],
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
