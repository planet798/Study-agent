"""Phase 5：撤销 / 回退 / 保护规则测试。"""

from __future__ import annotations

import pytest

from app.services.capability import PROJECT, UNLEARNED
from app.services.practice_project_service import PracticeError
from tests.practice_capability_helpers import (
    add_output,
    add_topic,
    complete,
    create_evidence,
    ready_lora,
)


class TestRevoke:
    def test_revoke_flags_and_history(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        out = env.pc.revoke_project_topic_evidence(ev["id"], "改写需求")
        assert out["is_active"] is False
        assert out["revoked_at"]
        assert out["revocation_reason"] == "改写需求"
        # 历史行保留
        rows = env.evidence_repo.list_by_project(r.project["id"],
                                                 active_only=False)
        assert len(rows) == 1
        # capability evidence 同步 revoke
        cap = env.cap.repo.get_by_key(f"practice_topic_evidence:{ev['id']}")
        assert cap["is_active"] is False
        assert cap["revoked_at"]

    def test_second_project_keeps_project_level(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev1 = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = ev1["knowledge_point_id"]
        p2 = env.service.create_project("B", "other", route_ids=[env.r2.id])
        add_topic(env, p2, env.r2.id, "LoRA / QLoRA")
        o2 = add_output(env, p2, "code", "c", uri="https://x/c")
        complete(env, p2)
        ev2 = env.pc.create_project_topic_evidence(
            p2["id"], r.lora.id, [o2["id"]], "second", confirmed=True
        )
        env.pc.revoke_project_topic_evidence(ev1["id"], "r")
        assert env.cap.get_current_level(kp) == PROJECT
        env.pc.revoke_project_topic_evidence(ev2["id"], "r")
        assert env.cap.get_current_level(kp) == UNLEARNED

    def test_revoke_unknown_raises(self, practice_capability_env):
        with pytest.raises(Exception):
            practice_capability_env.pc.revoke_project_topic_evidence(99999, "x")

    def test_revoke_by_capability_evidence_routes_practice(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        cap = env.cap.repo.get_by_key(f"practice_topic_evidence:{ev['id']}")
        env.pc.revoke_by_capability_evidence(cap["id"], "via generic dialog")
        # 原始事实也必须被撤销
        assert env.pc.get_topic_evidence(ev["id"])["is_active"] is False

    def test_revoke_by_capability_evidence_non_practice(self, practice_capability_env):
        env = practice_capability_env
        cap = env.cap.repo.create_or_update_by_key(
            knowledge_point_id=1, capability_level=1,
            evidence_type="learning_activity", evidence_key="task:123",
        )
        env.pc.revoke_by_capability_evidence(cap["id"], "normal")
        assert env.cap.repo.get(cap["id"])["is_active"] is False


class TestOutputProtection:
    def test_referenced_output_delete_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        with pytest.raises(PracticeError):
            env.service.delete_output(r.repo["id"])
        assert env.service.outputs.get(r.repo["id"]) is not None

    def test_referenced_output_edit_locked(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        for fields in (
            {"output_type": "readme"},
            {"uri": "https://evil/x"},
            {"details": {"x": 1}},
            {"description": "changed"},
        ):
            with pytest.raises(PracticeError):
                env.service.update_output(r.repo["id"], **fields)

    def test_title_edit_allowed(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        out = env.service.update_output(r.repo["id"], title="新标题")
        assert out["title"] == "新标题"

    def test_after_revoke_output_still_protected(self, practice_capability_env):
        """Phase 5.1：历史完整性——撤销后仍冻结关键字段并禁止删除。"""
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.update_output(r.repo["id"], uri="https://x/new")
        with pytest.raises(PracticeError):
            env.service.delete_output(r.repo["id"])
        assert env.service.outputs.get(r.repo["id"]) is not None

    def test_unreferenced_output_still_deletable(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        extra = add_output(env, r.project, "readme", "extra",
                           uri="https://x/extra")
        assert env.service.delete_output(extra["id"]) is True


class TestRelationAndProjectProtection:
    def test_remove_topic_with_active_evidence_rejected(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        with pytest.raises(PracticeError):
            env.service.remove_topic(r.project["id"], r.lora.id)
        assert r.lora.id in env.service.projects.list_topic_ids(r.project["id"])

    def test_set_topics_dropping_evidenced_topic_rejected(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        with pytest.raises(PracticeError):
            env.service.set_topics(r.project["id"], [])

    def test_remove_topic_still_blocked_after_revoke(self, practice_capability_env):
        """Phase 5.1：历史 evidence 存在时，Topic 关联仍不可解除。"""
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.remove_topic(r.project["id"], r.lora.id)

    def test_project_with_evidence_history_cannot_be_deleted(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        # 即使已撤销，历史证据仍禁止物理删除
        with pytest.raises(PracticeError):
            env.service.delete_project(r.project["id"])

    def test_project_with_evidence_cannot_be_deleted_while_active(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        with pytest.raises(PracticeError):
            env.service.delete_project(r.project["id"])
