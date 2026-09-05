"""AIPlanner 与规划 schema 测试：用假 client，不依赖真实 API。"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.planner import AIPlanner
from app.ai.planner_context import PlanningContext
from app.ai.schemas import (
    DailyPlan,
    RecommendedTask,
    parse_daily_plan,
    parse_daily_plan_from_json,
)


class FakeClient:
    def __init__(self, content=None, error=None, configured=True):
        self._content = content
        self._error = error
        self._configured = configured
        self.calls = []

    def is_configured(self):
        return self._configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        if self._error is not None:
            raise self._error
        return self._content


def _valid_plan_json(**overrides) -> str:
    base = {
        "reasoning": "最近完成率稳定，逐步增加难度",
        "recommended_tasks": [
            {
                "topic_id": 1,
                "title": "Python 函数练习",
                "description": "练习常见内置函数",
                "estimated_minutes": 45,
                "priority": 3,
            }
        ],
        "carry_over_tasks": [
            {"task_id": 10, "reason": "最近学习时间不足"}
        ],
        "daily_minutes": 135,
        "adjustment": "比昨天增加一个任务",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _ctx() -> PlanningContext:
    return PlanningContext(current_date="2026-09-05")


class TestSchemaValid:
    def test_normal_plan_json(self):
        plan = parse_daily_plan_from_json(_valid_plan_json())
        assert isinstance(plan, DailyPlan)
        assert plan.daily_minutes == 135
        assert len(plan.recommended_tasks) == 1
        assert plan.recommended_tasks[0].topic_id == 1
        assert plan.carry_over_tasks[0].task_id == 10

    def test_empty_carry_over_allowed(self):
        plan = parse_daily_plan(json.loads(_valid_plan_json(carry_over_tasks=[])))
        assert plan.carry_over_tasks == ()


class TestSchemaInvalidJson:
    def test_not_json(self):
        with pytest.raises(AIServiceError):
            parse_daily_plan_from_json("这不是 JSON")

    def test_not_object(self):
        with pytest.raises(AIServiceError):
            parse_daily_plan([1, 2, 3])

    def test_missing_field(self):
        data = json.loads(_valid_plan_json())
        del data["adjustment"]
        with pytest.raises(AIServiceError):
            parse_daily_plan(data)


class TestSchemaConstraints:
    def test_empty_recommended_rejected(self):
        with pytest.raises(AIServiceError):
            parse_daily_plan(
                json.loads(_valid_plan_json(recommended_tasks=[]))
            )

    def test_too_many_recommended_rejected(self):
        recs = [
            {"topic_id": i, "title": f"t{i}", "estimated_minutes": 10}
            for i in range(1, 7)
        ]
        with pytest.raises(AIServiceError):
            parse_daily_plan(json.loads(_valid_plan_json(recommended_tasks=recs)))

    def test_daily_minutes_over_180_rejected(self):
        with pytest.raises(AIServiceError):
            parse_daily_plan(json.loads(_valid_plan_json(daily_minutes=220)))

    def test_daily_minutes_zero_rejected(self):
        with pytest.raises(AIServiceError):
            parse_daily_plan(json.loads(_valid_plan_json(daily_minutes=0)))

    def test_estimated_minutes_invalid_rejected(self):
        data = json.loads(_valid_plan_json())
        data["recommended_tasks"][0]["estimated_minutes"] = 0
        with pytest.raises(AIServiceError):
            parse_daily_plan(data)

    def test_missing_topic_id_rejected(self):
        data = json.loads(_valid_plan_json())
        del data["recommended_tasks"][0]["topic_id"]
        with pytest.raises(AIServiceError):
            parse_daily_plan(data)


class TestAIPlanner:
    def test_uses_client_and_returns_plan(self):
        client = FakeClient(content=_valid_plan_json())
        planner = AIPlanner(client)
        plan = planner.plan_next_day(_ctx())
        assert isinstance(plan, DailyPlan)
        assert len(client.calls) == 1
        sys_prompt, user_prompt = client.calls[0]
        assert "学习规划助手" in sys_prompt
        assert "2026-09-05" in user_prompt

    def test_not_configured(self):
        planner = AIPlanner(FakeClient(configured=False))
        with pytest.raises(AIServiceError):
            planner.plan_next_day(_ctx())

    def test_timeout_propagated_as_aiserviceerror(self):
        client = FakeClient(error=AIServiceError("AI 请求超时"))
        planner = AIPlanner(client)
        with pytest.raises(AIServiceError) as exc:
            planner.plan_next_day(_ctx())
        assert "超时" in str(exc.value)

    def test_http_error_propagated(self):
        client = FakeClient(error=AIServiceError("AI HTTP 错误 500: boom"))
        planner = AIPlanner(client)
        with pytest.raises(AIServiceError):
            planner.plan_next_day(_ctx())

    def test_invalid_ai_content_rejected(self):
        client = FakeClient(content="hahaha")
        planner = AIPlanner(client)
        with pytest.raises(AIServiceError):
            planner.plan_next_day(_ctx())
