"""StudyPlanService 测试：学习计划 / 当前阶段 / 每日任务生成。"""

from __future__ import annotations

import pytest

from app.database.study_plan_repository import StudyPlanRepository
from app.services.study_plan_service import MAX_DAILY_STUDY_MINUTES, StudyPlanService
from app.services.task_service import TaskService

# 默认计划的各阶段边界（与 service 内种子保持一致）
PHASE1_START = "2026-09-01"
PHASE1_END = "2026-09-05"
PHASE2_START = "2026-09-06"
PLAN_START = "2026-09-01"
PLAN_END = "2027-08-31"


@pytest.fixture()
def plan_repo(repo):
    return StudyPlanRepository(repo.conn)


@pytest.fixture()
def sps(repo, plan_repo):
    """StudyPlanService + 默认计划。"""
    svc = StudyPlanService(repo, plan_repo)
    svc.ensure_default_plan()
    return svc


@pytest.fixture()
def task_service(repo):
    return TaskService(repo)


class TestDefaultPlan:
    def test_create_default_plan(self, sps, plan_repo):
        plan = plan_repo.get_active_plan()
        assert plan is not None
        assert plan.name == "USTC AI 研一大厂算法路线"
        assert plan.start_date == PLAN_START
        assert plan.end_date == PLAN_END

    def test_default_plan_has_career_phases(self, sps):
        plan = sps.get_active_plan_full()
        assert plan is not None
        assert len(plan.phases) == 5
        names = [p.name for p in plan.phases]
        assert "Python + Linux + Git" in names
        assert "阶段二：深度学习与 LLM 基础" in names
        assert "阶段三：LLM 应用" in names
        assert "阶段四：模型训练与部署" in names
        assert "阶段五：后续扩展" in names

    def test_ensure_default_plan_idempotent(self, sps, plan_repo):
        # 再次调用不产生重复
        sps.ensure_default_plan()
        assert len(plan_repo.list_plans(status="active")) == 1

    def test_phases_have_topics(self, sps):
        plan = sps.get_active_plan_full()
        for ph in plan.phases:
            assert len(ph.topics) >= 1


class TestCurrentPhase:
    def test_date_inside_phase1(self, sps):
        phase = sps.get_current_phase("2026-09-04")
        assert phase is not None
        assert phase.name == "Python + Linux + Git"

    def test_phase_start_boundary(self, sps):
        phase = sps.get_current_phase(PHASE1_START)
        assert phase is not None
        assert phase.name == "Python + Linux + Git"

    def test_phase_end_boundary(self, sps):
        phase = sps.get_current_phase(PHASE1_END)
        assert phase is not None
        assert phase.name == "Python + Linux + Git"

    def test_next_phase_start(self, sps):
        phase = sps.get_current_phase(PHASE2_START)
        assert phase is not None
        assert phase.name == "阶段二：深度学习与 LLM 基础"

    def test_date_before_plan_returns_none(self, sps):
        assert sps.get_current_phase("2026-08-31") is None

    def test_date_after_plan_returns_none(self, sps):
        assert sps.get_current_phase("2027-09-01") is None

    def test_phase_priority_order(self, sps):
        # 阶段按起始日期升序：Phase1 最早
        plan = sps.get_active_plan_full()
        starts = [p.start_date for p in plan.phases]
        assert starts == sorted(starts)


class TestTopicSorting:
    def test_topics_sorted_high_priority_first(self, sps, plan_repo):
        plan = sps.get_active_plan_full()
        phase1 = plan.phases[0]
        topics = plan_repo.list_topics(phase1.id)
        priorities = [t.priority for t in topics]
        assert priorities == sorted(priorities, reverse=True)


class TestDailyGeneration:
    def test_generates_tasks_for_phase1_date(self, sps, task_service):
        res = sps.generate_daily_tasks("2026-09-04")
        assert res["phase"] == "Python + Linux + Git"
        assert len(res["generated"]) >= 1
        # 生成的任务进入 tasks 表
        today_tasks = task_service.get_tasks_by_date("2026-09-04")
        assert len(today_tasks) == len(res["generated"])
        # 与 TaskService 兼容（可正常读取状态）
        first = today_tasks[0]
        assert first.status == "active"
        assert first.source == "generated"
        assert first.topic_id is not None

    def test_respects_180_minute_budget(self, sps):
        res = sps.generate_daily_tasks("2026-09-04")
        total = sum(t.estimated_minutes for t in res["generated"])
        assert total <= MAX_DAILY_STUDY_MINUTES

    def test_high_priority_topic_selected_first(self, sps, repo, plan_repo):
        # 构造一个仅能容纳一个主题的小预算，验证优先选高优先级
        svc = StudyPlanService(repo, plan_repo, max_daily_minutes=50)
        svc.ensure_default_plan()
        res = svc.generate_daily_tasks("2026-09-04")
        assert len(res["generated"]) == 1
        # Phase1 最高优先级主题 45 分钟（priority 3）
        picked = res["generated"][0]
        assert picked.estimated_minutes == 45
        assert picked.priority == 3

    def test_stops_when_remaining_too_small(self, sps, repo, plan_repo):
        svc = StudyPlanService(repo, plan_repo, max_daily_minutes=70)
        svc.ensure_default_plan()
        res = svc.generate_daily_tasks("2026-09-04")
        # 45+45=90 > 70，只能放 1 个
        assert len(res["generated"]) == 1

    def test_does_not_regenerate_same_day(self, sps, task_service):
        sps.generate_daily_tasks("2026-09-04")
        n1 = len(task_service.get_tasks_by_date("2026-09-04"))
        sps.generate_daily_tasks("2026-09-04")
        n2 = len(task_service.get_tasks_by_date("2026-09-04"))
        assert n1 == n2  # 幂等：不重复生成

    def test_no_generation_outside_plan(self, sps):
        res = sps.generate_daily_tasks("2028-01-01")
        assert res["phase"] is None
        assert res["generated"] == []

    def test_at_least_one_core_task(self, sps, repo, plan_repo):
        # 极小预算也确保至少一个核心主题
        svc = StudyPlanService(repo, plan_repo, max_daily_minutes=10)
        svc.ensure_default_plan()
        res = svc.generate_daily_tasks("2026-09-04")
        assert len(res["generated"]) == 1


