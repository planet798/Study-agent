"""验证每个生产 AI 调用都真正经过 PromptRegistry（override 立即生效）。

这是需求 #33/#34 的核心测试：UI 中修改 Prompt 后，下一次真实 call 必须使用新内容。
"""

from __future__ import annotations

import json

import pytest

from app.ai.planner import AIPlanner
from app.ai.planner_context import PlanningContext
from app.ai.prompts import (
    build_assessment_generate_vars,
    build_assessment_judge_vars,
    build_jd_parse_vars,
    build_planner_system_vars,
    build_planner_user_vars,
    build_resume_material_vars,
    build_route_builder_vars,
    build_route_suggest_vars,
    build_task_review_vars,
)
from app.ai.schemas import (
    ASSESSMENT_QUESTION_TYPES,
    ASSESSMENT_RESULT_LEVELS,
    ASSESSMENT_VERDICTS,
)
from app.database.repository import Task
from app.services.ai_route_service import AIRouteBuilderService
from app.services.assessment_service import AssessmentService
from app.services.jd_service import build_default_parse_ai
from app.services.learning_outcome_service import LearningOutcomeService
from app.services.task_review_service import TaskReviewService


class CapturingClient:
    """假 AIClient：记录 (system, user)，按顺序返回预设内容。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


def _task():
    return Task(
        id=1, title="读书", description="d", category="学习",
        estimated_minutes=30, priority=2, status="active", reason=None,
        scheduled_date="2026-01-05", postpone_count=0, created_at="",
        updated_at="", completed_at=None, not_done_at=None,
    )


REVIEW_JSON = json.dumps({
    "reasonable": True, "score": 0.9, "should_postpone": True,
    "suggested_date": "2026-01-06", "analysis": "a", "suggestion": "b",
})
PLAN_JSON = json.dumps({
    "reasoning": "r",
    "recommended_tasks": [{"topic_id": 1, "title": "t",
                           "estimated_minutes": 30}],
    "carry_over_tasks": [], "daily_minutes": 30, "adjustment": "a",
})
QUESTIONS_JSON = json.dumps({
    "questions": [{"question": "q", "type": ASSESSMENT_QUESTION_TYPES[0],
                   "expected_points": 1}]
})
JUDGMENT_JSON = json.dumps({
    "questions": [{"question_index": 0, "verdict": ASSESSMENT_VERDICTS[0],
                   "reason": "ok"}],
    "weak_points": [], "result_level": ASSESSMENT_RESULT_LEVELS[0],
    "mastery_estimate": 0.7,
})
ROUTE_DRAFT_JSON = json.dumps({
    "route_name": "RL", "plan_name": "RL 计划", "summary": "s",
    "phases": [
        {"name": "P1", "goal": "g", "order": 1, "topics": [
            {"name": "T1", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 1},
            {"name": "T2", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 2},
        ]},
        {"name": "P2", "goal": "g", "order": 2, "topics": [
            {"name": "T3", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 1},
            {"name": "T4", "description": "d", "estimated_minutes": 30,
             "priority": 3, "order": 2},
        ]},
    ],
})
SUGGEST_JSON = json.dumps({"suggested_route_names": [], "reason": "r"})
RESUME_JSON = json.dumps({"keywords": [], "bullets": [], "summary": ""})


class TestTaskReviewUsesOverride:
    def test_system_and_user(self, prompt_registry):
        prompt_registry.set_override("task_review.system", "SENTINEL_R_SYS")
        prompt_registry.set_override(
            "task_review.user",
            "SENTINEL_R_USER {{task_title}} {{reason}} {{output_instruction}}",
        )
        client = CapturingClient(REVIEW_JSON)
        svc = TaskReviewService(client, prompt_registry=prompt_registry)
        svc.review_task(_task(), "没时间")
        system, user = client.calls[0]
        assert system == "SENTINEL_R_SYS"
        assert "SENTINEL_R_USER" in user and "读书" in user

    def test_reset_falls_back_to_default(self, prompt_registry):
        prompt_registry.set_override("task_review.system", "X")
        prompt_registry.reset("task_review.system")
        client = CapturingClient(REVIEW_JSON)
        TaskReviewService(client, prompt_registry=prompt_registry).review_task(
            _task(), "没时间"
        )
        assert "SENTINEL" not in client.calls[0][0]


class TestPlannerUsesOverride:
    def test_planner_prompts(self, prompt_registry):
        prompt_registry.set_override("planner.system", "SENTINEL_PL_SYS")
        prompt_registry.set_override(
            "planner.user",
            "SENTINEL_PL_USER {{context_json}} {{output_instruction}}",
        )
        client = CapturingClient(PLAN_JSON)
        AIPlanner(client, prompt_registry=prompt_registry).plan_next_day(
            PlanningContext(current_date="2026-01-05")
        )
        system, user = client.calls[0]
        assert system == "SENTINEL_PL_SYS"
        assert "SENTINEL_PL_USER" in user


class TestAssessmentUsesOverride:
    def test_generate(self, prompt_registry):
        prompt_registry.set_override("assessment.generate.system", "SENTINEL_A_SYS")
        prompt_registry.set_override(
            "assessment.generate.user",
            "SENTINEL_A_USER {{knowledge_point_name}} {{output_instruction}}",
        )
        client = CapturingClient(QUESTIONS_JSON)
        svc = AssessmentService(client, prompt_registry=prompt_registry)
        svc.generate_questions({"name": "装饰器", "description": ""})
        system, user = client.calls[0]
        assert system == "SENTINEL_A_SYS"
        assert "SENTINEL_A_USER" in user and "装饰器" in user

    def test_judge(self, prompt_registry):
        prompt_registry.set_override("assessment.judge.system", "SENTINEL_J_SYS")
        prompt_registry.set_override(
            "assessment.judge.user",
            "SENTINEL_J_USER {{questions_json}} {{answers_json}} "
            "{{output_instruction}}",
        )
        client = CapturingClient(JUDGMENT_JSON)
        svc = AssessmentService(client, prompt_registry=prompt_registry)
        svc._judge([{"question": "q", "type": "concept", "expected_points": 1}],
                   ["a"])
        system, user = client.calls[0]
        assert system == "SENTINEL_J_SYS"
        assert "SENTINEL_J_USER" in user



class TestRouteBuilderUsesOverride:
    def test_build_draft(self, prompt_registry):
        prompt_registry.set_override("route_builder.system", "SENTINEL_RB_SYS")
        prompt_registry.set_override(
            "route_builder.user", "SENTINEL_RB_USER {{route_context_json}}"
        )
        client = CapturingClient(ROUTE_DRAFT_JSON)
        AIRouteBuilderService(client, prompt_registry=prompt_registry).build_draft(
            {"route_name": "RL"}
        )
        system, user = client.calls[0]
        assert system == "SENTINEL_RB_SYS"
        assert "SENTINEL_RB_USER" in user

    def test_suggest_routes(self, prompt_registry):
        prompt_registry.set_override("route_suggestion.system", "SENTINEL_RS_SYS")
        prompt_registry.set_override(
            "route_suggestion.user",
            "SENTINEL_RS_USER {{candidate_name}} {{routes_json}}",
        )
        client = CapturingClient(SUGGEST_JSON)
        AIRouteBuilderService(client, prompt_registry=prompt_registry).suggest_routes(
            "Docker", [{"name": "RL", "id": 1}]
        )
        system, user = client.calls[0]
        assert system == "SENTINEL_RS_SYS"
        assert "SENTINEL_RS_USER" in user


class TestJdParseUsesOverride:
    def test_jd_parse(self, prompt_registry):
        prompt_registry.set_override("jd_parse.system", "SENTINEL_JD_SYS")
        prompt_registry.set_override(
            "jd_parse.user", "SENTINEL_JD_USER {{jd_text}} {{output_format}}"
        )
        client = CapturingClient('{"direction":"","must":[],"plus":[],"intern":false}')
        parse = build_default_parse_ai(client, prompt_registry)
        parse("原始 JD 文本")
        system, user = client.calls[0]
        assert system == "SENTINEL_JD_SYS"
        assert "SENTINEL_JD_USER" in user and "原始 JD 文本" in user


class TestResumeUsesOverride:
    def test_resume(self, prompt_registry):
        prompt_registry.set_override("resume_material.system", "SENTINEL_RES_SYS")
        prompt_registry.set_override(
            "resume_material.user",
            "SENTINEL_RES_USER {{outcomes_json}} {{output_format}}",
        )
        client = CapturingClient(RESUME_JSON)
        svc = LearningOutcomeService(
            outcome_repo=None, ai_client=client, prompt_registry=prompt_registry
        )
        svc.build_resume_material(outcomes=[{"title": "t", "kind": "project"}])
        system, user = client.calls[0]
        assert system == "SENTINEL_RES_SYS"
        assert "SENTINEL_RES_USER" in user
