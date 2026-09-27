"""Compact, read-only Task Context snapshot for each Agent user turn (Agent-3)."""

from __future__ import annotations

from typing import Any

from .tools.base import AgentToolContext
from .tools.registry import AgentToolRegistry

CONTEXT_TOOL_NAMES = (
    "get_task_context",
    "get_route_context",
    "get_topic_context",
    "get_learning_components",
    "get_mastery",
    "get_capability",
)
DEFAULT_TEXT_LIMIT = 2000


class AgentTaskContextBuilder:
    """Build a small deterministic context by reusing Agent-2 read-only tools.

    The builder returns an in-memory snapshot only. It neither creates model tool
    protocol messages nor persists context to ``agent_messages``.
    """

    def __init__(
        self,
        tool_registry: AgentToolRegistry,
        max_text_length: int = DEFAULT_TEXT_LIMIT,
    ):
        self.tool_registry = tool_registry
        if isinstance(max_text_length, bool) or int(max_text_length) < 1:
            raise ValueError("max_text_length must be a positive integer")
        self.max_text_length = int(max_text_length)

    def build(self, context: AgentToolContext) -> dict[str, Any]:
        """Read each canonical context tool exactly once and compact its result."""
        results = {
            name: self.tool_registry.execute(name, context, {})
            for name in CONTEXT_TOOL_NAMES
        }
        task_data = self._data(results["get_task_context"])
        route_data = self._data(results["get_route_context"])
        topic_data = self._data(results["get_topic_context"])
        learning_data = self._data(results["get_learning_components"])
        mastery_data = self._data(results["get_mastery"])
        capability_data = self._data(results["get_capability"])

        task = None
        if task_data is not None:
            task = {
                "title": self._text(task_data.get("title")),
                "description": self._text(task_data.get("description")),
                "estimated_minutes": task_data.get("estimated_minutes"),
                "status": task_data.get("status"),
                "activity_kind": self._text(
                    task_data.get("learning_activity_kind")
                ),
                "project_name": self._text(task_data.get("project_name")),
                "deliverable": self._text(task_data.get("deliverable")),
                "acceptance_criteria": self._text(
                    task_data.get("acceptance_criteria")
                ),
                "expected_artifact": self._text(
                    task_data.get("expected_artifact")
                ),
            }

        route = None
        route_obj = route_data.get("route") if route_data else None
        if isinstance(route_obj, dict):
            route = {
                "name": self._text(route_obj.get("name")),
                "goal": self._text(route_obj.get("goal")),
            }

        phase = None
        topic = None
        topic_obj = topic_data.get("topic") if topic_data else None
        phase_obj = topic_data.get("phase") if topic_data else None
        if isinstance(topic_obj, dict):
            topic = {
                "name": self._text(topic_obj.get("name")),
                "description": self._text(topic_obj.get("description")),
            }
        if isinstance(phase_obj, dict):
            phase = {
                "name": self._text(phase_obj.get("name")),
                "goals": self._text(phase_obj.get("goals")),
            }

        learning = None
        if learning_data is not None and learning_data.get("available"):
            components = learning_data.get("components") or []
            current_id = learning_data.get("current_component_id")
            current = next(
                (
                    c for c in components
                    if isinstance(c, dict) and c.get("component_id") == current_id
                ),
                None,
            )
            learning = {
                "current_component": self._text(
                    current.get("label") if current else
                    learning_data.get("current_activity_kind")
                ),
                "components": [
                    {
                        "activity_kind": self._text(c.get("activity_kind")),
                        "label": self._text(c.get("label")),
                        "required": bool(c.get("required")),
                        "complete": bool(c.get("complete")),
                    }
                    for c in components if isinstance(c, dict)
                ],
            }

        mastery = None
        if mastery_data is not None and mastery_data.get("available"):
            mastery = {
                "has_assessment": bool(mastery_data.get("has_assessment")),
                "mastery_estimate": mastery_data.get("mastery_estimate"),
            }

        capability = None
        if capability_data is not None and capability_data.get("available"):
            capability = {
                "level": capability_data.get("level"),
                "label": self._text(capability_data.get("label")),
                "evidence_count": capability_data.get("evidence_count"),
                "has_evidence": bool(capability_data.get("has_evidence")),
            }

        return {
            "task": task,
            "route": route,
            "phase": phase,
            "topic": topic,
            "learning": learning,
            "mastery": mastery,
            "capability": capability,
        }

    @staticmethod
    def _data(result: dict | None) -> dict | None:
        if not isinstance(result, dict) or result.get("ok") is not True:
            return None
        data = result.get("data")
        if not isinstance(data, dict) or data.get("available") is False:
            return None
        return data

    def _text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) > self.max_text_length:
            return text[:self.max_text_length]
        return text
