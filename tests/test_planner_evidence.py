"""Phase 8：验收结果 / 掌握度 / 复习结果反馈给 Planner 的测试。

覆盖：
1. 无验收证据时行为与旧版兼容（evidence 为空、生成正常）
2. 有 mastery / weak_points 时 PlanningContext 正确包含 evidence
3. weak point 影响正式任务候选优先级
4. mastery 高不重复安排
5. mastery 低针对性巩固
6. review task 不会被 Planner 重复生成
8. 一次高分不会跳过多个 phase
9. 一次低分不会永久锁死
10. AI 非法结果 -> fallback 正常
11. AI 不可用 -> fallback 正常
12. 历史无 assessment_attempts 的老库仍能正常规划
13. long-term context 行为不回归
14. 日期切换 / 自动规划不回归（含 evidence 存在时不重复）

原则：mastery 是动态证据，不是路线控制器；绝不伪造 mastery。
"""

from __future__ import annotations

import json

import pytest

from app.ai.interface import AIServiceError
from app.ai.planner import AIPlanner
from app.ai.planner_context import KnowledgeEvidence, PlanningContext
from app.ai.prompts import build_planner_user_prompt
from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.study_plan_service import StudyPlanService

QUESTIONS_JSON = json.dumps(
    [{"question": "q", "type": "concept", "expected_points": 1}],
    ensure_ascii=False,
)


class CountingPlanner:
    def __init__(self, topic_id=1, error=None, configured=True):
        self.topic_id = topic_id
        self.error = error
        self.configured = configured
        self.inputs = []

    def is_configured(self):
        return self.configured

    def plan_next_day(self, context):
        self.inputs.append(context)
        if self.error is not None:
            raise self.error
        return DailyPlan(
            reasoning="x",
            recommended_tasks=(
                RecommendedTask(
                    topic_id=self.topic_id, title="t", estimated_minutes=30,
                ),
            ),
            carry_over_tasks=(),
            daily_minutes=30,
            adjustment="a",
        )


@pytest.fixture()
def assessment_repo(conn):
    return AssessmentRepository(conn)


@pytest.fixture()
def plan_repo(conn):
    return StudyPlanRepository(conn)


def _sps(conn, assessment_repo, ensure=True):
    """基于 conn 构造 StudyPlanService（可注入评估库）。"""
    svc = StudyPlanService(
        TaskRepository(conn), StudyPlanRepository(conn),
        assessment_repo=assessment_repo,
    )
    if ensure:
        svc.ensure_default_plan()
    return svc


def _mini(conn, assessment_repo, max_minutes=60):
    """构造只有一个阶段、两个同级主题的最小计划服务（避免依赖默认种子）。"""
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(
        name="mini", start_date="2026-09-01", end_date="2026-12-31",
        description="mini",
    )
    phase = plan_repo.create_phase(
        plan_id=plan.id, name="mini-phase", start_date="2026-09-01",
        end_date="2026-12-31", goals="g",
    )
    ta = plan_repo.create_topic(phase_id=phase.id, name="TopicA",
                                estimated_minutes=30, priority=3, order_index=0)
    tb = plan_repo.create_topic(phase_id=phase.id, name="TopicB",
                                estimated_minutes=30, priority=3, order_index=1)
    svc = StudyPlanService(
        TaskRepository(conn), plan_repo,
        max_daily_minutes=max_minutes, assessment_repo=assessment_repo,
    )
    return svc, ta, tb


def _seed(assessment_repo, name, topic_id, mastery=0.4,
          weak=(), result_level="good"):
    """写入一个有真实验收证据的知识点（并关联到主题）。"""
    kp = assessment_repo.create_knowledge_point(name, topic_id=topic_id)
    assessment_repo.update_knowledge_point(
        kp["id"], last_assessed_at="2026-09-06T10:00:00",
        mastery_estimate=mastery, review_count=1,
    )
    att = assessment_repo.create_attempt(kp["id"], QUESTIONS_JSON)
    assessment_repo.update_attempt(
        att["id"], judge_status="judged", result_level=result_level,
        weak_points_json=json.dumps(list(weak), ensure_ascii=False),
        mastery_estimate=mastery,
    )
    return kp


