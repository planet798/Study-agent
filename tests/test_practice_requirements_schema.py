"""Phase 6：v20 schema + PracticeTopicRequirement 基础测试。"""

from __future__ import annotations

import pytest

from tests.practice_capability_helpers import (
    make_requirement_project,
    topic_in_current_phase,
)


class TestSchemaV20:
    def test_version_and_table(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "practice_topic_requirements" in names

    def test_columns_and_unique(self, conn):
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(practice_topic_requirements)"
        )]
        assert cols == [
            "id", "project_id", "topic_id", "target_capability_level",
            "is_active", "note", "created_at", "updated_at",
        ]
        sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE name='practice_topic_requirements'"
        ).fetchone()[0]
        assert "UNIQUE(project_id, topic_id)" in sql
        assert "BETWEEN 1 AND 4" in sql

    def test_migration_idempotent(self, tmp_path):
        from app.database.connection import get_connection
        from app.database.schema import migrate

        c = get_connection(str(tmp_path / "idem.db"))
        assert migrate(c) == 20
        assert migrate(c) == 20
        c.close()

    def test_v19_to_v20_sequential_no_auto_requirement(self, tmp_path):
        from app.database.connection import get_connection
        from app.database.repository import TaskRepository
        from app.database.study_plan_repository import StudyPlanRepository
        from app.database.learning_route_repository import (
            LearningRouteRepository,
        )
        from app.database.skill_repository import SkillRepository
        from app.services.study_plan_service import StudyPlanService
        from app.services.canonical_route_service import CanonicalRouteService

        c = get_connection(str(tmp_path / "v20.db"))
        repo = TaskRepository(c)
        plan_repo = StudyPlanRepository(c)
        StudyPlanService(repo, plan_repo).ensure_default_plan()
        CanonicalRouteService(
            c, LearningRouteRepository(c), plan_repo, SkillRepository(c)
        ).ensure_all()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 20
        assert c.execute(
            "SELECT COUNT(*) FROM practice_topic_requirements"
        ).fetchone()[0] == 0
        c.close()


class TestRequirementRules:
    def test_target_levels_1_to_4(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"], targets={"LoRA / QLoRA": 1}
        )
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        assert req["target_capability_level"] == 1
        env.readiness.set_requirement(project["id"], topics[0].id, 4)
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        assert req["target_capability_level"] == 4

    def test_project_target_rejected(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"]
        )
        with pytest.raises(ValueError):
            env.readiness.set_requirement(project["id"], topics[0].id, 5)

    def test_zero_and_negative_rejected(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"]
        )
        for bad in (0, -1, 6, "x"):
            with pytest.raises(ValueError):
                env.readiness.set_requirement(project["id"], topics[0].id, bad)

    def test_only_linked_topic(self, practice_readiness_env):
        env = practice_readiness_env
        project, _ = make_requirement_project(env, env.r2.id, ["LoRA / QLoRA"])
        other = topic_in_current_phase(env, env.r2.id, "SFT 指令微调")
        with pytest.raises(ValueError) as exc:
            env.readiness.set_requirement(project["id"], other.id, 3)
        assert "topic_not_linked_to_project" in str(exc.value)
        # 不会自动创建 project topic relation
        assert other.id not in env.service.projects.list_topic_ids(
            project["id"]
        )

    def test_create_and_update_same_row(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"], targets={"LoRA / QLoRA": 2}
        )
        rid = env.readiness.get_requirement(project["id"], topics[0].id)["id"]
        env.readiness.set_requirement(project["id"], topics[0].id, 4,
                                      note="更新")
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        assert req["id"] == rid  # update same row
        assert req["target_capability_level"] == 4
        assert req["note"] == "更新"
        assert env.requirement_repo.count_by_project(project["id"]) == 1

    def test_deactivate_not_delete(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"]
        )
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        env.readiness.deactivate_requirement(req["id"])
        assert env.readiness.get_requirement(
            project["id"], topics[0].id
        )["is_active"] is False
        # 历史行仍在
        assert env.requirement_repo.count_by_project(
            project["id"], active_only=False
        ) == 1
        assert env.requirement_repo.count_by_project(project["id"]) == 0

    def test_reactivate_same_row(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r2.id, ["LoRA / QLoRA"]
        )
        req = env.readiness.get_requirement(project["id"], topics[0].id)
        env.readiness.deactivate_requirement(req["id"])
        env.readiness.set_requirement(project["id"], topics[0].id, 4)
        again = env.readiness.get_requirement(project["id"], topics[0].id)
        assert again["id"] == req["id"]
        assert again["is_active"] is True

    def test_no_auto_seed_for_project(self, practice_readiness_env):
        env = practice_readiness_env
        project = env.service.create_project("P", "llm_training",
                                            route_ids=[env.r2.id])
        lora = topic_in_current_phase(env, env.r2.id, "SFT 指令微调")
        env.service.add_topic(project["id"], lora.id)
        # 关联 Topic 不会自动生成 requirement
        assert env.readiness.get_requirement(project["id"], lora.id) is None
        assert env.requirement_repo.count_by_project(project["id"]) == 0
