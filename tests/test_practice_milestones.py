"""Practice Milestone 测试（Phase 4）。"""

from __future__ import annotations

import pytest

from app.services.practice_project_service import PracticeError


class TestMilestones:
    def test_create_and_order(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        m1 = env.service.add_milestone(p["id"], "环境准备")
        m2 = env.service.add_milestone(p["id"], "数据构造")
        assert m1["order_index"] == 1
        assert m2["order_index"] == 2
        assert [m["title"] for m in
                env.service.milestones.list_by_project(p["id"])] == [
            "环境准备", "数据构造"
        ]

    def test_status_flow_and_completed_at(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        m = env.service.add_milestone(p["id"], "训练")
        assert m["status"] == "todo"
        m = env.service.set_milestone_status(m["id"], "in_progress")
        assert m["status"] == "in_progress"
        m = env.service.set_milestone_status(m["id"], "done")
        assert m["status"] == "done"
        assert m["completed_at"]
        # reopen → clear completed_at
        m = env.service.set_milestone_status(m["id"], "todo")
        assert m["completed_at"] is None

    def test_progress_count(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        for i in range(3):
            env.service.add_milestone(p["id"], f"m{i}")
        ms = env.service.milestones.list_by_project(p["id"])
        env.service.set_milestone_status(ms[0]["id"], "done")
        env.service.set_milestone_status(ms[1]["id"], "done")
        prog = env.service.get_project_progress(p["id"])
        assert prog["milestones_done"] == 2
        assert prog["milestones_total"] == 3
        assert prog["has_milestones"] is True

    def test_no_milestone(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        prog = env.service.get_project_progress(p["id"])
        assert prog["has_milestones"] is False
        assert prog["milestones_total"] == 0

    def test_reorder(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        ids = [env.service.add_milestone(p["id"], f"m{i}")["id"]
               for i in range(3)]
        env.service.reorder_milestones(p["id"], [ids[2], ids[0], ids[1]])
        titles = [m["id"] for m in
                  env.service.milestones.list_by_project(p["id"])]
        assert titles == [ids[2], ids[0], ids[1]]

    def test_delete_todo_allowed(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        m = env.service.add_milestone(p["id"], "todo")
        assert env.service.delete_milestone(m["id"]) is True

    def test_completed_milestone_delete_protected(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        m = env.service.add_milestone(p["id"], "done")
        env.service.set_milestone_status(m["id"], "done")
        with pytest.raises(PracticeError):
            env.service.delete_milestone(m["id"])
        assert env.service.milestones.get(m["id"]) is not None

    def test_invalid_status(self, practice_env):
        env = practice_env
        p = env.service.create_project("P", "other")
        with pytest.raises(PracticeError):
            env.service.add_milestone(p["id"], "m", status="bogus")
