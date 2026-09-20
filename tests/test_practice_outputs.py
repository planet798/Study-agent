"""Practice Output 测试（Phase 4）。"""

from __future__ import annotations

import pytest

from app.services.practice_project_service import PracticeError


class TestOutputs:
    def test_create_types_and_count(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.add_output(p["id"], "repository", "GitHub",
                               uri="https://github.com/x/y")
        env.service.add_output(p["id"], "benchmark", "LoRA vs FT",
                               details={"metric": "loss", "value": 0.8})
        env.service.add_output(p["id"], "report", "实验报告")
        assert env.service.output_count(p["id"]) == 3
        outs = env.service.outputs.list_by_project(p["id"])
        bench = next(o for o in outs if o["output_type"] == "benchmark")
        assert bench["details"]["metric"] == "loss"

    def test_update_output(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        o = env.service.add_output(p["id"], "repository", "repo")
        updated = env.service.update_output(
            o["id"], title="repo v2", details={"branch": "main"}
        )
        assert updated["title"] == "repo v2"
        assert updated["details"]["branch"] == "main"

    def test_delete_output(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        o = env.service.add_output(p["id"], "demo", "demo")
        assert env.service.delete_output(o["id"]) is True
        assert env.service.output_count(p["id"]) == 0

    def test_invalid_type(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.add_output(p["id"], "bogus", "t")

    def test_secret_uri_rejected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(ValueError):
            env.service.add_output(
                p["id"], "repository", "repo",
                uri="https://user:token@github.com/x/y",
            )
        with pytest.raises(ValueError):
            env.service.add_output(
                p["id"], "code", "code", description="api_key=sk-abcdefghijklmnop"
            )
        assert env.service.output_count(p["id"]) == 0

    def test_secret_in_update_rejected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        o = env.service.add_output(p["id"], "repository", "repo")
        with pytest.raises(ValueError):
            env.service.update_output(o["id"], uri="https://a:b@host/x")

    def test_archive_preserves_outputs(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.add_output(p["id"], "repository", "repo")
        env.service.archive_project(p["id"])
        assert env.service.output_count(p["id"]) == 1

    def test_output_and_learning_outcome_are_separate(self, practice_env):
        from app.database.skill_repository import LearningOutcomeRepository

        env = practice_env
        p = env.service.create_project("P", "other")
        env.service.add_output(p["id"], "repository", "repo")
        # practice_outputs 与 learning_outcomes 是两张独立表
        assert LearningOutcomeRepository(env.conn).count() == 0
