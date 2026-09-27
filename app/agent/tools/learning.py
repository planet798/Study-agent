"""Six session-scoped, read-only learning context tools (Agent-2).

The only identifier used by handlers is ``AgentToolContext.task_id``, resolved by
Runtime from the active Agent Session. Tool arguments are deliberately empty so
the model cannot select arbitrary task / route / topic / knowledge-point IDs.
All domain access goes through injected existing Services.
"""

from __future__ import annotations

from .base import AgentTool, AgentToolContext, AgentToolSpec
from .registry import AgentToolRegistry


class _LearningTool(AgentTool):
    def __init__(self, name: str, description: str):
        self._spec = AgentToolSpec(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            read_only=True,
        )

    @property
    def spec(self) -> AgentToolSpec:
        return self._spec

    @staticmethod
    def _unavailable(reason: str) -> dict:
        return {"available": False, "reason": reason}


class GetTaskContextTool(_LearningTool):
    def __init__(self, task_service):
        super().__init__(
            "get_task_context",
            "Read the current session's learning task context.",
        )
        self.task_service = task_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        return {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "scheduled_date": task.scheduled_date,
            "estimated_minutes": task.estimated_minutes,
            "source": task.source,
            "task_type": task.task_type,
            "route_id": task.route_id,
            "topic_id": task.topic_id,
            "knowledge_point_id": task.knowledge_point_id,
            "component_id": task.component_id,
            "learning_activity_kind": task.learning_activity_kind,
            "project_name": task.project_name,
            "deliverable": task.deliverable,
            "acceptance_criteria": task.acceptance_criteria,
            "expected_artifact": task.expected_artifact,
        }


class GetRouteContextTool(_LearningTool):
    def __init__(self, task_service, learning_route_service):
        super().__init__(
            "get_route_context",
            "Read the learning route attached to the current session's task.",
        )
        self.task_service = task_service
        self.learning_route_service = learning_route_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        if task.route_id is None:
            return self._unavailable("task_has_no_route")
        route = self.learning_route_service.get(task.route_id)
        if route is None:
            return self._unavailable("task_route_not_available")
        return {
            "available": True,
            "route": {
                "route_id": route.id,
                "route_key": route.route_key,
                "name": route.name,
                "description": route.description,
                "goal": route.goal,
                "status": route.status,
                "priority": route.priority,
                "planning_enabled": bool(route.planning_enabled),
            },
        }


class GetTopicContextTool(_LearningTool):
    def __init__(self, task_service, study_plan_service):
        super().__init__(
            "get_topic_context",
            "Read the Topic and Phase attached to the current session's task.",
        )
        self.task_service = task_service
        self.study_plan_service = study_plan_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        if task.topic_id is None:
            return self._unavailable("task_has_no_topic")
        topic = self.study_plan_service.get_topic(task.topic_id)
        if topic is None:
            return self._unavailable("task_topic_not_available")
        phase = self.study_plan_service.get_phase(topic.phase_id)
        if phase is None:
            return self._unavailable("topic_phase_not_available")
        return {
            "available": True,
            "topic": {
                "id": topic.id,
                "name": topic.name,
                "description": topic.description,
                "estimated_minutes": topic.estimated_minutes,
                "priority": topic.priority,
            },
            "phase": {
                "id": phase.id,
                "name": phase.name,
                "description": phase.description,
                "goals": phase.goals,
                "start_date": phase.start_date,
                "end_date": phase.end_date,
            },
        }


class GetLearningComponentsTool(_LearningTool):
    def __init__(self, task_service, topic_learning_service):
        super().__init__(
            "get_learning_components",
            "Read enabled learning components and completion status for the task Topic.",
        )
        self.task_service = task_service
        self.topic_learning_service = topic_learning_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        if task.topic_id is None:
            return self._unavailable("task_has_no_topic")
        components = self.topic_learning_service.get_component_status(task.topic_id)
        return {
            "available": True,
            "current_component_id": task.component_id,
            "current_activity_kind": task.learning_activity_kind,
            "components": [
                {
                    "component_id": c["component_id"],
                    "activity_kind": c["activity_kind"],
                    "label": c["label"],
                    "required": bool(c["required"]),
                    "complete": bool(c["complete"]),
                    "order_index": c["order_index"],
                }
                for c in components
            ],
        }


class GetMasteryTool(_LearningTool):
    def __init__(self, task_service, assessment_service):
        super().__init__(
            "get_mastery",
            "Read Assessment-derived mastery for the current task's Knowledge Point; no review schedule data is exposed.",
        )
        self.task_service = task_service
        self.assessment_service = assessment_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        kp_id = task.knowledge_point_id
        if kp_id is None:
            return self._unavailable("task_has_no_knowledge_point")
        kp = self.assessment_service.get_knowledge_point(kp_id)
        if kp is None:
            return self._unavailable("knowledge_point_not_available")
        assessed = kp.get("last_assessed_at") is not None
        return {
            "available": True,
            "knowledge_point_id": kp_id,
            "name": kp.get("name", ""),
            # A default 0.0 is not evidence that the learner knows nothing.
            "mastery_estimate": (
                kp.get("mastery_estimate") if assessed else None
            ),
            "has_assessment": assessed,
            "last_assessed_at": kp.get("last_assessed_at"),
        }


class GetCapabilityTool(_LearningTool):
    def __init__(self, task_service, capability_service):
        super().__init__(
            "get_capability",
            "Read current evidence-derived capability for the current task's Knowledge Point.",
        )
        self.task_service = task_service
        self.capability_service = capability_service

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        task = self.task_service.get_task(context.task_id)
        kp_id = task.knowledge_point_id
        if kp_id is None:
            return self._unavailable("task_has_no_knowledge_point")
        capability = self.capability_service.get_current_capability(kp_id)
        return {
            "available": True,
            "knowledge_point_id": kp_id,
            "level": capability["level"],
            "name": capability["name"],
            "label": capability["label"],
            "evidence_count": capability["evidence_count"],
            "has_evidence": capability["has_evidence"],
        }


def build_learning_tool_registry(
    task_service,
    learning_route_service,
    study_plan_service,
    topic_learning_service,
    assessment_service,
    capability_service,
) -> AgentToolRegistry:
    """Build the six read-only tools from existing Services (never repositories)."""
    registry = AgentToolRegistry()
    for tool in (
        GetTaskContextTool(task_service),
        GetRouteContextTool(task_service, learning_route_service),
        GetTopicContextTool(task_service, study_plan_service),
        GetLearningComponentsTool(task_service, topic_learning_service),
        GetMasteryTool(task_service, assessment_service),
        GetCapabilityTool(task_service, capability_service),
    ):
        registry.register(tool)
    return registry


__all__ = [
    "GetCapabilityTool",
    "GetLearningComponentsTool",
    "GetMasteryTool",
    "GetRouteContextTool",
    "GetTaskContextTool",
    "GetTopicContextTool",
    "build_learning_tool_registry",
]
