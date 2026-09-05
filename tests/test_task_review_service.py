"""task_review_service 测试：用假 AIClient，不依赖真实 API。"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import TaskReview
from app.services.task_review_service import TaskReviewService


class FakeClient:
    """实现 AIClient 接口的假客户端。"""

    def __init__(self, configured=True, content=None, error=None):
        self._configured = configured
        self._content = content
        self._error = error
        self.calls = []

    def is_configured(self) -> bool:
        return self._configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        if self._error is not None:
            raise self._error
        return self._content


def _review_json(**overrides) -> str:
    base = {
        "reasonable": True,
        "score": 0.85,
        "should_postpone": True,
        "suggested_date": "2026-09-05",
        "analysis": "突发课程冲突，原因合理",
        "suggestion": "建议延期一天再补上",
    }
    base.update(overrides)
    return json.dumps(base)


def _make_task():
    from app.database.repository import Task

    return Task(
        id=1,
        title="复习数学",
        description="第三章",
        category="学习",
        estimated_minutes=90,
        priority=3,
        status="active",
        scheduled_date="2026-09-04",
        postpone_count=0,
        created_at="",
        updated_at="",
    )


class TestReviewService:
    def test_not_configured(self):
        svc = TaskReviewService(FakeClient(configured=False))
        assert svc.is_configured() is False
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "生病了")

    def test_success_returns_taskreview(self):
        client = FakeClient(content=_review_json())
        svc = TaskReviewService(client)
        review = svc.review_task(_make_task(), "突发课程冲突")
        assert isinstance(review, TaskReview)
        assert review.should_postpone is True
        assert review.score == 0.85
        # 确认 prompt 拼装发生了
        assert len(client.calls) == 1
        sys_prompt, user_prompt = client.calls[0]
        assert "学习计划辅助助手" in sys_prompt
        assert "复习数学" in user_prompt
        assert "突发课程冲突" in user_prompt

    def test_invalid_json_content_rejected(self):
        client = FakeClient(content="这不是 JSON")
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "原因")

    def test_score_out_of_range_rejected(self):
        client = FakeClient(content=_review_json(score=2.0))
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "原因")

    def test_bad_date_rejected(self):
        client = FakeClient(content=_review_json(suggested_date="05-09-2026"))
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "原因")

    def test_client_error_propagated_as_aiserviceerror(self):
        client = FakeClient(error=AIServiceError("AI 网络错误: boom"))
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError) as exc:
            svc.review_task(_make_task(), "原因")
        assert "boom" in str(exc.value)

    def test_unexpected_client_exception_wrapped(self):
        client = FakeClient(error=RuntimeError("意外错误"))
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "原因")

    def test_empty_reason_rejected(self):
        client = FakeClient(content=_review_json())
        svc = TaskReviewService(client)
        with pytest.raises(AIServiceError):
            svc.review_task(_make_task(), "   ")
