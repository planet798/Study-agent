"""日期注入机制测试：模拟从 2026-09-04 进入 2026-09-05 的完整链路。

覆盖：
- DateService 正确处理日期切换（含延期任务带入今天）
- postpone_count 保持正确、不产生重复任务
- AI Planner 基于 2026-09-05 生成任务，且不重复生成延期/已完成任务
- 同一天重复处理幂等
- today provider 的默认/覆盖/恢复行为
"""

from __future__ import annotations

import pytest

from app.ai.schemas import CarryOverTask, DailyPlan, RecommendedTask
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.date_service import DateService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.utils import date_utils
from tests.utils import D9_04, D9_05, make_today_provider, postpone_task_to_tomorrow

# 默认研一计划 Phase1 的主题名（用于定位 Linux 等主题）
PHASE1_TOPICS = {
    "python_basic": "Python 语法与基础练习",
    "python_oop": "Python 面向对象与常用库",
    "linux": "Linux 常用命令与工具链",
    "git": "Git 版本控制与协作流程",
}


class FakePlanner:
    """可配置假 AIPlanner：返回固定 DailyPlan 或抛错。"""

    def __init__(self, plan=None, error=None, configured=True):
        self._plan = plan
        self._error = error
        self.configured = configured
        self.ctx_inputs = []

    def is_configured(self):
        return self.configured

    def plan_next_day(self, context):
        self.ctx_inputs.append(context)
        if self._error is not None:
            raise self._error
        if self._plan is None:
            self._plan = default_plan_for(context)
        return self._plan


@pytest.fixture()
def plan_repo(repo):
    return StudyPlanRepository(repo.conn)


@pytest.fixture()
def sps(repo, plan_repo):
    svc = StudyPlanService(repo, plan_repo)
    svc.ensure_default_plan()
    return svc


def _topics_by_name(sps) -> dict[str, int]:
    plan = sps.get_active_plan_full()
    return {t.name: t.id for ph in plan.phases for t in ph.topics}


def default_plan_for(context):
    """生成一个把 Phase1 主题作为推荐的 DailyPlan。"""
    return DailyPlan(
        reasoning="测试规划",
        recommended_tasks=(
            RecommendedTask(
                topic_id=1, title="Python 语法与基础练习",
                estimated_minutes=45, priority=3,
            ),
        ),
        carry_over_tasks=(),
        daily_minutes=60,
        adjustment="无",
    )


# ================= today provider 行为 =================

class TestTodayProvider:
    def test_default_is_system_date(self):
        import datetime

        assert date_utils.today() == datetime.date.today().strftime("%Y-%m-%d")

    def test_set_and_reset(self):
        original = date_utils.today()
        date_utils.set_today_provider(D9_05)
        assert date_utils.today() == D9_05
        date_utils.reset_today_provider()
        assert date_utils.today() == original

    def test_override_context_restores(self):
        original = date_utils.today()
        with date_utils.override_today(D9_05):
            assert date_utils.today() == D9_05
        assert date_utils.today() == original

    def test_provider_flows_into_repository_create_default(self, repo):
        """repository.create 缺省日期走 provider（验证统一入口）。"""
        original = date_utils.today()
        try:
            date_utils.set_today_provider(D9_05)
            t = TaskService(repo).create_task("任务")
            assert t.scheduled_date == D9_05
        finally:
            date_utils.reset_today_provider()
            assert repo.get(t.id).scheduled_date == D9_05


# ================= 从 09-04 进入 09-05 的完整链路 =================

class TestTransitionToNextDay:
    def _make_date_service(self, repo, sps):
        """构造 DateService（含 rules 级生成）并首次进入 09-04。"""
        ds = DateService(repo, study_plan_service=sps)
        ds.process_date_transition(D9_04)
        return ds

    def test_09_04_processed_and_postponed_task_exists(
        self, repo, plan_repo, sps, task_service
    ):
        ds = self._make_date_service(repo, sps)
        # 09-04 建任务并延期到 09-05 (模拟昨日晚延期)
        t = task_service.create_task("待延期", scheduled_date=D9_04,
                                     topic_id=1, source="generated")
        postpone_task_to_tomorrow(task_service, t.id)

        assert task_service.get_task(t.id).scheduled_date == D9_05
        assert task_service.get_task(t.id).postpone_count == 1
        assert ds.get_last_processed_date() == D9_04

    def test_using_09_05_as_today(self, task_service):
        """用 09-05 作为 today 构造窗口/服务：日期标签生效。"""
        provider = make_today_provider(D9_05)
        assert provider() == D9_05

    def test_date_service_handles_transition_to_09_05(
        self, repo, plan_repo, sps
    ):
        ds = self._make_date_service(repo, sps)
        res = ds.process_date_transition(D9_05)
        assert res["processed"] is True
        assert ds.get_last_processed_date() == D9_05

    def test_09_04_postponed_task_appears_on_09_05(
        self, repo, plan_repo, sps, task_service
    ):
        ds = self._make_date_service(repo, sps)
        t = task_service.create_task("从昨日延期来", scheduled_date=D9_04,
                                     topic_id=1, source="generated")
        postpone_task_to_tomorrow(task_service, t.id)

        ds.process_date_transition(D9_05)
        today_tasks = task_service.get_active_tasks_by_date(D9_05)
        assert any(x.id == t.id for x in today_tasks)

    def test_postpone_count_preserved_across_transition(
        self, repo, plan_repo, sps, task_service
    ):
        ds = self._make_date_service(repo, sps)
        t = task_service.create_task("多次延期", scheduled_date=D9_04,
                                     topic_id=1, source="generated")
        postpone_task_to_tomorrow(task_service, t.id)  # count=1
        ds.process_date_transition(D9_05)
        got = task_service.get_task(t.id)
        assert got.postpone_count == 1
        assert got.scheduled_date == D9_05

    def test_no_duplicate_tasks_on_09_05(
        self, repo, plan_repo, sps, task_service
    ):
        ds = self._make_date_service(repo, sps)
        t = task_service.create_task("不重复", scheduled_date=D9_04,
                                     topic_id=1, source="generated")
        postpone_task_to_tomorrow(task_service, t.id)

        # 同一 09-05 处理两次，幂等：任务数量与内容不变
        ds.process_date_transition(D9_05)
        n1 = len(task_service.get_tasks_by_date(D9_05))
        ids1 = {x.id for x in task_service.get_tasks_by_date(D9_05)}
        ds.process_date_transition(D9_05)  # already_processed，无副作用
        n2 = len(task_service.get_tasks_by_date(D9_05))
        ids2 = {x.id for x in task_service.get_tasks_by_date(D9_05)}
        assert n1 == n2
        assert ids1 == ids2
        # 延期任务只有一条，未被重复生成
        same = [x for x in task_service.get_tasks_by_date(D9_05) if x.id == t.id]
        assert len(same) == 1


