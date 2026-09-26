"""Phase A：cancelled（移除今日任务）语义与回归。

覆盖需求 26 的 10~24、27~30：
- Agent / manual 任务可 cancelled；
- cancelled 不物理删除、不算 not_done、不计入完成率分母；
- cancelled 不进入昨日未确认，active manual 进入；
- 补确认完成（普通 / 知识）行为；
- 当天 replan 不重新生成 cancelled topic，次日可重新候选；
- done / not_done 现有规则不回归；
- 完成率 2/3 与 2/2 场景；
- 移除确认框、重启后状态保留、Review / Assessment 不回归。
"""

from __future__ import annotations

from app.ai.schemas import DailyPlan, RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_DONE
from app.database.study_plan_repository import StudyPlanRepository
from app.services.daily_planner_service import DailyPlannerService
from app.services.manual_task_service import ManualTaskService
from app.services.past_task_service import (
    DECISION_DONE,
    PastTaskConfirmationService,
)
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import InvalidTransitionError, TaskService
from app.ui.main_window import MainWindow
from app.ui.task_widget import TaskWidget

TODAY = "2026-09-15"
NEXT_DAY = "2026-09-16"
YESTERDAY = "2026-09-14"


def _env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    sps.ensure_default_plan()
    ts = TaskService(repo)
    manual = ManualTaskService(repo, assessment_repo=arepo, study_plan_service=sps)
    return {
        "repo": repo, "arepo": arepo, "plan_repo": plan_repo, "sps": sps,
        "ts": ts, "manual": manual,
    }


def _agent_task(env, title="Embedding 与向量检索", date=TODAY, topic_id=None):
    return env["repo"].create(
        title, scheduled_date=date, source="generated", task_type="new",
        topic_id=topic_id,
    )


# ================= 10~14：状态语义 =================

class TestCancelledSemantics:
    def test_agent_task_can_cancel(self, conn):
        env = _env(conn)
        t = _agent_task(env)
        out = env["ts"].cancel_task(t.id)
        assert out.status == STATUS_CANCELLED

    def test_manual_task_can_cancel(self, conn):
        env = _env(conn)
        t = env["manual"].create_todo("刷 LeetCode", scheduled_date=TODAY)
        out = env["ts"].cancel_task(t.id)
        assert out.status == STATUS_CANCELLED

    def test_cancelled_not_physically_deleted(self, conn):
        env = _env(conn)
        t = _agent_task(env)
        env["ts"].cancel_task(t.id)
        assert env["repo"].get(t.id) is not None
        assert any(x.id == t.id for x in env["repo"].list_by_date(TODAY))

    def test_cancelled_is_not_not_done(self, conn):
        env = _env(conn)
        t = _agent_task(env)
        out = env["ts"].cancel_task(t.id)
        assert out.is_cancelled is True
        assert out.is_not_done is False
        assert out.is_done is False
        assert out.not_done_at is None

    def test_cancelled_excluded_from_completion_denominator(self, conn):
        env = _env(conn)
        a = _agent_task(env, "A")
        b = _agent_task(env, "B")
        c = _agent_task(env, "C")
        env["ts"].cancel_task(a.id)
        env["ts"].cancel_task(b.id)
        stats = env["ts"].get_daily_stats(TODAY)
        assert stats["total"] == 1  # 只剩 C
        assert stats["active"] == 1
        assert stats["done"] == 0

    def test_done_cannot_cancel(self, conn):
        env = _env(conn)
        t = _agent_task(env)
        env["ts"].complete_task(t.id)
        try:
            env["ts"].cancel_task(t.id)
            assert False, "done 任务不应能 cancelled"
        except InvalidTransitionError:
            pass

    def test_legacy_review_has_generic_task_transitions(self, conn):
        env = _env(conn)
        t = env["repo"].create("历史复习", scheduled_date=TODAY,
                               source="review", task_type="review")
        assert env["ts"].is_cancellable(t) is True
        cancelled = env["ts"].cancel_task(t.id)
        assert cancelled.status == "cancelled"


# ================= 15~18：昨日未确认兼容 =================

