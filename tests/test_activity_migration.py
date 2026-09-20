"""Phase 1 → v16 顺序迁移 + Activity backfill 测试（Phase 2）。"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.connection import get_connection
from app.database.learning_route_repository import LearningRouteRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.database.topic_learning_repository import (
    TopicLearningComponentRepository,
)
from app.services.canonical_route_service import CanonicalRouteService
from app.services.study_plan_service import StudyPlanService
from app.services.topic_learning_profile_service import (
    TopicLearningProfileService,
)


def _build_legacy(path):
    conn = get_connection(path)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    StudyPlanService(repo, plan_repo).ensure_default_plan()
    route_repo = LearningRouteRepository(conn)
    legacy = route_repo.get_default_learning_route()
    # 历史 done 正式任务（无 component）+ kp/assessment 证据
    topic = plan_repo.find_topic_by_name_in_route(
        legacy.id, "Function Calling / Tool Calling"
    )
    task = repo.create(
        "Function Calling 学习", scheduled_date="2026-09-05",
        topic_id=topic.id, route_id=legacy.id, source="generated",
        task_type="new",
    )
    repo.mark_done(task.id)
    a_repo = AssessmentRepository(conn)
    kp = a_repo.get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, topic.description, route_id=legacy.id
    )
    a_repo.update_knowledge_point(
        kp["id"], mastery_estimate=0.73,
        last_assessed_at="2026-09-05T10:00:00",
    )
    conn.close()
    return {"topic_id": topic.id, "task_id": task.id, "kp_id": kp["id"]}


class TestSequentialMigration:
    def test_v15_to_v16_and_backfill(self, tmp_path):
        path = tmp_path / "legacy.db"
        info = _build_legacy(path)
        conn = get_connection(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 16

        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        tl = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        res = CanonicalRouteService(
            conn, route_repo, plan_repo, skill_repo,
            topic_learning_service=tl,
        ).ensure_all()
        # Phase 1 migration 仍无损
        assert res["migration"]["applied"] is True
        assert res["activity_profiles"] == 93
        # backfill：历史 done task → theory
        assert res["legacy_theory_backfill"]["legacy_theory_backfilled"] >= 1

        # MOVE 保留 topic id；component 绑定为 theory
        fn_topic = plan_repo.get_topic(info["topic_id"])
        r4 = route_repo.get_by_key("R4_AI_AGENT")
        assert plan_repo.get_route_id_for_topic(fn_topic.id) == r4.id
        theory = tl.repo.get_by_topic_and_kind(fn_topic.id, "theory")
        assert tl.repo.is_component_done(theory["id"]) is True
        # 不推断 experiment
        experiment = tl.repo.get_by_topic_and_kind(fn_topic.id, "experiment")
        assert tl.repo.is_component_done(experiment["id"]) is False
        # mastery 证据保留
        a_repo = AssessmentRepository(conn)
        kp = a_repo.get_knowledge_point(info["kp_id"])
        assert float(kp["mastery_estimate"]) == 0.73
        # 历史 task 绑定了 learning_activity_kind=theory
        row = conn.execute(
            "SELECT learning_activity_kind, component_id FROM tasks WHERE id=?",
            (info["task_id"],),
        ).fetchone()
        assert row[0] == "theory"
        assert row[1] == theory["id"]
        conn.close()

    def test_backfill_is_idempotent(self, tmp_path):
        path = tmp_path / "legacy.db"
        _build_legacy(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        tl = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo,
                              topic_learning_service=tl).ensure_all()
        second = tl.backfill_legacy_theory()
        assert second["legacy_theory_backfilled"] == 0
        conn.close()

    def test_split_new_topics_have_no_inherited_completion(self, tmp_path):
        path = tmp_path / "legacy.db"
        _build_legacy(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        tl = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo,
                              topic_learning_service=tl).ensure_all()
        # 新 SPLIT_NEW topic：Embedding Fundamentals 全新
        r1 = route_repo.get_by_key("R1_LLM_FUNDAMENTALS")
        new_topic = plan_repo.find_topic_by_name_in_route(
            r1.id, "Embedding Fundamentals"
        )
        assert tl.is_topic_curriculum_complete(new_topic.id) is False
        assert tl.get_next_required_component(new_topic.id)[
            "activity_kind"] == "theory"
        conn.close()

    def test_legacy_route_not_forced(self, tmp_path):
        path = tmp_path / "legacy.db"
        _build_legacy(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        tl = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo,
                              topic_learning_service=tl).ensure_all()
        legacy = route_repo.get_by_key("LEGACY_SEARCH_LLM")
        # LEGACY 下的 Topic 不应该被强制建 component profile
        legacy_topics = plan_repo.list_topics_by_route(legacy.id)
        profiled = [
            t for t in legacy_topics if tl.has_profile(t.id)
        ]
        assert profiled == []
        conn.close()
