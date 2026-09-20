"""PracticeProjectService CRUD / status / archive 测试（Phase 4）。"""

from __future__ import annotations

from app.services.practice_project_service import (
    PracticeError,
    PracticeProjectService,
)


class TestCreateUpdate:
    def test_create_and_update(self, practice_env):
        env = practice_env
        p = env.service.create_project(
            "Qwen LoRA", "llm_training", goal="跑通", description="desc"
        )
        assert p["status"] == "planned"
        assert p["project_type"] == "llm_training"
        updated = env.service.update_project(p["id"], name="Qwen LoRA v2")
        assert updated["name"] == "Qwen LoRA v2"

    def test_invalid_type(self, practice_env):
        import pytest

        with pytest.raises(PracticeError):
            practice_env.service.create_project("P", "bogus")

    def test_blank_name(self, practice_env):
        import pytest

        with pytest.raises(ValueError):
            practice_env.service.create_project("  ", "other")


class TestStatus:
    def test_status_flow_completed_at(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        assert env.service.set_status(p["id"], "in_progress")["started_at"]
        c = env.service.set_status(p["id"], "completed")
        assert c["status"] == "completed"
        assert c["completed_at"]
        # reopen → 清空 completed_at
        r = env.service.set_status(p["id"], "in_progress")
        assert r["status"] == "in_progress"
        assert r["completed_at"] is None

    def test_set_status_archived_rejected(self, practice_env):
        import pytest

        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.set_status(p["id"], "archived")


class TestArchiveRestore:
    def test_archive_restore_completed(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.set_status(p["id"], "completed")
        a = env.service.archive_project(p["id"])
        assert a["status"] == "archived"
        assert a["archived_from_status"] == "completed"
        r = env.service.restore_project(p["id"])
        assert r["status"] == "completed"

    def test_archive_restore_in_progress(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.set_status(p["id"], "in_progress")
        env.service.archive_project(p["id"])
        assert env.service.restore_project(p["id"])["status"] == "in_progress"

    def test_archive_preserves_children(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        env.service.add_milestone(p["id"], "m1")
        env.service.add_output(p["id"], "repository", "repo")
        env.service.archive_project(p["id"])
        detail = env.service.get_project_detail(p["id"])
        assert len(detail["milestones"]) == 1
        assert len(detail["outputs"]) == 1
        assert detail["routes"]


class TestDeleteProtection:
    def test_empty_project_delete(self, practice_env):
        env = practice_env
        p = env.service.create_project("Empty", "other")
        assert env.service.delete_project(p["id"]) is True
        assert env.service.projects.get(p["id"]) is None

    def test_project_with_history_protected(self, practice_env):
        import pytest

        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.add_milestone(p["id"], "m1")
        with pytest.raises(PracticeError):
            env.service.delete_project(p["id"])
        assert env.service.projects.get(p["id"]) is not None


class TestQueries:
    def test_list_active_archived_and_by_route(self, practice_env):
        env = practice_env
        a = env.service.create_project("A", "other", route_ids=[env.r1.id])
        b = env.service.create_project("B", "other", route_ids=[env.r2.id])
        env.service.archive_project(b["id"])
        active_names = {x["name"] for x in env.service.list_projects()}
        assert "A" in active_names and "B" in active_names
        archived = {x["name"] for x in env.service.projects.list_archived()}
        assert archived == {"B"}
        by_route = {x["name"] for x in env.service.list_by_route(env.r1.id)}
        assert by_route == {"A"}
        assert env.service.count_by_route(env.r1.id) == 1