class TestPastConfirmation:
    def test_cancelled_not_asked(self, conn):
        env = _env(conn)
        t = _agent_task(env, "A", date=YESTERDAY)
        env["ts"].cancel_task(t.id)
        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        assert svc.find_unresolved(TODAY) == []

    def test_active_manual_asked(self, conn):
        env = _env(conn)
        t = env["manual"].create_knowledge_task("强化学习基础", scheduled_date=YESTERDAY)
        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        assert [x.id for x in svc.find_unresolved(TODAY)] == [t.id]

    def test_manual_todo_confirmed_done(self, conn):
        env = _env(conn)
        t = env["manual"].create_todo("刷题", scheduled_date=YESTERDAY)
        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        svc.apply_decisions({t.id: DECISION_DONE})
        assert env["ts"].get_status(t.id) == STATUS_DONE

    def test_manual_knowledge_confirmed_done_still_assessable(self, conn, qtbot):
        env = _env(conn)
        t = env["manual"].create_knowledge_task("PPO 基础", scheduled_date=YESTERDAY)
        svc = PastTaskConfirmationService(env["repo"], env["ts"])
        svc.apply_decisions({t.id: DECISION_DONE})
        done = env["repo"].get(t.id)
        assert done.status == STATUS_DONE
        assert done.knowledge_point_id is not None
        w = TaskWidget(done)
        qtbot.addWidget(w)
        assert hasattr(w, "assessment_btn") is True
        # done ≠ mastered
        kp = env["arepo"].get_knowledge_point(done.knowledge_point_id)
        assert kp["mastery_estimate"] == 0.0
        assert kp["last_assessed_at"] is None


# ================= 19~22：Planner / coverage 不回归 =================

class TestPlannerExclusion:
    def test_cancelled_topic_not_regenerated_same_day(self, conn):
        env = _env(conn)
        phase = env["sps"].get_current_phase(TODAY)
        first = env["sps"].generate_daily_tasks(TODAY)["generated"][0]
        env["ts"].cancel_task(first.id)
        res = env["sps"].generate_daily_tasks(TODAY)
        generated_titles = [t.title for t in res["generated"]]
        assert first.title not in generated_titles
        assert first.topic_id in res["skipped_cancelled"]

    def test_cancelled_topic_reeligible_next_day(self, conn):
        env = _env(conn)
        first = env["sps"].generate_daily_tasks(TODAY)["generated"][0]
        env["ts"].cancel_task(first.id)
        res = env["sps"].generate_daily_tasks(NEXT_DAY)
        assert first.topic_id in [t.topic_id for t in res["generated"]]

    def test_ai_path_skips_cancelled_topic(self, conn):
        env = _env(conn)
        phase = env["sps"].get_current_phase(TODAY)
        topic = phase.topics[0]
        t = _agent_task(env, topic.name, date=TODAY, topic_id=topic.id)
        env["ts"].cancel_task(t.id)

        class FakePlanner:
            def is_configured(self):
                return True

            def plan_next_day(self, context):
                return DailyPlan(
                    reasoning="r", adjustment="a",
                    recommended_tasks=(
                        RecommendedTask(topic_id=topic.id, title=topic.name,
                                        estimated_minutes=30, priority=2),
                    ),
                    carry_over_tasks=(), daily_minutes=30,
                )

        dps = DailyPlannerService(env["repo"], env["plan_repo"],
                                  planner=FakePlanner(), study_plan_service=env["sps"])
        out = dps.generate_next_day_plan(YESTERDAY, force=True)  # plan_date=TODAY
        assert out["created"] == []
        assert topic.id in dps._cancelled_topic_ids(TODAY)
        assert topic.id not in dps._cancelled_topic_ids(NEXT_DAY)

    def test_done_topic_still_respected(self, conn):
        env = _env(conn)
        first = env["sps"].generate_daily_tasks(TODAY)["generated"][0]
        env["ts"].complete_task(first.id)
        res = env["sps"].generate_daily_tasks(NEXT_DAY)
        assert first.topic_id not in [t.topic_id for t in res["generated"]]

    def test_not_done_still_postponable(self, conn):
        env = _env(conn)
        t = env["manual"].create_todo("刷题", scheduled_date=TODAY)
        env["ts"].mark_not_done(t.id, "没时间")
        out = env["ts"].postpone_task(t.id)
        assert out.status == STATUS_ACTIVE
        assert out.scheduled_date == NEXT_DAY


# ================= 23~24：完成率 =================

