"""Review 链路修复：topic -> knowledge_point -> task 幂等映射测试。

对应要求（19 项）—— 1~12 为映射/链路，13~17 为 Review 联动（复用真实服务，
不修改 Review 算法），18 为“不为 review/extra/manual 建 kp”，19 由完整回归覆盖。
"""

from __future__ import annotations

import json

import pytest

from app.ai.schemas import RecommendedTask
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.daily_planner_service import DailyPlannerService
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-09-12"
PHASE_START = "2026-09-01"

QUESTIONS_CONTENT = json.dumps(
    {"questions": [{"question": "解释 autograd", "type": "concept",
                    "expected_points": 3}]},
    ensure_ascii=False,
)
JUDGMENT_CONTENT = json.dumps(
    {"questions": [{"question_index": 0, "verdict": "correct", "reason": "ok"}],
     "weak_points": [], "result_level": "good", "mastery_estimate": 0.8},
    ensure_ascii=False,
)


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return self.responses.pop(0)


def _env(conn, topic_names=("Transformer", "RAG", "Agent")):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    topics = {}
    for i, name in enumerate(topic_names):
        topics[name] = plan_repo.create_topic(
            phase_id=phase.id, name=name, description=f"{name} 描述",
            estimated_minutes=60, priority=3, order_index=i,
        )
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    return {"plan_repo": plan_repo, "repo": repo, "arepo": arepo, "sps": sps,
            "topics": topics}


# ================= 1~3：get_or_create 幂等 =================

