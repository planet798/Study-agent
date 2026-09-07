"""验收题生成（Phase 3C）测试。

覆盖：
- 正常生成：AI 返回合法 JSON -> 解析出客观题集合
- questions_json 可落库（assessment_attempts.questions_json）
- Schema 校验：非法 JSON / 缺字段 / 类型错 / 分值越界 / 空列表
- 空知识点 / 异常输入
- AI 未配置 / AI 调用失败 / AI 返回非法结构 的错误处理
"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import (
    parse_assessment_questions_from_json,
)
from app.services.assessment_service import AssessmentService


class FakeClient:
    """记录 prompt 并返回给定内容的假 AI client。"""

    def __init__(self, content="", configured=True, error=None):
        self.content = content
        self.configured = configured
        self.error = error
        self.calls = []

    def is_configured(self):
        return self.configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return self.content


def _content(questions):
    return json.dumps({"questions": questions}, ensure_ascii=False)


VALID_QUESTIONS = [
    {"question": "解释 requires_grad=True 的作用", "type": "concept",
     "expected_points": 3},
    {"question": "读代码：这段代码里 zero_grad 在循环外可能导致什么问题？",
     "type": "code_reading", "expected_points": 4},
    {"question": "写一个最小线性回归训练循环", "type": "coding",
     "expected_points": 5},
    {"question": "找出下面梯度累积代码的错误", "type": "debug",
     "expected_points": 4},
    {"question": "在给定场景下选择合适的损失函数并说明理由", "type": "scenario",
     "expected_points": 3},
]


class TestNormalGeneration:
    def test_generate_valid_questions(self):
        client = FakeClient(content=_content(VALID_QUESTIONS))
        svc = AssessmentService(client)

        result = svc.generate_questions(
            {"name": "pytorch.autograd", "description": "自动求导"},
            num_questions=5,
        )

        assert len(result.questions) == 5
        qtypes = {q.type for q in result.questions}
        assert qtypes <= {"concept", "code_reading", "coding", "debug", "scenario"}
        # expected_points 必须为正整数
        assert all(isinstance(q.expected_points, int) for q in result.questions)
        assert all(q.expected_points >= 1 for q in result.questions)
        # prompt 包含知识点名与目标题数
        _, user_prompt = client.calls[0]
        assert "pytorch.autograd" in user_prompt
        assert "5" in user_prompt

    def test_questions_json_round_trip(self):
        client = FakeClient(content=_content(VALID_QUESTIONS))
        svc = AssessmentService(client)
        result = svc.generate_questions({"name": "pytorch.autograd"})

        data = json.loads(result.questions_json())
        assert isinstance(data, list)
        assert len(data) == 5
        for item in data:
            assert set(item) == {"question", "type", "expected_points"}


class TestSchemaValidation:
    def test_invalid_json(self):
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json("{ not json")

    def test_missing_questions_field(self):
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json('{"foo": 1}')

    def test_questions_not_list(self):
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json('{"questions": "x"}')

    def test_empty_questions(self):
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json('{"questions": []}')

    def test_question_missing_field(self):
        content = _content([{"question": "只有一个空题", "type": "concept"}])
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json(content)

    def test_invalid_type(self):
        content = _content([
            {"question": "x", "type": "self_assessment", "expected_points": 1}
        ])
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json(content)

    def test_points_out_of_range(self):
        content = _content([
            {"question": "x", "type": "concept", "expected_points": 0}
        ])
        with pytest.raises(AIServiceError):
            parse_assessment_questions_from_json(content)


class TestAbnormalInput:
    def test_empty_knowledge_point_dict(self):
        client = FakeClient(content=_content(VALID_QUESTIONS))
        svc = AssessmentService(client)
        with pytest.raises(ValueError):
            svc.generate_questions({})
        assert client.calls == []

    def test_blank_name(self):
        client = FakeClient(content=_content(VALID_QUESTIONS))
        svc = AssessmentService(client)
        with pytest.raises(ValueError):
            svc.generate_questions({"name": "   "})
        assert client.calls == []

    def test_non_dict_non_convertible(self):
        client = FakeClient(content=_content(VALID_QUESTIONS))
        svc = AssessmentService(client)
        with pytest.raises(ValueError):
            svc.generate_questions("not a knowledge point")
        assert client.calls == []


class TestErrorHandling:
    def test_ai_not_configured(self):
        client = FakeClient(configured=False)
        svc = AssessmentService(client)
        with pytest.raises(AIServiceError):
            svc.generate_questions({"name": "pytorch.autograd"})
        assert client.calls == []

    def test_ai_call_failure_propagates(self):
        client = FakeClient(content="", error=AIServiceError("AI 超时"))
        svc = AssessmentService(client)
        with pytest.raises(AIServiceError):
            svc.generate_questions({"name": "pytorch.autograd"})

    def test_ai_invalid_structure(self):
        client = FakeClient(content='{"unexpected": true}')
        svc = AssessmentService(client)
        with pytest.raises(AIServiceError):
            svc.generate_questions({"name": "pytorch.autograd"})
