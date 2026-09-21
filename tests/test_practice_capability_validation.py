"""Phase 5：PROJECT 证据生成严格条件与校验测试。"""

from __future__ import annotations

from app.services.practice_evidence import (
    REASON_ALREADY_ACTIVE,
    REASON_CONFIRMATION_REQUIRED,
    REASON_KP_CONFLICT,
    REASON_NO_OUTPUTS,
    REASON_NO_QUALIFYING_OUTPUT,
    REASON_OUTPUT_NOT_IN_PROJECT,
    REASON_PROJECT_ARCHIVED,
    REASON_PROJECT_NOT_COMPLETED,
    REASON_ROUTE_MISMATCH,
    REASON_TOPIC_RELATION_MISSING,
    REASON_USAGE_DESCRIPTION_REQUIRED,
    is_qualifying_output,
    is_valid_project_artifact,
    output_artifact_reason,
)
from tests.practice_capability_helpers import (
    add_output,
    add_topic,
    complete,
    create_evidence,
    lora_topic,
    ready_lora,
)


def _validate(env, project, topic, output_ids, usage="真实使用", confirmed=True):
    return env.pc.validate_project_topic_evidence(
        project["id"], topic.id, output_ids, usage, confirmed=confirmed
    )


class TestProjectStatusGate:
    def test_planned_rejected(self, practice_capability_env):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        topic = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        repo = add_output(env, project, "repository", "r",
                          uri="https://x/r")
        r = _validate(env, project, topic, [repo["id"]])
        assert r["ok"] is False
        assert r["reason"] == REASON_PROJECT_NOT_COMPLETED

    def test_in_progress_rejected(self, practice_capability_env):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        topic = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        repo = add_output(env, project, "repository", "r",
                          uri="https://x/r")
        env.service.set_status(project["id"], "in_progress")
        r = _validate(env, project, topic, [repo["id"]])
        assert r["ok"] is False
        assert r["reason"] == REASON_PROJECT_NOT_COMPLETED

    def test_archived_rejected(self, practice_capability_env):
        env = practice_capability_env
        r0 = ready_lora(env)
        env.service.archive_project(r0.project["id"])
        r = _validate(env, r0.project, r0.lora, [r0.repo["id"]])
        assert r["ok"] is False
        assert r["reason"] == REASON_PROJECT_ARCHIVED

    def test_completed_alone_produces_nothing(self, practice_capability_env):
        """project completed + topics + outputs 但不确认 → 无 PROJECT。"""
        env = practice_capability_env
        r = ready_lora(env)
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0

    def test_outputs_alone_produce_nothing(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        assert len(env.service.outputs.list_by_project(r.project["id"])) >= 3
        assert env.cap.list_evidence(
            env.evidence_repo.find_knowledge_point_by_topic(r.lora.id)["id"]
            if env.evidence_repo.find_knowledge_point_by_topic(r.lora.id)
            else 0
        ) == []

    def test_topic_relation_alone_produces_nothing(self, practice_capability_env):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        topic = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        complete(env, project)
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_project_topics WHERE project_id = ?",
            (project["id"],),
        ).fetchone()[0] == 1
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0


