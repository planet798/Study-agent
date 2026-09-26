"""验收闭环（Phase 3D）测试。

覆盖：
- start_assessment：生成题目并建一条 pending 记录（含 task_id / knowledge_point_id）
- submit_answers：
  - 正常提交 + AI 判题 + 结果/薄弱点/掌握度落库
  - answers_json 保存且不覆盖 questions_json
  - 知识点掌握度回写（首次直接采用，后续平滑更新）
  - 历史验收记录保留
  - 重复/重新判题
- 答案数量/格式错误
- AI 判题失败：保留答案、标记 failed、不伪造 mastery、可稍后重判
- 判题 JSON Schema 校验
"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.schemas import parse_assessment_judgment_from_json
from app.database.assessment_repository import AssessmentRepository
from app.services.assessment_service import AssessmentService

QUESTIONS = [
    {"question": "解释 requires_grad=True 的作用", "type": "concept",
     "expected_points": 3},
    {"question": "写一个最小梯度计算程序", "type": "coding",
     "expected_points": 5},
]
QUESTIONS_JSON = json.dumps(QUESTIONS, ensure_ascii=False)

QUESTIONS_CONTENT = json.dumps({"questions": QUESTIONS}, ensure_ascii=False)

JUDGMENT = {
    "questions": [
        {"question_index": 0, "verdict": "correct", "reason": "概念正确"},
        {"question_index": 1, "verdict": "partial", "reason": "缺少 zero_grad"},
    ],
    "weak_points": ["zero_grad", "gradient accumulation"],
    "result_level": "good",
    "mastery_estimate": 0.72,
}
JUDGMENT_CONTENT = json.dumps(JUDGMENT, ensure_ascii=False)


class FakeClient:
    """按顺序返回预设响应，并记录 prompt。"""

    def __init__(self, responses=None, configured=True, error=None):
        self.responses = list(responses) if responses else []
        self.configured = configured
        self.error = error
        self.calls = []

    def is_configured(self):
        return self.configured

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AIServiceError("没有更多预设响应")
        return self.responses.pop(0)


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


def _make_kp(repo) -> int:
    return repo.create_knowledge_point(
        "pytorch.autograd", "PyTorch 自动求导"
    )["id"]


def _make_attempt(repo, kp_id: int, task_id: int | None = None) -> int:
    return repo.create_attempt(
        kp_id, QUESTIONS_JSON, task_id=task_id
    )["id"]


class TestStartAssessment:
    def test_start_assessment_creates_pending_attempt(self, assessment_repo):
        client = FakeClient(responses=[QUESTIONS_CONTENT])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)

        result = svc.start_assessment(kp_id, task_id=5, num_questions=2)

        assert result["knowledge_point_id"] == kp_id
        assert result["task_id"] == 5
        assert result["judge_status"] == "pending"
        assert json.loads(result["questions_json"]) == QUESTIONS
        assert len(result["questions"]) == 2

    def test_start_assessment_unknown_knowledge_point(self, assessment_repo):
        client = FakeClient(responses=[QUESTIONS_CONTENT])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        with pytest.raises(ValueError):
            svc.start_assessment(99999)


class TestSubmitAnswersNormal:
    def test_submit_full_cycle(self, assessment_repo):
        client = FakeClient(responses=[JUDGMENT_CONTENT])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id, task_id=7)

        result = svc.submit_answers(attempt_id, ["概念正确", "代码缺 zero_grad"])

        # 答案保存，题目未被覆盖
        assert json.loads(result["answers_json"]) == [
            "概念正确", "代码缺 zero_grad"
        ]
        assert json.loads(result["questions_json"]) == QUESTIONS

        # 判题结果落库
        assert result["judge_status"] == "judged"
        assert result["judge_error"] == ""
        assert result["result_level"] == "good"
        assert result["mastery_estimate"] == 0.72
        assert json.loads(result["weak_points_json"]) == [
            "zero_grad", "gradient accumulation"
        ]
        assert json.loads(result["ai_result_json"])["questions"][0]["verdict"] == "correct"

        # 知识点掌握度回写（首次直接采用）
        kp = assessment_repo.get_knowledge_point(kp_id)
        assert kp["mastery_estimate"] == 0.72
        assert kp["review_count"] == 0
        assert kp["last_assessed_at"] is not None

    def test_second_submission_blends_mastery(self, assessment_repo):
        first = dict(JUDGMENT, mastery_estimate=0.9)
        second = dict(JUDGMENT, mastery_estimate=0.3, result_level="poor")
        client = FakeClient(responses=[
            json.dumps(first, ensure_ascii=False),
            json.dumps(second, ensure_ascii=False),
        ])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)

        svc.submit_answers(attempt_id, ["a1", "a2"])
        svc.submit_answers(attempt_id, ["b1", "b2"])

        kp = assessment_repo.get_knowledge_point(kp_id)
        # 0.7 * 0.9 + 0.3 * 0.3 = 0.72
        assert kp["mastery_estimate"] == pytest.approx(0.72, abs=1e-4)
        assert kp["review_count"] == 0

    def test_history_attempts_preserved(self, assessment_repo):
        client = FakeClient(responses=[QUESTIONS_CONTENT, QUESTIONS_CONTENT])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)

        svc.start_assessment(kp_id)
        svc.start_assessment(kp_id)

        attempts = assessment_repo.list_attempts_for_knowledge_point(kp_id)
        assert len(attempts) == 2  # 历史记录保留，不被删除


class TestSubmitValidation:
    def test_answer_count_mismatch(self, assessment_repo):
        svc = AssessmentService(FakeClient(), assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)

        with pytest.raises(ValueError):
            svc.submit_answers(attempt_id, ["只有一个答案"])

        attempt = assessment_repo.get_attempt(attempt_id)
        assert attempt["answers_json"] == ""  # 校验失败不写答案
        assert attempt["judge_status"] == "pending"

    def test_answers_not_list(self, assessment_repo):
        svc = AssessmentService(FakeClient(), assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)
        with pytest.raises(ValueError):
            svc.submit_answers(attempt_id, "不是数组")

    def test_blank_answer_rejected(self, assessment_repo):
        svc = AssessmentService(FakeClient(), assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)
        with pytest.raises(ValueError):
            svc.submit_answers(attempt_id, ["ok", "   "])

    def test_unknown_attempt(self, assessment_repo):
        svc = AssessmentService(FakeClient(), assessment_repo=assessment_repo)
        with pytest.raises(ValueError):
            svc.submit_answers(99999, ["a", "b"])


class TestJudgeFailure:
    def test_ai_not_configured_marks_failed_preserves_answers(self, assessment_repo):
        client = FakeClient(configured=False)
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)

        result = svc.submit_answers(attempt_id, ["a", "b"])

        assert json.loads(result["answers_json"]) == ["a", "b"]  # 答案保留
        assert result["judge_status"] == "failed"
        assert result["judge_error"] != ""
        assert result["mastery_estimate"] is None  # 不伪造 mastery

        kp = assessment_repo.get_knowledge_point(kp_id)
        assert kp["mastery_estimate"] == 0.0
        assert kp["review_count"] == 0
        assert kp["last_assessed_at"] is None

    def test_ai_call_failure_marks_failed(self, assessment_repo):
        client = FakeClient(error=AIServiceError("AI 超时"))
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)

        result = svc.submit_answers(attempt_id, ["a", "b"])

        assert result["judge_status"] == "failed"
        assert "AI 超时" in result["judge_error"]
        assert json.loads(result["answers_json"]) == ["a", "b"]

    def test_ai_invalid_structure_marks_failed(self, assessment_repo):
        client = FakeClient(responses=['{"unexpected": true}'])
        svc = AssessmentService(client, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)

        result = svc.submit_answers(attempt_id, ["a", "b"])

        assert result["judge_status"] == "failed"
        assert result["mastery_estimate"] is None

    def test_failed_attempt_can_be_rejudged(self, assessment_repo):
        """先失败，随后换一个可用 client 重新提交即可成功判题。"""
        failing = FakeClient(error=AIServiceError("网络错误"))
        svc_fail = AssessmentService(failing, assessment_repo=assessment_repo)
        kp_id = _make_kp(assessment_repo)
        attempt_id = _make_attempt(assessment_repo, kp_id)
        svc_fail.submit_answers(attempt_id, ["a", "b"])

        working = FakeClient(responses=[JUDGMENT_CONTENT])
        svc_ok = AssessmentService(working, assessment_repo=assessment_repo)
        result = svc_ok.submit_answers(attempt_id, ["a2", "b2"])

        assert result["judge_status"] == "judged"
        assert result["mastery_estimate"] == 0.72
        kp = assessment_repo.get_knowledge_point(kp_id)
        assert kp["review_count"] == 0


class TestJudgmentSchema:
    def test_valid(self):
        j = parse_assessment_judgment_from_json(JUDGMENT_CONTENT)
        assert j.result_level == "good"
        assert j.mastery_estimate == 0.72
        assert len(j.question_judgments) == 2
        assert j.weak_points == ("zero_grad", "gradient accumulation")

    @pytest.mark.parametrize("bad", [
        "{ not json",
        '{"weak_points": [], "result_level": "good", "mastery_estimate": 0.5}',
        '{"questions": [], "weak_points": [], "result_level": "good",'
        ' "mastery_estimate": 0.5}',
        json.dumps({
            "questions": [{"question_index": 0, "verdict": "maybe",
                           "reason": "x"}],
            "weak_points": [], "result_level": "good", "mastery_estimate": 0.5,
        }),
        json.dumps({
            "questions": [{"question_index": 0, "verdict": "correct",
                           "reason": "x"}],
            "weak_points": [], "result_level": "super", "mastery_estimate": 0.5,
        }),
        json.dumps({
            "questions": [{"question_index": 0, "verdict": "correct",
                           "reason": "x"}],
            "weak_points": [], "result_level": "good", "mastery_estimate": 1.5,
        }),
    ])
    def test_invalid(self, bad):
        with pytest.raises(AIServiceError):
            parse_assessment_judgment_from_json(bad)
