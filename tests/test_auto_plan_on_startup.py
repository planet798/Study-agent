"""启动时自动规划链路测试。

覆盖（对应产品要求）：
1. 新日期第一次启动 -> 今天无任务 -> 自动调用 Planner 并生成任务；
2. 同一天再次启动 -> 今天已有任务 -> 不再调用 Planner（幂等）；
3. 用户主动点击“重新规划今天” -> 仍然能触发 Planner；
4. 昨天有延期任务 -> 延期逻辑仍正常；
5. 自动 Planner -> career_context.json 被读取并进入 prompt；
6. Planner 失败 -> 应用不崩溃，走规则 fallback；
7. 今天已有任务 -> 自动启动不覆盖已有任务。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QMessageBox

from app.ai.interface import AIServiceError
from app.ai.long_term_context import load_long_term_context
from app.ai.planner import AIPlanner
from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.main_window import MainWindow


@pytest.fixture()
def plan_repo(repo):
    return StudyPlanRepository(repo.conn)


@pytest.fixture()
def sps(repo, plan_repo):
    svc = StudyPlanService(repo, plan_repo)
    svc.ensure_default_plan()
    return svc


def _topic_for_date(sps, date_str) -> int:
    """返回 date_str 当天应学阶段的首个主题 id。"""
    phase = sps.get_current_phase(date_str)
    assert phase is not None
    return phase.topics[0].id


class CountingPlanner:
    """返回固定主题的假 planner，并记录调用次数。"""

    def __init__(self, topic_id, error=None, configured=True):
        self.topic_id = topic_id
        self.error = error
        self.configured = configured
        self.calls = 0
        self.contexts = []

    def is_configured(self):
        return self.configured

    def plan_next_day(self, context):
        self.calls += 1
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return DailyPlan(
            reasoning="自动规划",
            recommended_tasks=(
                RecommendedTask(
                    topic_id=self.topic_id,
                    title="自动规划任务",
                    estimated_minutes=45,
                    priority=3,
                ),
            ),
            carry_over_tasks=(),
            daily_minutes=45,
            adjustment="无",
        )


def _make_date_service(repo, plan_repo, sps, planner):
    dp = DailyPlannerService(
        repo, plan_repo, planner=planner, study_plan_service=sps,
    )
    return DateService(
        repo,
        task_service=TaskService(repo),
        study_plan_service=sps,
        daily_planner_service=dp,
    )


class TestAutoPlanFirstAndIdempotent:
    def test_new_date_first_start_auto_plans_when_empty(
        self, repo, plan_repo, sps
    ):
        planner = CountingPlanner(_topic_for_date(sps, "2026-09-06"))
        ds = _make_date_service(repo, plan_repo, sps, planner)

        res = ds.process_date_transition("2026-09-06")

        assert res["processed"] is True
        assert res["reason"] == "first_run"
        assert planner.calls == 1  # 自动调用了 Planner
        assert len(repo.list_by_date("2026-09-06")) == 1

    def test_same_day_second_start_does_not_replan(
        self, repo, plan_repo, sps
    ):
        planner = CountingPlanner(_topic_for_date(sps, "2026-09-06"))
        ds = _make_date_service(repo, plan_repo, sps, planner)

        ds.process_date_transition("2026-09-06")
        n1 = len(repo.list_by_date("2026-09-06"))
        calls1 = planner.calls

        res2 = ds.process_date_transition("2026-09-06")

        assert res2["reason"] == "already_processed"
        assert planner.calls == calls1  # 没有再次调用 Planner
        assert len(repo.list_by_date("2026-09-06")) == n1

    def test_existing_tasks_preserved_across_restart(
        self, repo, plan_repo, sps, task_service
    ):
        planner = CountingPlanner(_topic_for_date(sps, "2026-09-06"))
        ds = _make_date_service(repo, plan_repo, sps, planner)

        ds.process_date_transition("2026-09-06")
        before = [(t.id, t.title, t.status) for t in repo.list_by_date("2026-09-06")]
        assert len(before) >= 1

        # 再次启动：已有任务不被覆盖/重复，也不再调用 Planner
        ds.process_date_transition("2026-09-06")
        after = [(t.id, t.title, t.status) for t in repo.list_by_date("2026-09-06")]
        assert after == before
        assert planner.calls == 1


class TestDeferredAndFallback:
    def test_postponed_task_enters_next_day_with_planner(
        self, repo, plan_repo, sps, task_service
    ):
        planner = CountingPlanner(_topic_for_date(sps, "2026-09-06"))
        ds = _make_date_service(repo, plan_repo, sps, planner)
        ds.process_date_transition("2026-09-05")

        t = task_service.create_task(
            "昨天未完成", scheduled_date="2026-09-05",
            topic_id=_topic_for_date(sps, "2026-09-05"), source="generated",
        )
        task_service.mark_not_done(t.id, "没做完")
        task_service.postpone_task(t.id)  # -> 2026-09-06

        ds.process_date_transition("2026-09-06")

        today = task_service.get_active_tasks_by_date("2026-09-06")
        assert any(x.id == t.id for x in today)
        assert task_service.get_task(t.id).postpone_count == 1

    def test_planner_failure_falls_back_without_crash(
        self, repo, plan_repo, sps
    ):
        planner = CountingPlanner(
            _topic_for_date(sps, "2026-09-06"), error=AIServiceError("AI 超时")
        )
        ds = _make_date_service(repo, plan_repo, sps, planner)

        res = ds.process_date_transition("2026-09-06")

        # fallback 规则仍生成了任务，应用不崩溃
        assert res["processed"] is True
        assert len(repo.list_by_date("2026-09-06")) >= 1
        decision = PlanDecisionForTest(repo, "2026-09-06")
        assert decision is not None and decision["source"] == "fallback_rule"


def PlanDecisionForTest(repo, date_str):
    row = repo.conn.execute(
        "SELECT * FROM planner_decisions WHERE date = ? ORDER BY id DESC LIMIT 1",
        (date_str,),
    ).fetchone()
    return dict(row) if row else None


class _CapturingClient:
    """记录 prompt，并返回合法 DailyPlan JSON 的假 client。"""

    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        return json.dumps(
            {
                "reasoning": "按长期路线安排",
                "recommended_tasks": [
                    {"topic_id": 1, "title": "t", "estimated_minutes": 30}
                ],
                "carry_over_tasks": [],
                "daily_minutes": 30,
                "adjustment": "a",
            },
            ensure_ascii=False,
        )


class TestCareerContextInAutoPlan:
    def test_auto_plan_prompt_contains_career_context(
        self, repo, plan_repo, sps
    ):
        client = _CapturingClient()
        ctx = load_long_term_context()
        assert ctx is not None
        planner = AIPlanner(client, long_term_context=ctx)
        ds = _make_date_service(repo, plan_repo, sps, planner)

        ds.process_date_transition("2026-09-06")

        assert client.calls
        _, user_prompt = client.calls[0]
        assert "【长期学习上下文】" in user_prompt
        assert "9/10" in user_prompt  # JD 高频技能
        assert "阶段二" in user_prompt  # 技能路线触发 PyTorch 阶段


class TestReplanTriggersPlanner:
    def test_replan_button_triggers_planner(
        self, qtbot, repo, plan_repo, sps, task_service, monkeypatch
    ):
        planner = CountingPlanner(_topic_for_date(sps, "2026-09-06"))
        dp = DailyPlannerService(
            repo, plan_repo, planner=planner, study_plan_service=sps,
        )
        ds = DateService(
            repo,
            task_service=task_service,
            study_plan_service=sps,
            daily_planner_service=dp,
        )
        w = MainWindow(
            task_service=task_service,
            date_service=ds,
            today_provider=lambda: "2026-09-06",
            study_plan_service=sps,
            daily_planner_service=dp,
        )
        qtbot.addWidget(w)

        # 启动时已自动规划一次
        assert planner.calls >= 1
        before = planner.calls

        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        w._on_replan()

        assert planner.calls == before + 1  # 主动点击仍然触发 Planner
        assert len(task_service.get_tasks_by_date("2026-09-06")) >= 1
        w.close()


class TestCompletionAwareAdvance:
    def test_phase_advances_when_current_phase_complete(
        self, repo, plan_repo, sps, task_service
    ):
        """阶段一全部完成后，自动推进到阶段二（而不是每天生成 0 个任务）。"""
        phase1 = sps.get_active_plan_full().phases[0]
        for topic in phase1.topics:
            t = task_service.create_task(
                topic.name, scheduled_date="2026-09-04", topic_id=topic.id,
            )
            task_service.complete_task(t.id)

        phase = sps.get_current_phase("2026-09-06")
        assert phase is not None
        assert phase.name == "阶段二：深度学习与 LLM 基础"

    def test_auto_generates_phase2_task_after_phase1_done(
        self, repo, plan_repo, sps, task_service
    ):
        """完成阶段一后，新一天自动规划会生成阶段二（PyTorch）任务。"""
        phase1 = sps.get_active_plan_full().phases[0]
        for topic in phase1.topics:
            t = task_service.create_task(
                topic.name, scheduled_date="2026-09-04", topic_id=topic.id,
            )
            task_service.complete_task(t.id)

        ds = DateService(
            repo, task_service=task_service, study_plan_service=sps,
        )
        # 先进入 09-05（阶段一已完成）
        ds.process_date_transition("2026-09-05")
        ds.process_date_transition("2026-09-06")

        tasks = repo.list_by_date("2026-09-06")
        assert len(tasks) >= 1
        titles = {t.title for t in tasks}
        assert any("PyTorch" in t for t in titles)