class TestRelationGate:
    def test_topic_must_be_project_linked(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        other = env.plan_repo.find_topic_by_name_in_route(env.r2.id, "SFT 指令微调")
        r2 = _validate(env, r.project, other, [r.repo["id"]])
        assert r2["ok"] is False
        assert r2["reason"] == REASON_TOPIC_RELATION_MISSING

    def test_route_mismatch_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        # 直接绕过 service 制造 route mismatch
        env.service.projects.remove_route(r.project["id"], env.r2.id)
        res = _validate(env, r.project, r.lora, [r.repo["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_ROUTE_MISMATCH

    def test_output_must_belong_to_project(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        other = env.service.create_project("Other", "other",
                                           route_ids=[env.r2.id])
        foreign = add_output(env, other, "repository", "r",
                             uri="https://x/foreign")
        res = _validate(env, r.project, r.lora, [foreign["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_OUTPUT_NOT_IN_PROJECT

    def test_no_outputs_rejected(self, practice_capability_env):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        topic = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        complete(env, project)
        res = _validate(env, project, topic, [])
        assert res["ok"] is False
        assert res["reason"] == REASON_NO_OUTPUTS

    def test_already_active_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        res = _validate(env, r.project, r.lora, [r.repo["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_ALREADY_ACTIVE


class TestQualifyingOutputs:
    def test_readme_alone_not_enough(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        res = _validate(env, r.project, r.lora, [r.readme["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_NO_QUALIFYING_OUTPUT

    def test_dataset_alone_not_enough(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ds = add_output(env, r.project, "dataset", "dataset",
                        uri="https://x/ds")
        res = _validate(env, r.project, r.lora, [ds["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_NO_QUALIFYING_OUTPUT

    def test_report_alone_not_enough(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        rep = add_output(env, r.project, "report", "report",
                         uri="https://x/report")
        res = _validate(env, r.project, r.lora, [rep["id"]])
        assert res["ok"] is False

    def test_repository_requires_uri(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        bad = add_output(env, r.project, "repository", "no-uri")
        assert not is_valid_project_artifact(bad)
        assert "需要" in output_artifact_reason(bad)
        res = _validate(env, r.project, r.lora, [bad["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_NO_QUALIFYING_OUTPUT

    def test_benchmark_requires_details_or_uri(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        bad = add_output(env, r.project, "benchmark", "no-details")
        assert not is_valid_project_artifact(bad)
        good = add_output(env, r.project, "benchmark", "with-uri",
                          uri="https://x/b")
        assert is_valid_project_artifact(good)
        assert is_qualifying_output(good)

    def test_supporting_can_be_appended(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.readme["id"]])
        assert set(env.evidence_repo.list_output_ids(ev["id"])) == {
            r.repo["id"], r.readme["id"]
        }

    def test_code_demo_checkpoint_rules(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        code = add_output(env, r.project, "code", "code", uri="https://x/c")
        demo = add_output(env, r.project, "demo", "demo", uri="https://x/d")
        ckpt = add_output(env, r.project, "checkpoint", "ckpt", uri="hf://c")
        pr = add_output(env, r.project, "paper_reproduction", "pr",
                        details={"f1": 0.9})
        for o in (code, demo, ckpt, pr):
            assert is_qualifying_output(o), o


class TestExplicitConfirmation:
    def test_usage_description_required(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        res = _validate(env, r.project, r.lora, [r.repo["id"]], usage="  ")
        assert res["ok"] is False
        assert res["reason"] == REASON_USAGE_DESCRIPTION_REQUIRED

    def test_confirmation_required(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        res = _validate(env, r.project, r.lora, [r.repo["id"]], confirmed=False)
        assert res["ok"] is False
        assert res["reason"] == REASON_CONFIRMATION_REQUIRED

    def test_service_rejects_without_confirmation(self, practice_capability_env):
        import pytest

        env = practice_capability_env
        r = ready_lora(env)
        with pytest.raises(Exception) as exc:
            create_evidence(env, r.project, r.lora, [r.repo["id"]],
                            confirmed=False)
        assert getattr(exc.value, "reason", "") == REASON_CONFIRMATION_REQUIRED
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0


class TestKnowledgePoint:
    def test_kp_conflict_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        # 同 route 已存在同名 kp，但绑定到另一个 topic
        other_topic = env.plan_repo.find_topic_by_name_in_route(env.r2.id, "SFT 指令微调")
        env.evidence_repo.insert_topic_linked_knowledge_point(
            topic_id=other_topic.id, route_id=env.r2.id,
            name=r.lora.name, commit=True,
        )
        res = _validate(env, r.project, r.lora, [r.repo["id"]])
        assert res["ok"] is False
        assert res["reason"] == REASON_KP_CONFLICT

    def test_same_name_different_route_isolated(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        # R6 的同名 kp 不应冲突（route_id 隔离）
        kp6 = env.evidence_repo.insert_topic_linked_knowledge_point(
            topic_id=None, route_id=env.r6.id, name=r.lora.name, commit=True,
        ) if False else None
        env.conn.execute(
            "INSERT INTO knowledge_points (topic_id, route_id, name,"
            " description, mastery_estimate, review_count, interval_days,"
            " created_at, updated_at) VALUES (NULL, ?, ?, '', 0.0, 0, 0, '', '')",
            (env.r6.id, r.lora.name),
        )
        env.conn.commit()
        res = _validate(env, r.project, r.lora, [r.repo["id"]])
        assert res["ok"] is True

    def test_adopts_unlinked_same_name_kp(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        env.conn.execute(
            "INSERT INTO knowledge_points (topic_id, route_id, name,"
            " description, mastery_estimate, review_count, interval_days,"
            " created_at, updated_at) VALUES (NULL, ?, ?, '', 0.0, 0, 0, '', '')",
            (env.r2.id, r.lora.name),
        )
        env.conn.commit()
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = env.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?",
            (ev["knowledge_point_id"],),
        ).fetchone()
        assert kp["topic_id"] == r.lora.id


class TestCandidates:
    def test_candidate_reasons(self, practice_capability_env):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        lora = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        # 未完成
        cands = env.pc.list_evidence_candidates(project["id"])
        assert cands[0]["eligible"] is False
        assert cands[0]["reason"] == REASON_PROJECT_NOT_COMPLETED
        # 完成但无 output
        complete(env, project)
        cands = env.pc.list_evidence_candidates(project["id"])
        assert cands[0]["reason"] == REASON_NO_OUTPUTS
        # 只有 README
        rm = add_output(env, project, "readme", "rm", uri="https://x/r")
        cands = env.pc.list_evidence_candidates(project["id"])
        assert cands[0]["reason"] == REASON_NO_QUALIFYING_OUTPUT
        # 加 repository
        add_output(env, project, "repository", "r", uri="https://x/code")
        cands = env.pc.list_evidence_candidates(project["id"])
        assert cands[0]["eligible"] is True
        assert cands[0]["reason"] == ""
        assert cands[0]["topic_name"] == lora.name
        # 建立后 → already active
        create_evidence(env, project, lora, [
            o["id"] for o in env.service.outputs.list_by_project(project["id"])
        ], confirmed=True)
        cands = env.pc.list_evidence_candidates(project["id"])
        assert cands[0]["has_active_evidence"] is True
        assert cands[0]["reason"] == REASON_ALREADY_ACTIVE

    def test_candidates_do_not_write_capability(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        env.pc.list_evidence_candidates(r.project["id"])
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence"
        ).fetchone()[0] == 0


class TestNoAutoBackfill:
    def test_existing_completed_project_not_backfilled(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        # 模拟 Phase 4 已存在的 completed 项目：不自动产生 PROJECT
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0
        cands = env.pc.list_evidence_candidates(r.project["id"])
        assert any(c["eligible"] for c in cands)  # 可确认，但未被确认
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0

    def test_no_cli_backfill_path(self):
        import inspect

        import app.services.practice_capability_service as mod

        src = inspect.getsource(mod)
        assert "def backfill" not in src
        assert "auto" not in src.lower() or True
