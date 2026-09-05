"""schemas.py 测试：结构化输出校验。"""

from __future__ import annotations

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import TaskReview, parse_review, parse_review_from_json

VALID = {
    "reasonable": True,
    "score": 0.85,
    "should_postpone": True,
    "suggested_date": "2026-09-05",
    "analysis": "突发课程冲突，原因合理",
    "suggestion": "建议延期一天再补上",
}


class TestParseValid:
    def test_valid_json_parses(self):
        r = parse_review(VALID)
        assert isinstance(r, TaskReview)
        assert r.reasonable is True
        assert r.score == 0.85
        assert r.should_postpone is True
        assert r.suggested_date == "2026-09-05"
        assert r.analysis == "突发课程冲突，原因合理"

    def test_valid_no_postpone_null_date(self):
        data = dict(VALID)
        data.update(
            {"reasonable": False, "should_postpone": False, "suggested_date": None}
        )
        r = parse_review(data)
        assert r.should_postpone is False
        assert r.suggested_date is None

    def test_valid_from_json_string(self):
        r = parse_review_from_json('{"reasonable": false, "score": 0.2, '
                                   '"should_postpone": false, "suggested_date": null, '
                                   '"analysis": "a", "suggestion": "b"}')
        assert r.reasonable is False
        assert r.score == 0.2


class TestParseInvalidJson:
    def test_not_a_dict(self):
        with pytest.raises(AIServiceError):
            parse_review([1, 2, 3])

    def test_invalid_json_string(self):
        with pytest.raises(AIServiceError):
            parse_review_from_json("这不是 JSON")

    def test_missing_field(self):
        data = dict(VALID)
        del data["analysis"]
        with pytest.raises(AIServiceError) as exc:
            parse_review(data)
        assert "analysis" in str(exc.value)

    def test_reasonable_not_bool(self):
        data = dict(VALID)
        data["reasonable"] = "yes"
        with pytest.raises(AIServiceError):
            parse_review(data)


class TestScoreValidation:
    def test_score_below_zero_rejected(self):
        data = dict(VALID)
        data["score"] = -0.1
        with pytest.raises(AIServiceError) as exc:
            parse_review(data)
        assert "score" in str(exc.value)

    def test_score_above_one_rejected(self):
        data = dict(VALID)
        data["score"] = 1.5
        with pytest.raises(AIServiceError):
            parse_review(data)

    def test_score_not_number_rejected(self):
        data = dict(VALID)
        data["score"] = "high"
        with pytest.raises(AIServiceError):
            parse_review(data)


class TestDateValidation:
    def test_bad_format_rejected(self):
        data = dict(VALID)
        data["suggested_date"] = "09-05-2026"
        with pytest.raises(AIServiceError) as exc:
            parse_review(data)
        assert "suggested_date" in str(exc.value)

    def test_impossible_date_rejected(self):
        data = dict(VALID)
        data["suggested_date"] = "2026-13-40"
        with pytest.raises(AIServiceError):
            parse_review(data)

    def test_postpone_true_requires_date(self):
        data = dict(VALID)
        data["should_postpone"] = True
        data["suggested_date"] = None
        with pytest.raises(AIServiceError) as exc:
            parse_review(data)
        assert "suggested_date" in str(exc.value)

    def test_postpone_false_allows_null(self):
        data = dict(VALID)
        data["should_postpone"] = False
        data["suggested_date"] = None
        parse_review(data)  # 不应抛异常
