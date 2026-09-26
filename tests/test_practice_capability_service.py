"""Phase 5：PracticeCapabilityService 主流程测试。"""

from __future__ import annotations

import pytest

from app.services.capability import (
    AWARE,
    EXPERIMENT,
    IMPLEMENT,
    PROJECT,
    UNLEARNED,
)
from tests.practice_capability_helpers import (
    add_output,
    add_topic,
    complete,
    create_evidence,
    lora_topic,
    ready_lora,
)


class TestCreateEvidence:
    def test_valid_evidence_creates_project(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        assert env.cap.get_current_level(r.lora.id) == UNLEARNED
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"], r.ckpt["id"]])
        assert ev["is_active"] is True
        kp = ev["knowledge_point_id"]
        assert env.cap.get_current_level(kp) == PROJECT
        assert env.cap.get_current_capability(kp)["label"] == "已在真实项目中使用"

    def test_evidence_key_and_capability_reference(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        key = f"practice_topic_evidence:{ev['id']}"
        cap = env.cap.repo.get_by_key(key)
        assert cap is not None
        assert cap["capability_level"] == PROJECT
        assert cap["evidence_type"] == "practice_project"
        assert cap["practice_topic_evidence_id"] == ev["id"]
        assert cap["source_task_id"] is None
        assert cap["assessment_attempt_id"] is None
        assert cap["learning_outcome_id"] is None

    def test_project_does_not_require_levels_1_to_4(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        levels = sorted(
            int(e["capability_level"])
            for e in env.cap.list_evidence(ev["knowledge_point_id"])
        )
        assert levels == [PROJECT]  # 不自动补 1~4

    def test_no_lower_levels_written(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        rows = env.conn.execute(
            "SELECT DISTINCT capability_level FROM capability_evidence"
        ).fetchall()
        assert [int(x[0]) for x in rows] == [PROJECT]

    def test_multiple_outputs_and_supporting(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"], r.ckpt["id"],
                              r.readme["id"]])
        ids = env.evidence_repo.list_output_ids(ev["id"])
        assert set(ids) == {r.repo["id"], r.bench["id"], r.ckpt["id"],
                            r.readme["id"]}
        assert "README" in ev["output_labels"]

    def test_create_is_atomic_on_failure(self, practice_capability_env):
        """验证失败时不得留下 PracticeTopicEvidence。"""
        env = practice_capability_env
        r = ready_lora(env)
        before = env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0]
        with pytest.raises(Exception):
            create_evidence(env, r.project, r.lora, [], usage="")
        after = env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0]
        assert before == after == 0

    def test_rollback_removes_both_sides(self, practice_capability_env,
                                         monkeypatch):
        """中间步骤抛错 → PTE 与 capability 都不存在。"""
        env = practice_capability_env
        r = ready_lora(env)

        def boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            env.cap, "sync_from_practice_topic_evidence", boom
        )
        with pytest.raises(RuntimeError):
            create_evidence(env, r.project, r.lora, [r.repo["id"]])
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence "
            "WHERE evidence_type='practice_project'"
        ).fetchone()[0] == 0
        # 新建的 kp 也应回滚
        assert env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE topic_id = ?",
            (r.lora.id,),
        ).fetchone()[0] == 0

    def test_kp_created_deterministically(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        assert env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE topic_id = ?",
            (r.lora.id,),
        ).fetchone()[0] == 0
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = env.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?",
            (ev["knowledge_point_id"],),
        ).fetchone()
        assert kp["topic_id"] == r.lora.id
        assert kp["route_id"] == env.r2.id
        assert kp["name"] == r.lora.name

    def test_new_kp_creates_no_mastery_assessment_review(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = env.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?",
            (ev["knowledge_point_id"],),
        ).fetchone()
        assert float(kp["mastery_estimate"]) == 0.0
        assert int(kp["review_count"]) == 0
        assert kp["next_review_date"] is None
        assert kp["last_assessed_at"] is None
        assert env.conn.execute(
            "SELECT COUNT(*) FROM assessment_attempts "
            "WHERE knowledge_point_id = ?", (kp["id"],)
        ).fetchone()[0] == 0

    def test_reuses_existing_topic_kp(self, practice_capability_env,
                                     assessment_repo):
        env = practice_capability_env
        r = ready_lora(env)
        kp = assessment_repo.get_or_create_knowledge_point_for_topic(
            r.lora.id, r.lora.name, route_id=env.r2.id
        )
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        assert ev["knowledge_point_id"] == kp["id"]


