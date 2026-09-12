"""Planner 任务内容质量修复：生成“可执行学习事项”的测试。

覆盖用户要求：
1 AI 生成任务包含具体学习事项
2 AI 生成任务包含完成标准
3 fallback 任务同样包含具体学习事项
4 旧任务字段兼容
5 Planner 任务数量 / daily_limit 不变
6 新内容不破坏现有 Planner / Review / Extra / Assessment（冒烟 + 全量回归）
"""

from __future__ import annotations

import pytest

from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.study_plan_service import StudyPlanService
from app.services.task_content import (
    build_topic_task_content,
    has_actionable_content,
)

PHASE_START = "2026-09-01"
TODAY = "2026-09-12"

GOOD_DESC = (
    "【学习目标】\n跑通 Tool Calling 完整闭环\n\n"
    "【具体学习事项】\n1. 理解 请求→tool call→执行→result→回答 数据流\n"
    "2. 说清 tools/schema/arguments 的作用\n3. 实现一个最小 calculator tool\n"
    "4. 调用一次模型完成 tool calling\n5. 解释与直接执行代码的区别\n\n"
    "【实践】\n写最小 demo 并跑通\n\n"
    "【完成标准】\n- 能画数据流\n- 能跑最小 demo\n- 能解释字段\n- 能改 tool\n\n"
    "【客观验收】\n- 运行 demo\n- 回答 2 道题\n"
)


def _mini(conn, max_minutes=150):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="m", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="mini",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    t1 = plan_repo.create_topic(phase_id=phase.id,
                                name="Function Calling / Tool Calling",
                                estimated_minutes=90, priority=3)
    t2 = plan_repo.create_topic(phase_id=phase.id,
                                name="ReAct 推理与行动",
                                estimated_minutes=60, priority=3)
    sps = StudyPlanService(TaskRepository(conn), plan_repo,
                           max_daily_minutes=max_minutes)
    return {"plan_repo": plan_repo, "phase": phase, "sps": sps,
            "t1": t1, "t2": t2}


class CountingPlanner:
    def __init__(self, description="", topic_id=None):
        self.description = description
        self.topic_id = topic_id
        self.inputs = []

    def is_configured(self):
        return True

    def plan_next_day(self, context):
        self.inputs.append(context)
        return DailyPlan(
            reasoning="x",
            recommended_tasks=(
                RecommendedTask(
                    topic_id=self.topic_id or 1, title="t",
                    description=self.description, estimated_minutes=30,
                ),
            ),
            carry_over_tasks=(),
            daily_minutes=30,
            adjustment="a",
        )


class TestBuilder:
    def test_generic_builds_actionable_content(self):
        for name in ("Function Calling / Tool Calling", "ReAct 推理与行动",
                     "RAG 全流程搭建", "一个冷门主题"):
            content = build_topic_task_content(name)
            assert has_actionable_content(content) is True
            assert "【学习目标】" in content
            assert "【具体学习事项】" in content
            assert "【完成标准】" in content
            # 3~5 条编号事项
            n = sum(1 for line in content.splitlines()
                    if line[:1].isdigit() and "." in line[:3])
            assert 3 <= n <= 5

    def test_vague_description_rejected(self):
        assert has_actionable_content("学习 Function Calling 的用法") is False
        assert has_actionable_content(
            "熟悉函数调用，理解 tools 与参数。" ) is False


class TestFallbackRule:
    def test_fallback_task_has_actionable_items(self, conn):
        env = _mini(conn)
        r = env["sps"].generate_daily_tasks(TODAY)
        assert len(r["generated"]) >= 1
        for task in r["generated"]:
            assert "完成标准" in task.description
            assert "具体学习事项" in task.description
            assert has_actionable_content(task.description) is True

    def test_count_and_daily_limit_unchanged(self, conn):
        from app.database.repository import TaskRepository

        env = _mini(conn, max_minutes=150)
        r = env["sps"].generate_daily_tasks(TODAY)
        # 90+60=150 正好装下两个主题；数量与总时长符合预算
        assert len(r["generated"]) == 2
        assert sum(t.estimated_minutes for t in r["generated"]) <= 150
        # 再生成一次：当天已有任务被去重，不再新增/不膨胀数量
        r2 = env["sps"].generate_daily_tasks(TODAY)
        assert r2["generated"] == []
        assert len(TaskRepository(conn).list_by_date(TODAY)) == 2


class TestAiPath:
    def test_vague_ai_description_is_upgraded(self, conn):
        env = _mini(conn)
        repo = TaskRepository(conn)
        dp = DailyPlannerService(
            repo, env["plan_repo"],
            planner=CountingPlanner(description="", topic_id=env["t1"].id),
            study_plan_service=env["sps"],
        )
        res = dp.generate_next_day_plan("2026-09-11")  # 计划 09-12
        assert res.get("fallback") is False  # 不整单回退，而是升级内容
        tasks = repo.list_by_date(TODAY)
        assert tasks
        assert has_actionable_content(tasks[0].description) is True
        assert "完成标准" in tasks[0].description

    def test_good_ai_description_kept(self, conn):
        env = _mini(conn)
        repo = TaskRepository(conn)
        dp = DailyPlannerService(
            repo, env["plan_repo"],
            planner=CountingPlanner(description=GOOD_DESC,
                                    topic_id=env["t1"].id),
            study_plan_service=env["sps"],
        )
        res = dp.generate_next_day_plan("2026-09-11")
        assert res.get("fallback") is False
        tasks = repo.list_by_date(TODAY)
        assert tasks[0].description == GOOD_DESC


class TestCompatibility:
    def test_old_task_fields_untouched(self, conn):
        repo = TaskRepository(conn)
        t = repo.create(title="历史任务", scheduled_date="2026-09-01",
                        description="旧的一行描述")
        got = repo.get(t.id)
        assert got.title == "历史任务"
        assert got.description == "旧的一行描述"  # 未被改写

    def test_review_and_extra_not_broken(self, conn):
        env = _mini(conn)
        repo = TaskRepository(conn)
        arepo = AssessmentRepository(conn)
        # 复习任务 + extra 任务不受影响
        kp = arepo.create_knowledge_point("kp1")
        repo.create(title="复习 kp1", scheduled_date=TODAY, source="review",
                    task_type="review", knowledge_point_id=kp["id"])
        repo.create(title="【额外】实践", scheduled_date=TODAY, source="extra",
                    task_type="extra", difficulty="practice")
        env["sps"].generate_daily_tasks(TODAY)
        tasks = repo.list_by_date(TODAY)
        assert any(t.task_type == "review" for t in tasks)
        assert any(t.task_type == "extra" for t in tasks)
        # 正式生成任务均具备可执行内容
        formal = [t for t in tasks if t.task_type == "new"]
        assert formal and all(has_actionable_content(t.description)
                              for t in formal)
