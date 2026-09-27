"""Native learning tools read task-scoped context only through Services."""

from __future__ import annotations

import json

import pytest

from app.agent.tools.base import AgentToolContext
from app.agent.tools.learning import build_learning_tool_registry
from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.database.topic_learning_repository import TopicLearningComponentRepository
from app.services.assessment_service import AssessmentService
from app.services.capability import EVIDENCE_TYPE_ASSESSMENT
from app.services.capability_service import CapabilityService
from app.services.learning_route_service import LearningRouteService
from app.services.learning_activity import ACTIVITY_THEORY
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.services.topic_learning_profile_service import TopicLearningProfileService


class _UnusedAIClient:
    def is_configured(self):
        return False

    def chat(self, *args, **kwargs):
        raise AssertionError("read-only learning tools must not call AI")


@pytest.fixture()
def learning_env(conn):
    task_repo = TaskRepository(conn)
    task_service = TaskService(task_repo)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    plan_repo = StudyPlanRepository(conn)
    plan_service = StudyPlanService(task_repo, plan_repo)
    component_repo = TopicLearningComponentRepository(conn)
    topic_service = TopicLearningProfileService(conn, component_repo)
    assessment_repo = AssessmentRepository(conn)
    assessment_service = AssessmentService(
        _UnusedAIClient(), assessment_repo=assessment_repo
    )
    evidence_repo = CapabilityEvidenceRepository(conn)
    capability_service = CapabilityService(conn, evidence_repo)

    route = route_service.create_learning_route(
        "Agent-2 route", description="route description", goal="route goal",
        priority=4, planning_enabled=True,
    )
    plan = plan_repo.create_plan(
        "Agent-2 plan", "2026-01-01", "2027-01-01", route_id=route.id
    )
    phase = plan_repo.create_phase(
        plan.id, "Agent-2 phase", "2026-01-01", "2027-01-01",
        description="phase description", goals="phase goals",
    )
    topic = plan_repo.create_topic(
        phase.id, "Attention", description="Topic description",
        estimated_minutes=40, priority=3,
    )
    component = component_repo.create(topic.id, ACTIVITY_THEORY, order_index=0)
    kp = assessment_repo.create_knowledge_point(
        "Attention KP", description="KP description", topic_id=topic.id,
        route_id=route.id,
    )
    task = task_repo.create(
        title="Study attention", description="Learn the core idea",
        scheduled_date="2026-09-15", estimated_minutes=45,
        source="generated", task_type="new", route_id=route.id,
        topic_id=topic.id, knowledge_point_id=kp["id"],
        component_id=component["id"], learning_activity_kind=ACTIVITY_THEORY,
        project_name="", deliverable="artifact", acceptance_criteria="explain",
        expected_artifact="diagram",
    )
    registry = build_learning_tool_registry(
        task_service, route_service, plan_service, topic_service,
        assessment_service, capability_service,
    )
    return {
        "conn": conn, "task_repo": task_repo, "task_service": task_service,
        "route_repo": route_repo, "route_service": route_service,
        "plan_repo": plan_repo, "plan_service": plan_service,
        "component_repo": component_repo, "topic_service": topic_service,
        "assessment_repo": assessment_repo,
        "assessment_service": assessment_service,
        "evidence_repo": evidence_repo, "capability_service": capability_service,
        "route": route, "plan": plan, "phase": phase, "topic": topic,
        "component": component, "kp": kp, "task": task, "registry": registry,
    }


def _call(env, name, task=None, arguments="{}"):
    task = task or env["task"]
    return env["registry"].execute_raw(
        name,
        AgentToolContext(session_id=901, task_id=task.id),
        arguments,
    )


def test_registry_contains_exact_six_read_only_tools_with_empty_arguments(learning_env):
    env = learning_env
    assert env["registry"].names() == (
        "get_task_context", "get_route_context", "get_topic_context",
        "get_learning_components", "get_mastery", "get_capability",
    )
    for declaration in env["registry"].model_tools():
        assert declaration["type"] == "function"
        assert declaration["function"]["parameters"] == {
            "type": "object", "properties": {}, "additionalProperties": False,
        }
        assert env["registry"].get(declaration["function"]["name"]).spec.read_only