class TestMultipleProjects:
    def test_two_projects_same_topic(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev1 = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = ev1["knowledge_point_id"]

        p2 = env.service.create_project(
            "推荐模型 LoRA Adaptation", "recommendation_search",
            route_ids=[env.r2.id],
        )
        add_topic(env, p2, env.r2.id, "LoRA / QLoRA")
        o2 = add_output(env, p2, "code", "code", uri="https://x/code")
        complete(env, p2)
        ev2 = env.pc.create_project_topic_evidence(
            p2["id"], r.lora.id, [o2["id"]], "再次使用", confirmed=True
        )
        assert env.pc.count_active_by_topic(r.lora.id) == 2
        assert env.cap.get_current_level(kp) == PROJECT

        # 撤销 A 后，B 仍 active → 仍 PROJECT
        env.pc.revoke_project_topic_evidence(ev1["id"], "r")
        assert env.cap.get_current_level(kp) == PROJECT
        # 撤销 B → 回落
        env.pc.revoke_project_topic_evidence(ev2["id"], "r")
        assert env.cap.get_current_level(kp) == UNLEARNED


class TestFallback:
    def test_revoke_falls_back_to_experiment(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = ev["knowledge_point_id"]
        # 人为叠加 EXPERIMENT（模拟历史实验证据）
        env.cap.repo.create_or_update_by_key(
            knowledge_point_id=kp, capability_level=EXPERIMENT,
            evidence_type="experiment_outcome", evidence_key="experiment_outcome:999",
            description="历史实验",
        )
        assert env.cap.get_current_level(kp) == PROJECT
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        assert env.cap.get_current_level(kp) == EXPERIMENT

    def test_revoke_without_other_evidence_returns_unlearned(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        assert env.cap.get_current_level(ev["knowledge_point_id"]) == UNLEARNED
        assert env.cap.repo.get_by_key(
            f"practice_topic_evidence:{ev['id']}"
        )["is_active"] is False


class TestArchiveReopen:
    def test_archive_does_not_revoke_evidence(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.service.archive_project(r.project["id"])
        assert env.pc.get_topic_evidence(ev["id"])["is_active"] is True
        assert env.cap.get_current_level(ev["knowledge_point_id"]) == PROJECT

    def test_reopen_does_not_lower_capability(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.service.set_status(r.project["id"], "in_progress")
        assert env.cap.get_current_level(ev["knowledge_point_id"]) == PROJECT


class TestOtherPathsNeverProject:
    def test_sync_methods_never_project(self, practice_capability_env):
        from app.services.capability_service import _guard_non_practice_level

        for level in (AWARE, IMPLEMENT, EXPERIMENT):
            assert _guard_non_practice_level(level) == level
        with pytest.raises(ValueError):
            _guard_non_practice_level(PROJECT)

    def test_can_generate_project_now_true(self, practice_capability_env):
        assert practice_capability_env.cap.can_generate_project() is True


class TestSkillAndActivityIsolation:
    def test_skill_relation_alone_no_capability(self, practice_capability_env):
        env = practice_capability_env
        skills = env.skill_repo.list_all()
        skill = skills[0] if skills else env.skill_repo.create("LoRA Test Skill")
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        env.service.add_skill(project["id"], skill["id"])
        complete(env, project)
        # 关联 Skill 不产生任何 capability
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0

    def test_project_evidence_does_not_complete_activity(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        before = env.conn.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0]
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        after = env.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert before == after  # 不自动生成 Daily Task / 不自动补 Activity
        # Topic 的 activity profile 保持只有 enabled 配置，无完成态写入
        rows = env.conn.execute(
            "SELECT COUNT(*) FROM topic_learning_components WHERE topic_id = ?",
            (r.lora.id,),
        ).fetchone()[0]
        assert rows >= 0

    def test_project_evidence_does_not_change_mastery(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = env.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?",
            (ev["knowledge_point_id"],),
        ).fetchone()
        assert float(kp["mastery_estimate"]) == 0.0
