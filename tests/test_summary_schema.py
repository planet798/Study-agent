"""月总结 AI schema 测试 + AISummaryGenerator 测试。"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.summary import AISummaryGenerator
from app.ai.schemas import (
    MonthlySummary,
    parse_monthly_from_json,
    parse_monthly_summary,
)


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
    def test_monthly_generation(self):
        client = _FakeClient(content=json.dumps(VALID_MONTHLY, ensure_ascii=False))
        gen = AISummaryGenerator(client)
        out = gen.generate_monthly({"total_tasks": 30})
        assert isinstance(out, MonthlySummary)

    def test_ai_failure_raises_aiserviceerror(self):
        client = _FakeClient(error=AIServiceError("AI 请求超时"))
        gen = AISummaryGenerator(client)
        with pytest.raises(AIServiceError):
            gen.generate_monthly({"total_tasks": 1})

    def test_not_configured_raises(self):
        gen = AISummaryGenerator(_FakeClient(configured=False))
        with pytest.raises(AIServiceError):
            gen.generate_monthly({"total_tasks": 1})

    def test_invalid_content_raises(self):
        client = _FakeClient(content="garbage")
        gen = AISummaryGenerator(client)
        with pytest.raises(AIServiceError):
            gen.generate_monthly({"total_tasks": 1})
