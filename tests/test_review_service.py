"""复习调度（Phase 4）测试。

覆盖：
- 到期 / 未到期知识点识别
- 首次验收初始间隔、不同结果调整间隔
- 生成复习任务并与 knowledge_point_id / task_id / source_attempt_id 关联
- 同日去重、未完成复习任务去重
- 每日复习预算
- 验收完成后更新下一次复习并关闭调度
- 异常数据（无验收证据/无 next_review_date）不进入复习
- 与 Phase 3D 验收闭环联动
"""

from __future__ import annotations

import json

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.schema import STATUS_ACTIVE
from app.services.assessment_service import AssessmentService
from app.services.review_service import (
    DEFAULT_MAX_DAILY_REVIEWS,
    ReviewService,
)

QUESTIONS = [
    {"question": "解释 autograd", "type": "concept", "expected_points": 3},
]
QUESTIONS_CONTENT = json.dumps({"questions": QUESTIONS}, ensure_ascii=False)
JUDGMENT_CONTENT = json.dumps(
    {
        "questions": [
            {"question_index": 0, "verdict": "correct", "reason": "正确"},
        ],
        "weak_points": [],
        "result_level": "good",
        "mastery_estimate": 0.8,
    },
    ensure_ascii=False,
)


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def review_service(repo, assessment_repo):
    return ReviewService(repo, assessment_repo)


def _create_kp(assessment_repo, name="pytorch.autograd", **fields):
    kp = assessment_repo.create_knowledge_point(name)
    if fields:
        kp = assessment_repo.update_knowledge_point(kp["id"], **fields)
    return kp


def _make_due_kp(assessment_repo, name="pytorch.autograd",
                 next_review_date="2026-09-06", interval_days=2,
                 mastery=0.5):
    return _create_kp(
        assessment_repo,
        name=name,
        last_assessed_at="2026-09-05T10:00:00",
        next_review_date=next_review_date,
        interval_days=interval_days,
        mastery_estimate=mastery,
        review_count=1,
    )


class TestIntervalRule:
    def test_first_assessment_intervals(self):
        assert ReviewService.next_interval(0, "excellent") == 7
        assert ReviewService.next_interval(0, "good") == 3
        assert ReviewService.next_interval(0, "ok") == 2
        assert ReviewService.next_interval(0, "poor") == 1

    def test_adjust_by_result(self):
        assert ReviewService.next_interval(2, "excellent") == 6
        assert ReviewService.next_interval(5, "good") == 10
        assert ReviewService.next_interval(5, "ok") == 6
        assert ReviewService.next_interval(6, "poor") == 3
        assert ReviewService.next_interval(1, "poor") == 1  # 下限 1

    def test_interval_capped_at_30(self):
        assert ReviewService.next_interval(15, "excellent") == 30

    def test_unknown_level_keeps_interval(self):
        assert ReviewService.next_interval(5, "未知") == 5


class TestDueIdentification:
    def test_due_when_date_passed(self, review_service, assessment_repo):
        kp = _make_due_kp(assessment_repo, next_review_date="2026-09-06")
        assert review_service.is_due(kp, "2026-09-06") is True
        assert review_service.is_due(kp, "2026-09-08") is True

    def test_not_due_when_future(self, review_service, assessment_repo):
        kp = _make_due_kp(assessment_repo, next_review_date="2026-09-10")
        assert review_service.is_due(kp, "2026-09-06") is False

    def test_not_due_without_evidence(self, review_service, assessment_repo):
        # 有 next_review_date 但没有 last_assessed_at（无验收证据）
        kp = _create_kp(
            assessment_repo, next_review_date="2026-09-06"
        )
        assert review_service.is_due(kp, "2026-09-06") is False

    def test_not_due_without_next_review_date(self, review_service, assessment_repo):
        kp = _create_kp(assessment_repo, last_assessed_at="2026-09-05T10:00:00")
        assert review_service.is_due(kp, "2026-09-06") is False

    def test_due_sorted_by_date_then_mastery(self, review_service, assessment_repo):
        a = _make_due_kp(assessment_repo, "a", next_review_date="2026-09-07", mastery=0.9)
        b = _make_due_kp(assessment_repo, "b", next_review_date="2026-09-06", mastery=0.8)
        c = _make_due_kp(assessment_repo, "c", next_review_date="2026-09-06", mastery=0.3)
        due = review_service.due_knowledge_points("2026-09-08")
        ids = [kp["id"] for kp in due]
        assert ids == [c["id"], b["id"], a["id"]]


