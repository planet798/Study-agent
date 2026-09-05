"""周/月总结 AI schema 测试 + AISummaryGenerator 测试。"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.summary import AISummaryGenerator
from app.ai.schemas import (
    MonthlySummary,
    WeeklySummary,
    parse_monthly_from_json,
    parse_monthly_summary,
    parse_weekly_from_json,
    parse_weekly_summary,
)

VALID_WEEKLY = {
    "overview": "本周整体完成良好",
    "strengths": ["Python 完成率高", "坚持每天学习"],
    "problems": ["算法延期较多"],
    "recommendations": ["拆解大任务"],
    "next_week_focus": ["加强算法"],
}


class _FakeClient:
    def __init__(self, content=None, error=None, configured=True):
        self._content = content
        self._error = error
        self._configured = configured

    def is_configured(self):
        return self._configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        if self._error is not None:
            raise self._error
        return self._content


class TestWeeklySchema:
    def test_valid_weekly(self):
        w = parse_weekly_summary(VALID_WEEKLY)
        assert isinstance(w, WeeklySummary)
        assert w.overview == "本周整体完成良好"
        assert w.strengths == ("Python 完成率高", "坚持每天学习")

    def test_valid_weekly_from_json(self):
        w = parse_weekly_from_json(json.dumps(VALID_WEEKLY, ensure_ascii=False))
        assert w.problems == ("算法延期较多",)

    def test_invalid_json(self):
        with pytest.raises(AIServiceError):
            parse_weekly_from_json("not json")

    def test_missing_field(self):
        d = dict(VALID_WEEKLY)
        del d["recommendations"]
        with pytest.raises(AIServiceError):
            parse_weekly_summary(d)

    def test_strengths_not_list(self):
        d = dict(VALID_WEEKLY)
        d["strengths"] = "not a list"
        with pytest.raises(AIServiceError):
            parse_weekly_summary(d)

    def test_empty_list_item_rejected(self):
        d = dict(VALID_WEEKLY)
        d["next_week_focus"] = [""]
        with pytest.raises(AIServiceError):
            parse_weekly_summary(d)


VALID_MONTHLY = {
    "overview": "本月稳步推进",
    "progress": "从 Python 进入 NumPy",
    "strengths": ["坚持性好"],
    "weaknesses": ["周后期效率下降"],
    "recommendations": ["优化作息"],
    "next_month_focus": ["巩固 PyTorch"],
}


class TestMonthlySchema:
    def test_valid_monthly(self):
        m = parse_monthly_summary(VALID_MONTHLY)
        assert isinstance(m, MonthlySummary)
        assert m.progress == "从 Python 进入 NumPy"
        assert m.weaknesses == ("周后期效率下降",)

    def test_valid_monthly_from_json(self):
        m = parse_monthly_from_json(json.dumps(VALID_MONTHLY, ensure_ascii=False))
        assert m.recommendations == ("优化作息",)

    def test_invalid_json(self):
        with pytest.raises(AIServiceError):
            parse_monthly_from_json("<html>")

    def test_missing_progress(self):
        d = dict(VALID_MONTHLY)
        del d["progress"]
        with pytest.raises(AIServiceError):
            parse_monthly_summary(d)


class TestSummaryGenerator:
    def test_weekly_generation(self):
        client = _FakeClient(content=json.dumps(VALID_WEEKLY, ensure_ascii=False))
        gen = AISummaryGenerator(client)
        out = gen.generate_weekly({"total_tasks": 10})
        assert isinstance(out, WeeklySummary)
        assert out.overview == "本周整体完成良好"

    def test_monthly_generation(self):
        client = _FakeClient(content=json.dumps(VALID_MONTHLY, ensure_ascii=False))
        gen = AISummaryGenerator(client)
        out = gen.generate_monthly({"total_tasks": 30})
        assert isinstance(out, MonthlySummary)

    def test_ai_failure_raises_aiserviceerror(self):
        client = _FakeClient(error=AIServiceError("AI 请求超时"))
        gen = AISummaryGenerator(client)
        with pytest.raises(AIServiceError):
            gen.generate_weekly({"total_tasks": 1})

    def test_not_configured_raises(self):
        gen = AISummaryGenerator(_FakeClient(configured=False))
        with pytest.raises(AIServiceError):
            gen.generate_weekly({"total_tasks": 1})

    def test_invalid_content_raises(self):
        client = _FakeClient(content="garbage")
        gen = AISummaryGenerator(client)
        with pytest.raises(AIServiceError):
            gen.generate_monthly({"total_tasks": 1})