class TestAIPlannerOnNextDay:
    def _make_planner_service(self, repo, plan_repo, sps, fake):
        return DailyPlannerService(
            repo, plan_repo, planner=fake, study_plan_service=sps,
        )

    def test_ai_planner_uses_09_05_as_target(
        self, repo, plan_repo, sps, task_service
    ):
        fake = FakePlanner()
        dp = self._make_planner_service(repo, plan_repo, sps, fake)
        # 从 09-04 规划 => 目标是 09-05
        result = dp.generate_next_day_plan(D9_04)
        assert result["date"] == D9_05
        # AI 上下文以 09-05 为 current_date
        assert fake.ctx_inputs and fake.ctx_inputs[-1].current_date == D9_05

    def test_ai_does_not_regenerate_linux_postponed_task(
        self, repo, plan_repo, sps, task_service
    ):
        """Linux 延期任务在 09-05 已存在，AI 不再生成一份。"""
        topics = _topics_by_name(sps)
        linux_id = topics[PHASE1_TOPICS["linux"]]

        # 09-04 延 Linux 任务到 09-05
        t = task_service.create_task("Linux 命令", scheduled_date=D9_04,
                                     topic_id=linux_id, source="generated")
        postpone_task_to_tomorrow(task_service, t.id)

        # AI 推荐同一个 Linux 主题
        fake = FakePlanner(
            plan=DailyPlan(
                reasoning="r",
                recommended_tasks=(
                    RecommendedTask(
                        topic_id=linux_id, title="Linux 常用命令与工具链",
                        estimated_minutes=30, priority=2,
                    ),
                ),
                carry_over_tasks=(),
                daily_minutes=60,
                adjustment="a",
            )
        )
        dp = self._make_planner_service(repo, plan_repo, sps, fake)
        result = dp.generate_next_day_plan(D9_04)

        # 09-05 上 Linux 主题任务只有 1 条（已延期的），不产生第二份
        tasks = [x for x in task_service.get_tasks_by_date(D9_05)
                 if x.topic_id == linux_id]
        assert len(tasks) == 1
        assert tasks[0].id == t.id

    def test_completed_task_not_regenerated(
        self, repo, plan_repo, sps, task_service
    ):
        topics = _topics_by_name(sps)
        git_id = topics[PHASE1_TOPICS["git"]]

        # 09-04 完成 Git 主题
        t = task_service.create_task("Git", scheduled_date=D9_04,
                                     topic_id=git_id, source="generated")
        task_service.complete_task(t.id)

        # AI 推荐已完成 Git 主题 => 本地校验拒绝 -> fallback 规则生成（不含 Git 新任务）
        fake = FakePlanner(
            plan=DailyPlan(
                reasoning="r",
                recommended_tasks=(
                    RecommendedTask(
                        topic_id=git_id, title="Git 版本控制与协作流程",
                        estimated_minutes=30, priority=1,
                    ),
                ),
                carry_over_tasks=(),
                daily_minutes=60,
                adjustment="a",
            )
        )
        dp = self._make_planner_service(repo, plan_repo, sps, fake)
        result = dp.generate_next_day_plan(D9_04)

        # 未在 09-05 生成已完成主题的新任务（fallback 规则同样会跳过已完成的）
        new_tasks = [x for x in task_service.get_tasks_by_date(D9_05)
                     if x.topic_id == git_id and x.id != t.id]
        assert new_tasks == []

    def test_same_day_repeat_is_idempotent(
        self, repo, plan_repo, sps, task_service
    ):
        fake = FakePlanner()
        dp = self._make_planner_service(repo, plan_repo, sps, fake)
        r1 = dp.generate_next_day_plan(D9_04)
        n1 = len(task_service.get_tasks_by_date(D9_05))
        r2 = dp.generate_next_day_plan(D9_04)
        n2 = len(task_service.get_tasks_by_date(D9_05))
        assert r2["existing"] is True
        assert n1 == n2
