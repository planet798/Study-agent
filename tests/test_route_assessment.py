"""Phase E：Assessment 全链路 route-aware。

覆盖需求 36 的 1~9、19、24。
"""

from __future__ import annotations

import json

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.assessment_service import AssessmentService
from app.services.learning_route_service import LearningRouteService
from app.services.manual_task_service import ManualTaskService
from app.services.route_plan_service import RoutePlanService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService

TODAY = "2026-09-15"

QUESTIONS = json.dumps(
    {"questions": [{"question": "解释 MDP", "type": "concept",
                    "expected_points": 3}]},
    ensure_ascii=False,
)
JUDGMENT = json.dumps(
    {"questions": [{"question_index": 0, "verdict": "correct", "reason": "ok"}],
     "weak_points": [], "result_level": "good", "mastery_estimate": 0.8},
    ensure_ascii=False,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append(user_prompt)
        return self.responses.pop(0)


@pytest.fixture()
def env(conn):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    route_service = LearningRouteService(route_repo)
    route_plan = RoutePlanService(plan_repo, repo, arepo)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                           learning_route_repo=route_repo)
    sps.ensure_default_plan()
    return {
        "conn": conn, "repo": repo, "arepo": arepo, "plan_repo": plan_repo,
        "route_repo": route_repo, "route_service": route_service,
        "route_plan": route_plan, "sps": sps,
        "manual": ManualTaskService(repo, assessment_repo=arepo,
                                    study_plan_service=sps),
    }


def _route_with_topics(env, name, topics):
    rl = env["route_service"].create_learning_route(name, priority=5)
    env["route_plan"].ensure_manual_plan(rl.id, rl.name)
    phase = env["route_plan"].add_phase(rl.id, f"{name} 基础")
    out = []
    for i, t in enumerate(topics):
        out.append(env["route_plan"].add_topic(rl.id, phase.id, t, order_index=i))
    return rl, out


# ================= 1~5：route 一致性 =================

class TestRouteConsistency:
    def test_task_and_kp_route_consistent(self, env):
        rl, topics = _route_with_topics(env, "RL", ["MDP"])
        t = env["manual"].create_knowledge_task(
            "MDP", topic_id=topics[0].id, scheduled_date=TODAY
        )
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert t.route_id == kp["route_id"] == rl.id

    def test_topic_kp_auto_route(self, env):
        rl, topics = _route_with_topics(env, "RL", ["MDP"])
        task = env["repo"].create("MDP", scheduled_date=TODAY,
                                  source="generated", task_type="new",
                                  topic_id=topics[0].id)
        linked = env["sps"].link_task_knowledge_point(task)
        assert linked.route_id == rl.id
        assert env["arepo"].get_knowledge_point(linked.knowledge_point_id)[
            "route_id"] == rl.id

    def test_manual_kp_route_preserved(self, env):
        rl, _ = _route_with_topics(env, "RL", ["MDP"])
        kp = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl.id
        )
        assert kp["route_id"] == rl.id
        kp2 = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl.id
        )
        assert kp2["id"] == kp["id"]

    def test_same_name_kp_independent_across_routes(self, env):
        rl, _ = _route_with_topics(env, "RL", ["MDP"])
        cpp, _ = _route_with_topics(env, "C++", ["STL"])
        kp_rl = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl.id
        )
        kp_cpp = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=cpp.id
        )
        assert kp_rl["id"] != kp_cpp["id"]
        assert kp_rl["route_id"] == rl.id
        assert kp_cpp["route_id"] == cpp.id

    def test_ensure_kp_route_consistency_repairs(self, env):
        rl, topics = _route_with_topics(env, "RL", ["MDP"])
        # 人为制造错误 route
        kp = env["arepo"].create_knowledge_point("MDP", topic_id=topics[0].id)
        env["arepo"].update_knowledge_point(kp["id"], route_id=None)
        fixed = env["arepo"].ensure_knowledge_point_route_consistency()
        assert fixed == 1
        assert env["arepo"].get_knowledge_point(kp["id"])["route_id"] == rl.id
        # 幂等
        assert env["arepo"].ensure_knowledge_point_route_consistency() == 0

    def test_pending_attempt_not_cross_route(self, env):
        rl_a, ta = _route_with_topics(env, "A", ["基础"])
        rl_b, tb = _route_with_topics(env, "B", ["基础"])
        kp_a = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl_a.id
        )
        task_a = env["manual"].create_knowledge_task(
            "基础", scheduled_date=TODAY, route_id=rl_a.id
        )
        env["arepo"].create_attempt(kp_a["id"], "{}", task_id=task_a.id)
        task_b = env["manual"].create_knowledge_task(
            "基础", scheduled_date=TODAY, route_id=rl_b.id
        )
        assert env["arepo"].find_pending_attempt_for_task(task_a.id) is not None
        assert env["arepo"].find_pending_attempt_for_task(task_b.id) is None


