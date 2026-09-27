"""Pure deterministic selection of a teaching strategy from a Task Context."""

from __future__ import annotations

from .base import AgentSkill
from .registry import AgentSkillRegistry


class AgentSkillSelector:
    """Select one static Skill using only the current compact Task Context."""

    def __init__(self, registry: AgentSkillRegistry):
        self.registry = registry

    def select(self, task_context: dict | None) -> AgentSkill:
        task = task_context.get("task") if isinstance(task_context, dict) else None
        activity_kind = task.get("activity_kind") if isinstance(task, dict) else None
        return self.registry.select_for_activity(activity_kind)