class TestCompletionRate:
    def test_cancel_two_of_three_rate_2_3(self, conn):
        env = _env(conn)
        a = _agent_task(env, "A")
        b = _agent_task(env, "B")
        c = _agent_task(env, "C")
        env["ts"].cancel_task(a.id)
        env["ts"].cancel_task(b.id)
        d = env["manual"].create_todo("D", scheduled_date=TODAY)
        e = env["manual"].create_todo("E", scheduled_date=TODAY)
        env["ts"].complete_task(d.id)
        env["ts"].complete_task(e.id)
        stats = env["ts"].get_daily_stats(TODAY)
        assert stats["total"] == 3  # C, D, E
        assert stats["done"] == 2
        assert stats["rate"] == round(2 / 3 * 100, 1)

    def test_cancel_all_agent_rate_2_2(self, conn):
        env = _env(conn)
        for name in ("A", "B", "C"):
            t = _agent_task(env, name)
            env["ts"].cancel_task(t.id)
        d = env["manual"].create_todo("D", scheduled_date=TODAY)
        e = env["manual"].create_todo("E", scheduled_date=TODAY)
        env["ts"].complete_task(d.id)
        env["ts"].complete_task(e.id)
        stats = env["ts"].get_daily_stats(TODAY)
        assert stats["total"] == 2
        assert stats["done"] == 2
        assert stats["rate"] == 100.0


# ================= 27~28：UI 确认 / 持久化 =================

class TestUiAndPersistence:
    def test_remove_confirmation_yes_cancels(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        t = _agent_task(env)
        from app.services.date_service import DateService
        from app.ui import dialogs as dialogs_mod

        monkeypatch.setattr(dialogs_mod, "show_warning", lambda *a, **k: None)
        w = MainWindow(
            task_service=env["ts"],
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
        )
        qtbot.addWidget(w)
        w._confirm_remove_dialog = lambda: True
        w._on_remove_task(t.id)
        assert env["ts"].get_status(t.id) == STATUS_CANCELLED
        # 已从今日待执行列表消失
        assert t.id not in {wd.task().id for wd in w._task_widgets}
        # 但仍保留在数据库
        assert env["repo"].get(t.id) is not None

    def test_remove_confirmation_no_keeps_active(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        t = _agent_task(env)
        from app.services.date_service import DateService
        from app.ui import dialogs as dialogs_mod

        monkeypatch.setattr(dialogs_mod, "show_warning", lambda *a, **k: None)
        w = MainWindow(
            task_service=env["ts"],
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
        )
        qtbot.addWidget(w)
        w._confirm_remove_dialog = lambda: False
        w._on_remove_task(t.id)
        assert env["ts"].get_status(t.id) == STATUS_ACTIVE

    def test_card_shows_remove_button_for_active(self, conn, qtbot):
        env = _env(conn)
        t = _agent_task(env)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert hasattr(w, "remove_btn")
        assert w.remove_btn.text() == "移除今日任务"

    def test_legacy_review_card_has_generic_remove_button(self, conn, qtbot):
        env = _env(conn)
        t = env["repo"].create("历史复习", scheduled_date=TODAY,
                               source="review", task_type="review")
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert hasattr(w, "remove_btn") is True

    def test_cancelled_persists_after_reopen(self, tmp_path):
        from app.database.connection import get_connection

        db = tmp_path / "cancel.db"
        c1 = get_connection(db)
        env = _env(c1)
        t = _agent_task(env)
        env["ts"].cancel_task(t.id)
        c1.close()

        c2 = get_connection(db)
        repo2 = TaskRepository(c2)
        again = repo2.get(t.id)
        assert again is not None
        assert again.status == STATUS_CANCELLED
        c2.close()

    def test_pending_assessment_blocks_remove(self, qtbot, conn, monkeypatch):
        env = _env(conn)
        t = env["manual"].create_knowledge_task("PPI", scheduled_date=TODAY)
        env["arepo"].create_attempt(t.knowledge_point_id, "{}", task_id=t.id)
        from app.services.date_service import DateService
        from app.ui import dialogs as dialogs_mod

        captured = {}
        monkeypatch.setattr(
            dialogs_mod, "show_warning", lambda *a, **k: captured.update(called=True)
        )
        w = MainWindow(
            task_service=env["ts"],
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
        )
        qtbot.addWidget(w)
        w._confirm_remove_dialog = lambda: True
        w._on_remove_task(t.id)
        assert env["ts"].get_status(t.id) == STATUS_ACTIVE
        assert captured.get("called") is True