class TestKpMapping:
    def test_first_create_for_topic(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        kp = env["arepo"].get_or_create_knowledge_point_for_topic(
            t.id, t.name, t.description)
        assert kp["topic_id"] == t.id
        assert kp["name"] == t.name
        assert kp["mastery_estimate"] == 0.0
        assert kp["last_assessed_at"] is None
        assert kp["next_review_date"] is None

    def test_repeat_returns_same_kp(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        kp1 = env["arepo"].get_or_create_knowledge_point_for_topic(
            t.id, t.name)
        kp2 = env["arepo"].get_or_create_knowledge_point_for_topic(
            t.id, t.name)
        assert kp1["id"] == kp2["id"]
        assert len(env["arepo"].list_knowledge_points()) == 1

    def test_different_topics_different_kps(self, conn):
        env = _env(conn)
        a = env["topics"]["Transformer"]
        b = env["topics"]["RAG"]
        kp_a = env["arepo"].get_or_create_knowledge_point_for_topic(a.id, a.name)
        kp_b = env["arepo"].get_or_create_knowledge_point_for_topic(b.id, b.name)
        assert kp_a["id"] != kp_b["id"]
        assert env["arepo"].get_knowledge_point_by_topic(a.id)["id"] == kp_a["id"]

    def test_existing_by_name_relinked_without_touching_mastery(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        # 旧数据：同名 kp 但 topic_id 为空，且已有 mastery（不得被改）
        old = env["arepo"].create_knowledge_point(t.name)
        old = env["arepo"].update_knowledge_point(
            old["id"], mastery_estimate=0.7,
            last_assessed_at="2026-09-01T00:00:00", review_count=2)
        kp = env["arepo"].get_or_create_knowledge_point_for_topic(t.id, t.name)
        assert kp["id"] == old["id"]
        assert kp["topic_id"] == t.id
        assert kp["mastery_estimate"] == 0.7
        assert kp["review_count"] == 2
        assert len(env["arepo"].list_knowledge_points()) == 1


# ================= 4~5：新任务创建即带 kp =================

class TestNewTaskLinking:
    def test_fallback_rule_task_linked(self, conn):
        env = _env(conn)
        result = env["sps"].generate_daily_tasks(TODAY)
        assert result["generated"]
        for task in result["generated"]:
            assert task.knowledge_point_id is not None
            kp = env["arepo"].get_knowledge_point(task.knowledge_point_id)
            assert kp["topic_id"] == task.topic_id

    def test_ai_recommendation_task_linked(self, conn):
        env = _env(conn)
        planner = DailyPlannerService(
            env["repo"], env["plan_repo"], study_plan_service=env["sps"],
            assessment_repo=env["arepo"],
        )
        t = env["topics"]["RAG"]
        rec = RecommendedTask(topic_id=t.id, title=t.name, description="",
                              estimated_minutes=45, priority=2)
        task = planner._create_task_from_recommendation(
            rec, TODAY, topic_by_id={t.id: t})
        assert task.knowledge_point_id is not None
        kp = env["arepo"].get_knowledge_point(task.knowledge_point_id)
        assert kp["topic_id"] == t.id

    def test_same_topic_tasks_share_one_kp(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        a = env["repo"].create(title="a", scheduled_date=TODAY, source="generated",
                               topic_id=t.id)
        b = env["repo"].create(title="b", scheduled_date="2026-09-13",
                               source="generated", topic_id=t.id)
        a = env["sps"].link_task_knowledge_point(a, t)
        b = env["sps"].link_task_knowledge_point(b, t)
        assert a.knowledge_point_id == b.knowledge_point_id
        assert len(env["arepo"].list_knowledge_points()) == 1


# ================= 6~10：历史 repair =================

class TestRepair:
    def test_repair_links_history(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        task = env["repo"].create(title=t.name, scheduled_date=TODAY,
                                  source="generated", topic_id=t.id,
                                  description="x")
        assert task.knowledge_point_id is None
        res = env["sps"].repair_task_knowledge_points()
        assert res["repaired"] == 1
        assert env["repo"].get(task.id).knowledge_point_id is not None

    def test_repair_idempotent(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        env["repo"].create(title=t.name, scheduled_date=TODAY,
                           source="generated", topic_id=t.id)
        first = env["sps"].repair_task_knowledge_points()
        second = env["sps"].repair_task_knowledge_points()
        assert first["repaired"] == 1
        assert second["repaired"] == 0
        assert second["error"] == 0
        assert len(env["arepo"].list_knowledge_points()) == 1

    def test_done_task_linked_but_no_evidence(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        task = env["repo"].create(title=t.name, scheduled_date=TODAY,
                                  source="generated", topic_id=t.id)
        env["repo"].mark_done(task.id)
        env["sps"].repair_task_knowledge_points()
        got = env["repo"].get(task.id)
        assert got.knowledge_point_id is not None      # 关系补上
        assert got.status == "done"                    # 状态不变
        kp = env["arepo"].get_knowledge_point(got.knowledge_point_id)
        assert kp["mastery_estimate"] == 0.0           # 不推断“已掌握”
        assert kp["last_assessed_at"] is None
        assert env["arepo"].list_attempts() == []      # 不伪造验收

    def test_repair_does_not_change_mastery(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        kp = env["arepo"].create_knowledge_point(t.name, topic_id=t.id)
        env["arepo"].update_knowledge_point(
            kp["id"], mastery_estimate=0.66,
            last_assessed_at="2026-09-05T00:00:00",
            next_review_date="2026-09-20", review_count=3, interval_days=7)
        env["repo"].create(title="z", scheduled_date=TODAY, source="generated",
                           topic_id=t.id)
        env["sps"].repair_task_knowledge_points()
        after = env["arepo"].get_knowledge_point(kp["id"])
        assert after["mastery_estimate"] == 0.66
        assert after["last_assessed_at"] == "2026-09-05T00:00:00"
        assert after["next_review_date"] == "2026-09-20"
        assert after["review_count"] == 3
        assert after["interval_days"] == 7

    def test_repair_only_changes_kp_and_updated_at(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        task = env["repo"].create(
            title=t.name, scheduled_date=TODAY, description="keep-me",
            source="generated", topic_id=t.id, estimated_minutes=45,
            priority=2)
        before = env["repo"].get(task.id)
        env["sps"].repair_task_knowledge_points()
        after = env["repo"].get(task.id)
        assert before.title == after.title
        assert before.description == after.description
        assert before.status == after.status
        assert before.scheduled_date == after.scheduled_date
        assert before.estimated_minutes == after.estimated_minutes
        assert before.source == after.source
        assert before.task_type == after.task_type
        assert before.topic_id == after.topic_id
        assert after.knowledge_point_id is not None


# ================= 18：不为 review/extra/manual 建 kp =================

class TestNoSpuriousKp:
    def test_review_extra_manual_not_linked(self, conn):
        env = _env(conn)
        t = env["topics"]["Transformer"]
        kp = env["arepo"].create_knowledge_point(t.name, topic_id=t.id)
        review = env["repo"].create(title="复习", scheduled_date=TODAY,
                                    source="review", task_type="review",
                                    knowledge_point_id=kp["id"], topic_id=t.id)
        extra = env["repo"].create(title="额外", scheduled_date=TODAY,
                                   source="extra", task_type="extra",
                                   topic_id=t.id)
        manual = env["repo"].create(title="手动", scheduled_date=TODAY,
                                    source="manual", topic_id=t.id)
        env["sps"].repair_task_knowledge_points()
        assert env["repo"].get(extra.id).knowledge_point_id is None
        assert env["repo"].get(manual.id).knowledge_point_id is None
        assert env["repo"].get(review.id).knowledge_point_id == kp["id"]
        assert len(env["arepo"].list_knowledge_points()) == 1

    def test_no_topic_or_missing_topic_safe(self, conn):
        env = _env(conn)
        env["repo"].create(title="无topic", scheduled_date=TODAY,
                           source="generated")
        env["repo"].create(title="孤儿", scheduled_date=TODAY,
                           source="generated", topic_id=99999)
        res = env["sps"].repair_task_knowledge_points()
        assert res["repaired"] == 0
        assert res["error"] == 0


# ================= Assessment -> Mastery，不触发 Review =================

class TestAssessmentDoesNotScheduleReview:
    def test_assessment_updates_mastery_without_review_side_effects(self, conn):
        env = _env(conn)
        task = env["repo"].create(title="study", scheduled_date=TODAY,
                                  source="generated", topic_id=env["topics"]["Transformer"].id)
        task = env["sps"].link_task_knowledge_point(task)
        svc = AssessmentService(
            FakeClient([QUESTIONS_CONTENT, JUDGMENT_CONTENT]),
            assessment_repo=env["arepo"],
        )
        attempt = svc.start_assessment(task.knowledge_point_id, task_id=task.id)
        arepo = env["arepo"]
        arepo.update_knowledge_point(
            task.knowledge_point_id, next_review_date="2026-09-20",
            interval_days=7, review_count=2,
        )
        svc.submit_answers(attempt["id"], ["answer"], today="2026-09-13")
        kp = arepo.get_knowledge_point(task.knowledge_point_id)
        assert kp["mastery_estimate"] == 0.8
        assert kp["last_assessed_at"]
        assert kp["next_review_date"] == "2026-09-20"
        assert kp["interval_days"] == 7
        assert kp["review_count"] == 2
        assert conn.execute("SELECT COUNT(*) FROM review_schedule").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE task_type='review'").fetchone()[0] == 0


# ================= 11~12：UI 安全网 =================

class TestUiSafetyNet:
    def _window(self, qtbot, conn, repo, task_service, date_service):
        from app.ui.main_window import MainWindow
        plan_repo = StudyPlanRepository(conn)
        arepo = AssessmentRepository(conn)
        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
        w = MainWindow(
            task_service=task_service, date_service=date_service,
            today_provider=lambda: TODAY,
            study_plan_service=sps,
            assessment_service=AssessmentService(FakeClient([]), assessment_repo=arepo),
            assessment_repo=arepo,
        )
        qtbot.addWidget(w)
        return w, sps, arepo

    def test_ui_heals_missing_kp_and_proceeds(
        self, qtbot, repo, task_service, date_service, conn
    ):
        w, sps, arepo = self._window(qtbot, conn, repo, task_service,
                                     date_service)
        t = env_topic(conn, "Transformer")
        task = repo.create(title=t.name, scheduled_date=TODAY,
                           source="generated", topic_id=t.id)
        assert task.knowledge_point_id is None
        # 安全网触发：现场补 kp
        healed = w._ensure_task_knowledge_point(task)
        assert healed.knowledge_point_id is not None
        assert arepo.get_knowledge_point_by_topic(t.id) is not None

    def test_ui_no_topic_stays_unsupported(
        self, qtbot, repo, task_service, date_service, conn
    ):
        w, sps, arepo = self._window(qtbot, conn, repo, task_service,
                                     date_service)
        task = repo.create(title="手动任务", scheduled_date=TODAY,
                           source="manual")
        healed = w._ensure_task_knowledge_point(task)
        assert healed.knowledge_point_id is None
        assert arepo.list_knowledge_points() == []


def env_topic(conn, name):
    """为 UI 测试快速建一个 topic。"""
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    return plan_repo.create_topic(phase_id=phase.id, name=name,
                                  description=f"{name} 描述")