class TestGenerateReviews:
    def test_generate_review_task_and_links(self, repo, assessment_repo,
                                            review_service):
        kp = _make_due_kp(assessment_repo, next_review_date="2026-09-06")

        result = review_service.generate_due_reviews(today="2026-09-06")

        assert len(result["created"]) == 1
        schedule = result["created"][0]
        assert schedule["knowledge_point_id"] == kp["id"]
        assert schedule["task_id"] is not None
        assert schedule["interval_days"] == 2

        task = repo.get(schedule["task_id"])
        assert task.source == "review"
        assert task.task_type == "review"
        assert task.knowledge_point_id == kp["id"]
        assert task.status == STATUS_ACTIVE

    def test_not_due_not_generated(self, repo, assessment_repo, review_service):
        _make_due_kp(assessment_repo, next_review_date="2026-09-10")
        result = review_service.generate_due_reviews(today="2026-09-06")
        assert result["created"] == []

    def test_same_day_dedup(self, repo, assessment_repo, review_service):
        _make_due_kp(assessment_repo, next_review_date="2026-09-06")
        r1 = review_service.generate_due_reviews(today="2026-09-06")
        r2 = review_service.generate_due_reviews(today="2026-09-06")
        assert len(r1["created"]) == 1
        assert r2["created"] == []

    def test_unfinished_review_task_dedup(self, repo, assessment_repo,
                                          review_service):
        kp = _make_due_kp(assessment_repo, next_review_date="2026-09-06")
        # 已有一条未完成的复习任务
        repo.create(
            title="复习旧任务", scheduled_date="2026-09-05",
            source="review", task_type="review", knowledge_point_id=kp["id"],
        )
        result = review_service.generate_due_reviews(today="2026-09-06")
        assert result["created"] == []
        assert kp["id"] in result["skipped_duplicate"]

    def test_daily_budget(self, repo, assessment_repo):
        for i in range(5):
            _make_due_kp(assessment_repo, name=f"kp{i}",
                         next_review_date="2026-09-06")
        rv = ReviewService(repo, assessment_repo, max_daily_reviews=2)
        result = rv.generate_due_reviews(today="2026-09-06")
        assert len(result["created"]) == 2
        assert len(result["skipped_budget"]) == 3

    def test_default_budget_config(self):
        assert DEFAULT_MAX_DAILY_REVIEWS > 0


class TestRecordAssessmentResult:
    def test_updates_next_review_and_closes_schedule(self, repo,
                                                     assessment_repo,
                                                     review_service):
        kp = _make_due_kp(assessment_repo, interval_days=2,
                          next_review_date="2026-09-06")
        result = review_service.generate_due_reviews(today="2026-09-06")
        schedule = result["created"][0]

        attempt = {
            "knowledge_point_id": kp["id"],
            "task_id": schedule["task_id"],
            "result_level": "good",
        }
        review_service.record_assessment_result(attempt, today="2026-09-06")

        updated = assessment_repo.get_knowledge_point(kp["id"])
        assert updated["interval_days"] == 4  # good: 2*2
        assert updated["next_review_date"] == "2026-09-10"

        closed = assessment_repo.get_review_schedule(schedule["id"])
        assert closed["status"] == "done"
        assert closed["completed_at"] is not None

    def test_first_assessment_initial_interval(self, assessment_repo,
                                               review_service):
        kp = _create_kp(
            assessment_repo,
            last_assessed_at="2026-09-05T10:00:00",
            interval_days=0,
        )
        review_service.record_assessment_result(
            {"knowledge_point_id": kp["id"], "task_id": None,
             "result_level": "excellent"},
            today="2026-09-06",
        )
        updated = assessment_repo.get_knowledge_point(kp["id"])
        assert updated["interval_days"] == 7
        assert updated["next_review_date"] == "2026-09-13"


class TestIntegrationWithAssessment:
    def test_submit_answers_schedules_next_review(self, conn, assessment_repo):
        repo = TaskRepository(conn)
        kp = _create_kp(
            assessment_repo,
            last_assessed_at="2026-09-05T10:00:00",
            interval_days=0,
        )
        rv = ReviewService(repo, assessment_repo)
        client = FakeClient(responses=[QUESTIONS_CONTENT, JUDGMENT_CONTENT])
        svc = AssessmentService(client, assessment_repo=assessment_repo,
                                review_service=rv)

        attempt = svc.start_assessment(kp["id"])
        svc.submit_answers(attempt["id"], ["autograd 用于自动求导"],
                           today="2026-09-06")

        updated = assessment_repo.get_knowledge_point(kp["id"])
        # 首次 good → 间隔 3，下一次复习 09-09
        assert updated["interval_days"] == 3
        assert updated["next_review_date"] == "2026-09-09"
        assert updated["review_count"] == 1