class TestPostponedPriority:
    def test_postponed_task_consumes_budget_and_takes_priority(
        self, sps, task_service
    ):
        """延期任务优先：先占用预算，剩余空间才安排新主题。"""
        res = sps.generate_daily_tasks("2026-09-04")
        # 第一天生成一些任务
        first_day_count = len(res["generated"])

        # 把其中一条延期到明天（模拟用户延期的任务）
        tasks_today = task_service.get_tasks_by_date("2026-09-04")
        postponed = tasks_today[0]
        task_service.mark_not_done(postponed.id, "没时间")
        task_service.postpone_task(postponed.id)  # -> 2026-09-05

        # 第二天生成：延期任务已占用预算，新生成不含同主题重复
        res2 = sps.generate_daily_tasks("2026-09-05")
        tomorrow_tasks = task_service.get_tasks_by_date("2026-09-05")

        # 延期任务在明天待办里
        postponed_ids = {t.topic_id for t in tomorrow_tasks if t.topic_id is not None}
        assert postponed.topic_id in postponed_ids

        # 新生成的总时长不超预算
        total = sum(t.estimated_minutes for t in tomorrow_tasks)
        assert total <= MAX_DAILY_STUDY_MINUTES

    def test_done_topic_not_regenerated(self, sps, task_service):
        res = sps.generate_daily_tasks("2026-09-04")
        done = res["generated"][0]
        task_service.complete_task(done.id)

        # 第二天：已完成主题不再重复生成
        res2 = sps.generate_daily_tasks("2026-09-05")
        tomorrow = task_service.get_tasks_by_date("2026-09-05")
        generated_topic_ids = {t.topic_id for t in tomorrow if t.topic_id is not None}
        assert done.topic_id not in generated_topic_ids


class TestCompatibility:
    def test_generated_tasks_work_with_task_service(self, sps, task_service):
        sps.generate_daily_tasks("2026-09-04")
        tasks = task_service.get_tasks_by_date("2026-09-04")
        t = tasks[0]
        # 完整业务流兼容：完成 / 未完成 / 延期
        task_service.complete_task(t.id)
        assert task_service.get_status(t.id) == "done"
        t2 = tasks[1]
        task_service.mark_not_done(t2.id, "原因")
        task_service.postpone_task(t2.id)
        assert task_service.get_task(t2.id).postpone_count == 1

    def test_stats_include_generated_tasks(self, sps, task_service):
        sps.generate_daily_tasks("2026-09-04")
        stats = task_service.get_daily_stats("2026-09-04")
        assert stats["total"] >= 1
        assert stats["rate"] == 0.0  # 新生成未完成


class TestDateServiceIntegration:
    def test_date_transition_generates_tasks(self, repo, plan_repo, conn):
        """日期切换后自动生成当天任务。"""
        from app.services.date_service import DateService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        ds = DateService(repo, study_plan_service=sps)

        # 首次进入计划期间的日期：生成当天任务
        res = ds.process_date_transition("2026-09-04")
        assert res["reason"] == "first_run"
        assert len(res["generated"]) >= 1

        today_tasks = repo.list_by_date("2026-09-04")
        assert all(t.source == "generated" for t in today_tasks)

        # 同日重复调用：幂等，不重复生成
        res2 = ds.process_date_transition("2026-09-04")
        assert res2["processed"] is False
        assert len(repo.list_by_date("2026-09-04")) == len(today_tasks)

    def test_date_transition_new_day_generates(self, repo, plan_repo):
        from app.services.date_service import DateService

        sps = StudyPlanService(repo, plan_repo)
        sps.ensure_default_plan()
        ds = DateService(repo, study_plan_service=sps)

        ds.process_date_transition("2026-09-04")
        ds.process_date_transition("2026-09-05")
        tasks = repo.list_by_date("2026-09-05")
        assert len(tasks) >= 1
        assert all(t.source == "generated" for t in tasks)

    def test_date_service_works_without_plan(self, repo):
        """未注入 learning plan 时日期切换仍正常（向后兼容）。"""
        from app.services.date_service import DateService

        ds = DateService(repo)
        res = ds.process_date_transition("2026-09-04")
        assert res["processed"] is True
        assert repo.list_by_date("2026-09-04") == []
