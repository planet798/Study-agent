"""额外学习任务（Phase 5）测试。

覆盖：
- 正常生成（task_type='extra'、source='extra'、难度标签、时长）
- 每日预算上限
- 同日去重
- 未完成 extra 任务去重
- 与正式任务隔离（不影响 phase/topic、不影响第二天自动规划）
- 完成后可进入现有验收流程
- 无候选 / 异常情况下不产生脏数据
"""

from __future__ import annotations

import json

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.schema import STATUS_ACTIVE, STATUS_NOT_DONE
from app.services.assessment_service import AssessmentService
from app.services.date_service import DateService
from app.services.extra_task_service import (
    DIFFICULTY_LABELS,
    DIFFICULTY_MINUTES,
    ExtraTaskService,
)
from app.services.study_plan_service import StudyPlanService


class FakeClient:
    def __init__(self, content):
        self.content = content

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return self.content


QUESTIONS_CONTENT = json.dumps(
    {"questions": [{"question": "解释 autograd", "type": "concept",
                    "expected_points": 3}]},
    ensure_ascii=False,
)


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def sps(repo, conn):
    from app.database.study_plan_repository import StudyPlanRepository

    svc = StudyPlanService(repo, StudyPlanRepository(conn))
    svc.ensure_default_plan()
    return svc


@pytest.fixture()
def extra_service(repo, sps, assessment_repo):
    return ExtraTaskService(
        repo,
        study_plan_service=sps,
        assessment_repo=assessment_repo,
    )


def _focused_service(repo, assessment_repo, max_daily_extra=2):
    """只关注薄弱知识点来源的服务（不引入阶段主题候选）。"""
    return ExtraTaskService(
        repo,
        study_plan_service=None,
        assessment_repo=assessment_repo,
        max_daily_extra=max_daily_extra,
    )


def _weak_kp(assessment_repo, name="pytorch.autograd", mastery=0.2):
    kp = assessment_repo.create_knowledge_point(name)
    assessment_repo.update_knowledge_point(
        kp["id"],
        last_assessed_at="2026-09-05T10:00:00",
        mastery_estimate=mastery,
        review_count=1,
    )
    return assessment_repo.get_knowledge_point(kp["id"])


class TestNormalGeneration:
    def test_generate_extra_task(self, repo, assessment_repo):
        kp = _weak_kp(assessment_repo)
        svc = _focused_service(repo, assessment_repo)
        result = svc.generate_extra_tasks(
            today="2026-09-06", difficulty="practice"
        )

        assert len(result["created"]) == 1
        task = result["created"][0]
        assert task.task_type == "extra"
        assert task.source == "extra"
        assert task.knowledge_point_id == kp["id"]
        assert task.scheduled_date == "2026-09-06"
        assert task.difficulty == "practice"
        assert f"【额外·{DIFFICULTY_LABELS['practice']}】" in task.title
        assert task.estimated_minutes == DIFFICULTY_MINUTES["practice"]

    def test_difficulties_are_labeled_and_isolated(self, repo, extra_service,
                                                   assessment_repo):
        _weak_kp(assessment_repo, "kp1", mastery=0.1)
        _weak_kp(assessment_repo, "kp2", mastery=0.2)
        _weak_kp(assessment_repo, "kp3", mastery=0.3)

        r1 = extra_service.generate_extra_tasks(
            today="2026-09-06", difficulty="basic"
        )
        assert DIFFICULTY_LABELS["basic"] in r1["created"][0].title
        assert r1["created"][0].estimated_minutes == DIFFICULTY_MINUTES["basic"]


class TestBudgetAndDedup:
    def test_daily_budget(self, repo, assessment_repo, sps):
        for i in range(4):
            _weak_kp(assessment_repo, f"kp{i}", mastery=0.1 + i * 0.1)
        svc = ExtraTaskService(
            repo, study_plan_service=sps, assessment_repo=assessment_repo,
            max_daily_extra=2,
        )
        result = svc.generate_extra_tasks(today="2026-09-06")
        assert len(result["created"]) == 2
        assert len(result["skipped_budget"]) >= 1

    def test_same_day_dedup(self, repo, assessment_repo):
        _weak_kp(assessment_repo)
        svc = _focused_service(repo, assessment_repo)
        r1 = svc.generate_extra_tasks(today="2026-09-06")
        r2 = svc.generate_extra_tasks(today="2026-09-06")
        assert len(r1["created"]) == 1
        assert r2["created"] == []
        assert len(r2["skipped_duplicate"]) >= 1

    def test_unfinished_extra_dedup(self, repo, assessment_repo):
        kp = _weak_kp(assessment_repo)
        svc = _focused_service(repo, assessment_repo)
        # 已有一条未完成的 extra
        repo.create(
            title="【额外·实践】pytorch.autograd", scheduled_date="2026-09-05",
            category="额外学习", source="extra", task_type="extra",
            knowledge_point_id=kp["id"], difficulty="practice",
        )
        result = svc.generate_extra_tasks(today="2026-09-06")
        assert result["created"] == []
        assert len(result["skipped_duplicate"]) >= 1