# ================= 6~9：判题 / mastery / weak 不串路线 =================

class TestGradingIsolation:
    def _service(self, env, responses):
        return AssessmentService(
            FakeClient(responses), assessment_repo=env["arepo"]
        )

    def test_grading_updates_only_own_kp(self, env):
        rl_a, _ = _route_with_topics(env, "A", ["基础"])
        rl_b, _ = _route_with_topics(env, "B", ["基础"])
        kp_a = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl_a.id
        )
        kp_b = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl_b.id
        )
        svc = self._service(env, [QUESTIONS, JUDGMENT])
        attempt = svc.start_assessment(kp_a["id"])
        svc.submit_answers(attempt["id"], ["answer"])
        a = env["arepo"].get_knowledge_point(kp_a["id"])
        b = env["arepo"].get_knowledge_point(kp_b["id"])
        assert a["mastery_estimate"] == pytest.approx(0.8)
        assert a["last_assessed_at"] is not None
        assert b["mastery_estimate"] == 0.0
        assert b["last_assessed_at"] is None

    def test_weak_points_not_cross_route(self, env):
        rl_a, _ = _route_with_topics(env, "A", ["基础"])
        rl_b, _ = _route_with_topics(env, "B", ["基础"])
        kp_a = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl_a.id
        )
        kp_b = env["manual"].get_or_create_manual_knowledge_point(
            "基础", "", route_id=rl_b.id
        )
        weak_judgment = json.dumps(
            {"questions": [{"question_index": 0, "verdict": "incorrect",
                            "reason": "no"}],
             "weak_points": ["V 与 Q 区别"], "result_level": "poor",
             "mastery_estimate": 0.3},
            ensure_ascii=False,
        )
        svc = self._service(env, [QUESTIONS, weak_judgment])
        attempt = svc.start_assessment(kp_a["id"])
        svc.submit_answers(attempt["id"], ["answer"])
        weak_a = env["arepo"].list_weak_by_route(rl_a.id)
        weak_b = env["arepo"].list_weak_by_route(rl_b.id)
        assert [k["id"] for k in weak_a] == [kp_a["id"]]
        assert weak_b == []


# ================= UI route mismatch guard =================

class TestMismatchGuard:
    def test_mismatch_blocks_assessment(self, env, qtbot, monkeypatch):
        from app.services.date_service import DateService
        from app.ui import dialogs as dialogs_mod
        from app.ui.main_window import MainWindow

        rl_a, ta = _route_with_topics(env, "A", ["基础"])
        rl_b, tb = _route_with_topics(env, "B", ["基础"])
        kp_a = env["arepo"].create_knowledge_point("基础", topic_id=ta[0].id)
        env["arepo"].update_knowledge_point(kp_a["id"], route_id=rl_a.id)
        # 任务故意挂到另一条 route
        task = env["repo"].create("错配", scheduled_date=TODAY,
                                  source="manual", task_type="new",
                                  topic_id=None, knowledge_point_id=kp_a["id"],
                                  route_id=rl_b.id)
        warnings = []
        monkeypatch.setattr(dialogs_mod, "show_warning",
                            lambda *a, **k: warnings.append(a))

        class DummySvc:
            def is_configured(self):
                return False

        w = MainWindow(
            task_service=TaskService(env["repo"]),
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            assessment_service=DummySvc(),
            manual_task_service=env["manual"],
            route_service=env["route_service"],
            route_plan_service=env["route_plan"],
        )
        qtbot.addWidget(w)
        w._on_start_assessment(task.id)
        assert warnings