def test_get_task_context_returns_only_task_domain_fields_and_is_json_safe(learning_env):
    env = learning_env
    result = _call(env, "get_task_context")
    assert result["ok"] is True
    data = result["data"]
    assert data == {
        "task_id": env["task"].id,
        "title": "Study attention",
        "description": "Learn the core idea",
        "status": "active",
        "scheduled_date": "2026-09-15",
        "estimated_minutes": 45,
        "source": "generated",
        "task_type": "new",
        "route_id": env["route"].id,
        "topic_id": env["topic"].id,
        "knowledge_point_id": env["kp"]["id"],
        "component_id": env["component"]["id"],
        "learning_activity_kind": ACTIVITY_THEORY,
        "project_name": "",
        "deliverable": "artifact",
        "acceptance_criteria": "explain",
        "expected_artifact": "diagram",
    }
    json.dumps(result, ensure_ascii=False)
    assert "reason" not in data and "priority" not in data


def test_get_route_context_uses_only_task_route(learning_env):
    env = learning_env
    result = _call(env, "get_route_context")
    assert result["ok"] is True
    assert result["data"] == {
        "available": True,
        "route": {
            "route_id": env["route"].id,
            "route_key": env["route"].route_key,
            "name": "Agent-2 route",
            "description": "route description",
            "goal": "route goal",
            "status": "active",
            "priority": 4,
            "planning_enabled": True,
        },
    }


def test_topic_service_read_apis_and_get_topic_context(learning_env):
    env = learning_env
    # Service API is a thin read-only delegate (Tool never sees StudyPlanRepository).
    assert env["plan_service"].get_topic(env["topic"].id).name == "Attention"
    assert env["plan_service"].get_phase(env["phase"].id).name == "Agent-2 phase"
    result = _call(env, "get_topic_context")
    assert result["ok"] is True
    assert result["data"] == {
        "available": True,
        "topic": {
            "id": env["topic"].id, "name": "Attention",
            "description": "Topic description", "estimated_minutes": 40,
            "priority": 3,
        },
        "phase": {
            "id": env["phase"].id, "name": "Agent-2 phase",
            "description": "phase description", "goals": "phase goals",
            "start_date": "2026-01-01", "end_date": "2027-01-01",
        },
    }


def test_get_learning_components_returns_status(learning_env):
    env = learning_env
    result = _call(env, "get_learning_components")
    assert result["ok"] is True
    assert result["data"] == {
        "available": True,
        "current_component_id": env["component"]["id"],
        "current_activity_kind": ACTIVITY_THEORY,
        "components": [{
            "component_id": env["component"]["id"],
            "activity_kind": ACTIVITY_THEORY,
            "label": "理论",
            "required": True,
            "complete": False,
            "order_index": 0,
        }],
    }


def test_get_mastery_distinguishes_unassessed_from_zero_mastery(learning_env):
    env = learning_env
    result = _call(env, "get_mastery")
    assert result["ok"] is True
    data = result["data"]
    assert data["available"] is True
    assert data["has_assessment"] is False
    assert data["mastery_estimate"] is None
    assert data["last_assessed_at"] is None
    assert not {"review_count", "next_review_date", "interval_days"} & data.keys()

    env["assessment_repo"].update_knowledge_point(
        env["kp"]["id"], mastery_estimate=0.72,
        last_assessed_at="2026-09-15T12:00:00",
    )
    assessed = _call(env, "get_mastery")["data"]
    assert assessed["has_assessment"] is True
    assert assessed["mastery_estimate"] == pytest.approx(0.72)
    assert assessed["last_assessed_at"] == "2026-09-15T12:00:00"


def test_get_capability_reports_no_evidence_then_evidence(learning_env):
    env = learning_env
    empty = _call(env, "get_capability")["data"]
    assert empty["available"] is True
    assert empty["level"] == 0
    assert empty["has_evidence"] is False
    assert empty["evidence_count"] == 0

    env["evidence_repo"].create_or_update_by_key(
        knowledge_point_id=env["kp"]["id"], capability_level=2,
        evidence_type=EVIDENCE_TYPE_ASSESSMENT,
        evidence_key=f"agent-tool-test:{env['kp']['id']}",
    )
    current = _call(env, "get_capability")["data"]
    assert current["level"] == 2
    assert current["has_evidence"] is True
    assert current["evidence_count"] == 1