class TestNoEvidenceCompatible:
    def test_generation_same_as_legacy_without_evidence(self, conn,
                                                        assessment_repo):
        svc, ta, tb = _mini(conn, assessment_repo)
        res = svc.generate_daily_tasks("2026-09-08")
        assert len(res["generated"]) == 2
        # 旧版选择顺序：同优先级按 order_index -> 先 TopicA
        assert res["generated"][0].topic_id == ta.id

    def test_build_context_evidence_empty(self, conn, assessment_repo, plan_repo):
        sps = _sps(conn, assessment_repo)
        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=CountingPlanner(),
            study_plan_service=sps, assessment_repo=assessment_repo,
        )
        ctx = dp.build_context("2026-09-05")
        assert ctx.knowledge_evidence == []


class TestContextEvidence:
    def test_context_includes_real_evidence_only(self, conn, assessment_repo,
                                                 plan_repo):
        sps = _sps(conn, assessment_repo)
        phase = sps.get_current_phase("2026-09-08")
        topic = phase.topics[0]
        kp = _seed(assessment_repo, "pytorch.autograd", topic.id,
                   mastery=0.496, weak=("zero_grad",), result_level="good")
        # 一个没有验收证据的知识点不应进入 evidence
        assessment_repo.create_knowledge_point("never_assessed")

        dp = DailyPlannerService(
            TaskRepository(conn), plan_repo, planner=CountingPlanner(),
            study_plan_service=sps, assessment_repo=assessment_repo,
        )
        ctx = dp.build_context("2026-09-07")  # -> 规划 09-08
        assert len(ctx.knowledge_evidence) == 1
        ev = ctx.knowledge_evidence[0]
        assert ev.knowledge_point_id == kp["id"]
        assert ev.name == "pytorch.autograd"
        assert ev.topic_id == topic.id
        assert ev.topic == topic.name
        assert ev.mastery_estimate == pytest.approx(0.496)
        assert ev.weak_points == ("zero_grad",)
        assert ev.recent_result_level == "good"
        assert ev.review_count == 1


class TestWeakPriority:
    def test_weak_topic_rises_in_candidates(self, conn, assessment_repo):
        svc, ta, tb = _mini(conn, assessment_repo, max_minutes=30)
        # TopicB 薄弱（weak_points），TopicA 无证据
        _seed(assessment_repo, "kp_b", tb.id, mastery=0.3,
              weak=("zero_grad",), result_level="poor")
        res = svc.generate_daily_tasks("2026-09-08")
        # 预算只够 1 个；薄弱主题在候选里应优先 -> 选中 TopicB
        assert res["generated"][0].topic_id == tb.id


class TestHighMasteryNoRepeat:
    def test_high_mastery_topic_not_rescheduled(self, conn, assessment_repo):
        svc, ta, tb = _mini(conn, assessment_repo, max_minutes=60)
        _seed(assessment_repo, "kp_a", ta.id, mastery=0.9,
              result_level="excellent")
        res = svc.generate_daily_tasks("2026-09-08")
        generated_ids = {t.topic_id for t in res["generated"]}
        assert ta.id not in generated_ids
        assert ta.id in res["skipped_done"]
        assert tb.id in generated_ids


class TestLowMasteryKeep:
    def test_low_mastery_topic_kept_and_boosted(self, conn, assessment_repo):
        svc, ta, tb = _mini(conn, assessment_repo, max_minutes=30)
        _seed(assessment_repo, "kp_b", tb.id, mastery=0.2, result_level="poor")
        res = svc.generate_daily_tasks("2026-09-08")
        assert res["generated"][0].topic_id == tb.id
        # 次日（未完成）仍可选、不因一次 poor 永久锁死
        res2 = svc.generate_daily_tasks("2026-09-09")
        assert tb.id in {t.topic_id for t in res2["generated"]}


class TestReviewNotDuplicated:
    def test_in_progress_review_blocks_formal_task(self, conn, assessment_repo):
        svc, ta, tb = _mini(conn, assessment_repo, max_minutes=60)
        kp = _seed(assessment_repo, "kp_b", tb.id, mastery=0.5)
        # 该知识点的未完成复习任务已存在
        TaskRepository(conn).create(
            title="复习 kp_b", scheduled_date="2026-09-08", source="review",
            task_type="review", knowledge_point_id=kp["id"],
        )
        res = svc.generate_daily_tasks("2026-09-08")
        generated_ids = {t.topic_id for t in res["generated"]}
        assert tb.id not in generated_ids  # 复习进行中 -> 不生成正式新任务
        assert ta.id in generated_ids

