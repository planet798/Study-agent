"""Manual Task × Learning Activity 测试（Phase 2）。"""

from __future__ import annotations

import pytest

from app.services.manual_task_service import ManualTaskService


def _svc(env):
    from app.database.assessment_repository import AssessmentRepository
    from app.services.study_plan_service import StudyPlanService

    sps = StudyPlanService(
        env.repo, env.plan_repo, topic_learning_service=env.tl,
        learning_route_repo=env.route_repo, route_id=env.route_ids[
            "R2_LLM_POST_TRAINING"
        ],
    )
    return ManualTaskService(
        env.repo,
        assessment_repo=AssessmentRepository(env.conn),
        study_plan_service=sps,
        topic_learning_service=env.tl,
    )


def _lora(env):
    return env.plan_repo.find_topic_by_name_in_route(
        env.route_ids["R2_LLM_POST_TRAINING"], "LoRA / QLoRA"
    )


class TestManualLinkedTopic:
    def test_default_next_component(self, activity_env):
        env = activity_env
        lora = _lora(env)
        task = _svc(env).create_knowledge_task(
            "LoRA 学习", topic_id=lora.id, scheduled_date="2026-01-05",
        )
        assert task.learning_activity_kind == "theory"
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        assert task.component_id == comp["id"]

    def test_explicit_component(self, activity_env):
        env = activity_env
        lora = _lora(env)
        interview = env.tl.repo.get_by_topic_and_kind(lora.id, "interview")
        task = _svc(env).create_knowledge_task(
            "LoRA 面试", topic_id=lora.id,
            component_id=interview["id"], scheduled_date="2026-01-05",
        )
        assert task.component_id == interview["id"]
        assert task.learning_activity_kind == "interview"

    def test_component_must_belong_to_topic(self, activity_env):
        env = activity_env
        lora = _lora(env)
        sft = env.plan_repo.find_topic_by_name_in_route(
            env.route_ids["R2_LLM_POST_TRAINING"], "SFT 指令微调"
        )
        sft_comp = env.tl.repo.get_by_topic_and_kind(sft.id, "theory")
        with pytest.raises(ValueError):
            _svc(env).create_knowledge_task(
                "错误关联", topic_id=lora.id, component_id=sft_comp["id"],
                scheduled_date="2026-01-05",
            )

    def test_custom_activity_no_component(self, activity_env):
        env = activity_env
        lora = _lora(env)
        task = _svc(env).create_knowledge_task(
            "LoRA 自定义", topic_id=lora.id,
            learning_activity_kind="experiment", scheduled_date="2026-01-05",
        )
        assert task.component_id is None
        assert task.learning_activity_kind == "experiment"


class TestStandaloneManual:
    def test_standalone_activity(self, activity_env):
        env = activity_env
        task = _svc(env).create_knowledge_task(
            "临时知识点", learning_activity_kind="interview",
            scheduled_date="2026-01-05",
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
        )
        assert task.component_id is None
        assert task.learning_activity_kind == "interview"
        assert task.topic_id is None

    def test_standalone_rejects_component(self, activity_env):
        env = activity_env
        lora = _lora(env)
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        with pytest.raises(ValueError):
            _svc(env).create_knowledge_task(
                "临时", topic_id=None, component_id=comp["id"],
                scheduled_date="2026-01-05",
            )


class TestOrdinaryActivity:
    def test_activity_has_no_activity(self, activity_env):
        env = activity_env
        task = _svc(env).create_learning_activity(
            "刷邮件", scheduled_date="2026-01-05",
        )
        assert task.component_id is None
        assert task.learning_activity_kind is None