def test_manual_learning_activity_and_null_relationships_are_graceful(learning_env):
    env = learning_env
    manual = env["task_repo"].create(
        title="Manual study activity", scheduled_date="2026-09-15",
        source="manual", task_type="manual",
    )
    assert _call(env, "get_route_context", manual)["data"] == {
        "available": False, "reason": "task_has_no_route"
    }
    assert _call(env, "get_topic_context", manual)["data"] == {
        "available": False, "reason": "task_has_no_topic"
    }
    assert _call(env, "get_learning_components", manual)["data"] == {
        "available": False, "reason": "task_has_no_topic"
    }
    for name in ("get_mastery", "get_capability"):
        assert _call(env, name, manual)["data"] == {
            "available": False, "reason": "task_has_no_knowledge_point"
        }

    # A route-less historical task remains readable; route is not inferred from Topic.
    route_less = env["task_repo"].create(
        title="No route", scheduled_date="2026-09-15", source="manual",
        task_type="new", topic_id=env["topic"].id,
    )
    assert _call(env, "get_route_context", route_less)["data"]["reason"] == "task_has_no_route"
    assert _call(env, "get_topic_context", route_less)["data"]["available"] is True


def test_temporary_knowledge_point_without_topic_is_supported(learning_env):
    env = learning_env
    temporary = env["assessment_repo"].create_knowledge_point(
        "Temporary KP", route_id=env["route"].id
    )
    task = env["task_repo"].create(
        title="Temporary knowledge learning", scheduled_date="2026-09-15",
        source="manual", task_type="new", route_id=env["route"].id,
        knowledge_point_id=temporary["id"],
    )
    topic_result = _call(env, "get_topic_context", task)["data"]
    assert topic_result == {"available": False, "reason": "task_has_no_topic"}
    mastery = _call(env, "get_mastery", task)["data"]
    assert mastery["available"] and mastery["has_assessment"] is False
    assert mastery["mastery_estimate"] is None
    capability = _call(env, "get_capability", task)["data"]
    assert capability["available"] and capability["has_evidence"] is False


def test_read_tools_do_not_mutate_task_mastery_capability_or_practice(learning_env):
    env = learning_env
    conn = env["conn"]
    before = {
        "task": env["task_service"].get_task(env["task"].id),
        "kp": env["assessment_service"].get_knowledge_point(env["kp"]["id"]),
        "capability_count": conn.execute(
            "SELECT COUNT(*) FROM capability_evidence"
        ).fetchone()[0],
        "practice_counts": {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "practice_projects", "practice_project_routes",
                "practice_project_skills", "practice_milestones",
                "practice_outputs", "practice_project_topics",
                "practice_topic_requirements",
                "practice_topic_evidence", "practice_topic_evidence_outputs",
            )
        },
        "assessment_count": conn.execute(
            "SELECT COUNT(*) FROM assessment_attempts"
        ).fetchone()[0],
    }
    for name in env["registry"].names():
        result = _call(env, name)
        assert result["ok"] is True, (name, result)
        json.dumps(result, ensure_ascii=False)
    after_task = env["task_service"].get_task(env["task"].id)
    after_kp = env["assessment_service"].get_knowledge_point(env["kp"]["id"])
    assert after_task.status == before["task"].status
    assert after_task == before["task"]
    assert after_kp["mastery_estimate"] == before["kp"]["mastery_estimate"]
    assert after_kp["last_assessed_at"] == before["kp"]["last_assessed_at"]
    assert conn.execute("SELECT COUNT(*) FROM capability_evidence").fetchone()[0] == before["capability_count"]
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before["practice_counts"]
    } == before["practice_counts"]
    assert conn.execute("SELECT COUNT(*) FROM assessment_attempts").fetchone()[0] == before["assessment_count"]