class TestIsolation:
    def test_extra_isolated_from_formal_generation(
        self, repo, assessment_repo, sps
    ):
        """正式生成的任务绝不产生 task_type='extra'，extra 也不影响正式生成。"""
        _weak_kp(assessment_repo, "isolated_kp")
        svc = _focused_service(repo, assessment_repo)
        extra = svc.generate_extra_tasks(
            today="2026-09-06"
        )["created"][0]
        assert extra.task_type == "extra"
        assert extra.source == "extra"

        # 同时刻正式生成：所有正式任务都不是 extra
        res = sps.generate_daily_tasks("2026-09-06")
        assert all(t.task_type != "extra" for t in res["generated"])
        assert all(t.source != "extra" for t in res["generated"])

    def test_does_not_change_phase_or_plan(self, repo, sps, extra_service,
                                           assessment_repo):
        before_phase = sps.get_current_phase("2026-09-06")
        phase_topics_before = len(before_phase.topics) if before_phase else 0

        _weak_kp(assessment_repo, "phase_safe")
        extra_service.generate_extra_tasks(today="2026-09-06")

        after_phase = sps.get_current_phase("2026-09-06")
        assert after_phase.name == before_phase.name
        assert len(after_phase.topics) == phase_topics_before

    def test_unfinished_extra_archived_next_day_plan_untouched(
        self, repo, conn, sps, assessment_repo
    ):
        """跨天后，未完成 extra 与普通任务一样被归档，不影响第二天正式规划。"""
        _weak_kp(assessment_repo, "nextday_kp")
        extra = ExtraTaskService(
            repo, study_plan_service=sps, assessment_repo=assessment_repo,
        )
        extra_task = extra.generate_extra_tasks(
            today="2026-09-06", difficulty="practice"
        )["created"][0]
        assert extra_task.status == STATUS_ACTIVE

        # 09-06 先自动规划正式任务，再进入 09-07
        ds = DateService(repo, study_plan_service=sps)
        ds.process_date_transition("2026-09-06")
        ds.process_date_transition("2026-09-07")

        # 未完成 extra 被跨天归档（不残留 active）
        got = repo.get(extra_task.id)
        assert got.status == STATUS_NOT_DONE
        # 09-07 正式任务仍正常生成（未被 extra 干扰）
        assert len(repo.list_by_date("2026-09-07")) >= 1


class TestNoCandidates:
    def test_empty_service_no_dirty_data(self, repo):
        svc = ExtraTaskService(repo, study_plan_service=None,
                               assessment_repo=None)
        result = svc.generate_extra_tasks(today="2026-09-06")
        assert result["created"] == []
        assert repo.list_by_date("2026-09-06") == []

    def test_no_weak_kp_but_phase_topics(self, repo, sps):
        svc = ExtraTaskService(
            repo, study_plan_service=sps, assessment_repo=None,
        )
        result = svc.generate_extra_tasks(today="2026-09-06")
        # 没有知识库时至少能基于当前阶段主题生成
        assert len(result["created"]) >= 1
        assert result["created"][0].task_type == "extra"

    def test_unknown_difficulty_defaults_to_practice(self, repo, extra_service,
                                                     assessment_repo):
        _weak_kp(assessment_repo)
        result = extra_service.generate_extra_tasks(
            today="2026-09-06", difficulty="not_a_difficulty"
        )
        assert result["difficulty"] == "practice"
        assert result["created"][0].difficulty == "practice"


class TestEnterAssessment:
    def test_extra_task_can_enter_assessment(self, repo, conn, assessment_repo):
        kp = _weak_kp(assessment_repo, "extra_assess")
        extra = ExtraTaskService(
            repo, study_plan_service=None, assessment_repo=assessment_repo,
        )
        task = extra.generate_extra_tasks(
            today="2026-09-06"
        )["created"][0]

        svc = AssessmentService(
            FakeClient(content=QUESTIONS_CONTENT),
            assessment_repo=assessment_repo,
        )
        attempt = svc.start_assessment(
            kp["id"], task_id=task.id, num_questions=1
        )
        # 验收记录与 extra 任务关联；正式阶段不被推进
        assert attempt["task_id"] == task.id
        assert attempt["knowledge_point_id"] == kp["id"]
