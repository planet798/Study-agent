"""Capability UI + 边界测试（Phase 3，offscreen）。"""

from __future__ import annotations

import inspect

from app.database.assessment_repository import AssessmentRepository
from app.database.capability_repository import CapabilityEvidenceRepository
from app.database.skill_repository import LearningOutcomeRepository
from app.services.capability import AWARE, IMPLEMENT
from app.services.capability_service import CapabilityService
from app.services.learning_outcome_service import LearningOutcomeService
from app.services.learning_route_service import LearningRouteService
from app.services.route_plan_service import RoutePlanService
from app.services.route_progress_service import RouteProgressService
from app.services.task_service import TaskService


def _labels(widget):
    from PySide6.QtWidgets import QLabel, QPushButton

    out = [lbl.text() for lbl in widget.findChildren(QLabel)]
    out += [b.text() for b in widget.findChildren(QPushButton)]
    return out


def _env(activity_env):
    env = activity_env
    cap = CapabilityService(env.conn, CapabilityEvidenceRepository(env.conn))
    out = LearningOutcomeService(
        LearningOutcomeRepository(env.conn), capability_service=cap
    )
    a_repo = AssessmentRepository(env.conn)
    progress = RouteProgressService(
        env.repo, a_repo, env.plan_repo, env.route_repo,
        topic_learning_service=env.tl, capability_service=cap,
    )
    return cap, out, a_repo, progress


def _lora(env):
    return env.plan_repo.find_topic_by_name_in_route(
        env.route_ids["R2_LLM_POST_TRAINING"], "LoRA / QLoRA"
    )


def _dialog(env, progress, cap, out, qtbot):
    from app.ui.routes_page import RouteDetailDialog

    route = env.route_repo.get_by_key("R2_LLM_POST_TRAINING")
    dlg = RouteDetailDialog(
        route,
        LearningRouteService(env.route_repo, skill_repo=env.skill_repo),
        RoutePlanService(env.plan_repo, env.repo, topic_learning_service=env.tl),
        progress_service=progress,
        today_provider=lambda: "2026-01-05",
        topic_learning_service=env.tl,
        capability_service=cap,
        outcome_service=out,
    )
    qtbot.addWidget(dlg)
    return dlg


def _aware(env, cap):
    lora = _lora(env)
    a_repo = AssessmentRepository(env.conn)
    kp = a_repo.get_or_create_knowledge_point_for_topic(
        lora.id, lora.name, "", route_id=env.route_ids["R2_LLM_POST_TRAINING"]
    )
    comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
    t = env.repo.create(
        "LoRA 理论", scheduled_date="2026-01-05", topic_id=lora.id,
        knowledge_point_id=kp["id"],
        route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        task_type="new", source="generated", component_id=comp["id"],
        learning_activity_kind="theory",
    )
    env.repo.mark_done(t.id)
    cap.sync_from_task(t.id)
    return kp


class TestRouteDetailCapability:
    def test_aware_label(self, activity_env, qtbot):
        cap, out, a_repo, progress = _env(activity_env)
        _aware(activity_env, cap)
        dlg = _dialog(activity_env, progress, cap, out, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "Capability：L1 · 知道概念" in joined
        assert "查看证据" in joined

    def test_no_evidence_label(self, activity_env, qtbot):
        cap, out, a_repo, progress = _env(activity_env)
        dlg = _dialog(activity_env, progress, cap, out, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "暂无能力证据" in joined

    def test_mastery_and_capability_side_by_side(self, activity_env, qtbot):
        env = activity_env
        cap, out, a_repo, progress = _env(env)
        kp = _aware(env, cap)
        a_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.58,
            last_assessed_at="2026-01-05T10:00:00",
        )
        dlg = _dialog(env, progress, cap, out, qtbot)
        joined = "\n".join(_labels(dlg))
        assert "Mastery：58%" in joined
        assert "Capability：L1 · 知道概念" in joined

    def test_evidence_dialog_timeline(self, activity_env, qtbot):
        from app.ui.capability_dialog import CapabilityEvidenceDialog

        env = activity_env
        cap, _out, _a, _p = _env(env)
        kp = _aware(env, cap)
        dlg = CapabilityEvidenceDialog(kp["name"], kp["id"], cap)
        qtbot.addWidget(dlg)
        joined = "\n".join(_labels(dlg))
        assert "当前能力：知道概念" in joined
        assert "learning_activity" in joined

    def test_evidence_dialog_empty(self, activity_env, qtbot):
        from app.ui.capability_dialog import CapabilityEvidenceDialog

        env = activity_env
        cap, _out, a_repo, _p = _env(env)
        lora = _lora(env)
        kp = a_repo.get_or_create_knowledge_point_for_topic(
            lora.id, lora.name, "",
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        )
        dlg = CapabilityEvidenceDialog(kp["name"], kp["id"], cap)
        qtbot.addWidget(dlg)
        joined = "\n".join(_labels(dlg))
        assert "暂无能力证据" in joined


class TestExperimentRecordFlow:
    def test_needs_record_and_dialog_creates_experiment(
        self, activity_env, qtbot
    ):
        from app.ui.capability_dialog import ExperimentOutcomeDialog

        env = activity_env
        cap, out, a_repo, progress = _env(env)
        lora = _lora(env)
        kp = a_repo.get_or_create_knowledge_point_for_topic(
            lora.id, lora.name, "",
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        )
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "experiment")
        t = env.repo.create(
            "LoRA 实验", scheduled_date="2026-01-05", topic_id=lora.id,
            knowledge_point_id=kp["id"],
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
            task_type="new", source="generated", component_id=comp["id"],
            learning_activity_kind="experiment",
        )
        env.repo.mark_done(t.id)

        dlg = ExperimentOutcomeDialog(
            kp["name"], kp["id"], out, env.repo
        )
        qtbot.addWidget(dlg)
        dlg.title_edit.setText("Qwen LoRA 微调")
        dlg.result_edit.setPlainText("loss 从 2.1 降到 0.8")
        dlg.metrics_edit.setPlainText("loss=0.8")
        dlg._on_save()
        assert cap.get_current_level(kp["id"]) == 4  # EXPERIMENT

    def test_dialog_requires_artifact(self, activity_env, qtbot):
        from app.ui.capability_dialog import ExperimentOutcomeDialog

        env = activity_env
        cap, out, a_repo, _p = _env(env)
        lora = _lora(env)
        kp = a_repo.get_or_create_knowledge_point_for_topic(
            lora.id, lora.name, "",
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        )
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "experiment")
        t = env.repo.create(
            "exp", scheduled_date="2026-01-05", topic_id=lora.id,
            knowledge_point_id=kp["id"],
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
            task_type="new", source="generated", component_id=comp["id"],
            learning_activity_kind="experiment",
        )
        env.repo.mark_done(t.id)
        dlg = ExperimentOutcomeDialog(kp["name"], kp["id"], out, env.repo)
        qtbot.addWidget(dlg)
        dlg.result_edit.setPlainText("做完了")
        dlg._on_save()
        assert dlg.error_label.text() != ""
        assert cap.get_current_level(kp["id"]) == 0


class TestHardBoundaries:
    def test_scheduler_and_plan_service_do_not_read_capability(self):
        # Phase 6：Scheduler 不读 capability；StudyPlanService 也不读。
        # DailyPlannerService 只通过 PlannerFeedbackService 间接获取能力缺口，
        # 绝不直接 import CapabilityService。
        import app.services.daily_planner_service as dps
        import app.services.route_scheduler as sched
        import app.services.study_plan_service as sps

        for mod in (sched, sps):
            src = inspect.getsource(mod).lower()
            assert "capability" not in src, mod.__name__
        planner_src = inspect.getsource(dps)
        assert "CapabilityService" not in planner_src
        assert "capability_repository" not in planner_src
        assert "from .capability" not in planner_src

    def test_scheduler_fairness_still_works(self, activity_env):
        from app.services.route_scheduler import GlobalDailyScheduler

        sched = GlobalDailyScheduler(
            activity_env.repo, activity_env.plan_repo,
            activity_env.route_repo,
            topic_learning_service=activity_env.tl,
        )
        res = sched.generate("2026-01-05")
        assert len(res["created_ids"]) <= 3

    def test_monthly_summary_prompt_says_activity_not_capability(self):
        from app.ai.prompt_defaults import SUMMARY_MONTHLY_USER

        assert "activity_completed" in SUMMARY_MONTHLY_USER
        assert "能力等级" in SUMMARY_MONTHLY_USER

    def test_assessment_mastery_weights_unchanged(self):
        from app.services import assessment_service as a

        assert a._PREVIOUS_WEIGHT == 0.7
        assert a._NEW_WEIGHT == 0.3
