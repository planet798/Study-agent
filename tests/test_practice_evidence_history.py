"""Phase 5.1：Practice Evidence Historical Integrity。

原则：revoke 的语义是“不再参与 current capability”，
不是“历史上从未存在”。因此一旦 Output / Project-Topic 曾被任意
PracticeTopicEvidence 使用，历史关联必须永久保留。
"""

from __future__ import annotations

import inspect

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


class TestOutputDeleteRule:
    def test_active_reference_delete_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        with pytest.raises(PracticeError):
            env.service.delete_output(r.repo["id"])

    def test_revoked_reference_delete_rejected(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.delete_output(r.repo["id"])
        assert env.service.outputs.get(r.repo["id"]) is not None

    def test_never_used_output_deletable(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        # readme 未被选中 → 从未用于 evidence
        assert env.service.delete_output(r.readme["id"]) is True

    def test_deleted_output_never_referenced_stays_deletable(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        extra = add_output(env, r.project, "readme", "extra",
                           uri="https://x/extra")
        assert env.service.delete_output(extra["id"]) is True


class TestOutputEditRule:
    LOCKED = (
        {"output_type": "readme"},
        {"uri": "https://evil/x"},
        {"details": {"x": 1}},
        {"description": "changed"},
    )

    def test_active_reference_key_fields_locked(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        for fields in self.LOCKED:
            with pytest.raises(PracticeError):
                env.service.update_output(r.repo["id"], **fields)

    def test_revoked_reference_key_fields_locked(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        for fields in self.LOCKED:
            with pytest.raises(PracticeError):
                env.service.update_output(r.repo["id"], **fields)

    def test_title_editable_after_evidence(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        out = env.service.update_output(r.repo["id"], title="新标题")
        assert out["title"] == "新标题"
        # revoked 后仍可改 title
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        out2 = env.service.update_output(r.repo["id"], title="再改标题")
        assert out2["title"] == "再改标题"
        # 但关键字段仍冻结
        with pytest.raises(PracticeError):
            env.service.update_output(r.repo["id"], uri="https://x/new")

    def test_never_used_output_editable(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        out = env.service.update_output(r.readme["id"], uri="https://x/new",
                                        output_type="report")
        assert out["uri"] == "https://x/new"
        assert out["output_type"] == "report"


class TestHistoryPreserved:
    def test_relation_preserved_after_revoke(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        ids = env.evidence_repo.list_output_ids(ev["id"])
        assert set(ids) == {r.repo["id"], r.bench["id"]}

    def test_revoked_timeline_shows_outputs_and_reason(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"], r.ckpt["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "证据关联错误")
        info = env.pc.get_topic_evidence(ev["id"])
        assert info["is_active"] is False
        assert info["revocation_reason"] == "证据关联错误"
        assert info["revoked_at"]
        assert info["project_name"] == r.project["name"]
        assert info["topic_name"] == r.lora.name
        assert set(info["output_labels"]) == {
            "Repository", "Benchmark", "Checkpoint"
        }
        assert info["usage_description"]

    def test_timeline_available_for_revoked_kp(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        evidence = env.cap.list_evidence(ev["knowledge_point_id"])
        assert len(evidence) == 1
        assert evidence[0]["is_active"] is False
        assert evidence[0]["practice_topic_evidence_id"] == ev["id"]


class TestTopicAndRouteHistory:
    def test_historical_evidence_blocks_topic_removal(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.remove_topic(r.project["id"], r.lora.id)
        with pytest.raises(PracticeError):
            env.service.set_topics(r.project["id"], [])

    def test_route_relation_cannot_be_dismantled(self, practice_capability_env):
        """revoke → remove topic → remove route 的拆结构路径必须失败。"""
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.remove_topic(r.project["id"], r.lora.id)
        # Topic 仍在 → 其相应 Route 不能移除
        with pytest.raises(PracticeError):
            env.service.remove_route(r.project["id"], env.r2.id)
        with pytest.raises(PracticeError):
            env.service.set_routes(r.project["id"], [env.r1.id, env.r3.id])
        assert r.lora.id in env.service.projects.list_topic_ids(r.project["id"])
        assert env.r2.id in env.service.projects.list_route_ids(r.project["id"])

    def test_project_delete_blocked_by_history(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        with pytest.raises(PracticeError):
            env.service.delete_project(r.project["id"])


class TestArchiveReopenHistory:
    def test_archive_preserves_history(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        env.service.archive_project(r.project["id"])
        info = env.pc.get_topic_evidence(ev["id"])
        assert info["is_active"] is False
        assert set(info["output_labels"]) == {"Repository", "Benchmark"}
        assert env.evidence_repo.list_output_ids(ev["id"])

    def test_reopen_preserves_history(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.service.archive_project(r.project["id"])
        env.service.restore_project(r.project["id"])
        assert env.evidence_repo.list_output_ids(ev["id"]) == [r.repo["id"]]


class TestCapabilityNotAffectedByHistory:
    def test_revoked_not_counted(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = ev["knowledge_point_id"]
        assert env.cap.get_current_level(kp) == PROJECT
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        assert env.cap.get_current_level(kp) == UNLEARNED
        assert env.cap.get_current_capability(kp)["evidence_count"] == 0

    def test_other_project_active_keeps_level5(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        ev1 = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        kp = ev1["knowledge_point_id"]
        p2 = env.service.create_project("B", "other", route_ids=[env.r2.id])
        add_topic(env, p2, env.r2.id, "LoRA / QLoRA")
        o2 = add_output(env, p2, "code", "c", uri="https://x/c")
        complete(env, p2)
        env.pc.create_project_topic_evidence(
            p2["id"], r.lora.id, [o2["id"]], "second", confirmed=True
        )
        env.pc.revoke_project_topic_evidence(ev1["id"], "r")
        assert env.cap.get_current_level(kp) == PROJECT


class TestNoRegression:
    def test_project_generation_rules_intact(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        # README alone 仍不够
        res = env.pc.validate_project_topic_evidence(
            r.project["id"], r.lora.id, [r.readme["id"]], "x", confirmed=True
        )
        assert res["ok"] is False
        assert res["reason"] == "no_qualifying_output"
        # 未确认仍拒绝
        res2 = env.pc.validate_project_topic_evidence(
            r.project["id"], r.lora.id, [r.repo["id"]], "x", confirmed=False
        )
        assert res2["reason"] == "confirmation_required"

    def test_planner_scheduler_review_untouched(self):
        # Phase 6：Scheduler / StudyPlanService 不读 Practice；
        # DailyPlannerService 只通过 feedback_service 间接使用。
        import app.services.daily_planner_service as dps
        import app.services.route_scheduler as sched
        import app.services.study_plan_service as sps

        for mod in (sched, sps):
            assert "practice" not in inspect.getsource(mod).lower()
        planner_src = inspect.getsource(dps)
        assert "PracticeReadinessService" not in planner_src
        assert "feedback_service" in planner_src

    def test_review_interval_unchanged(self):
        from app.services.review_service import ReviewService

        assert ReviewService.next_interval(0, "good") == 3

    def test_db_still_has_cascade(self, practice_capability_env):
        """Phase 5.1 不做危险表重建；DB 层 CASCADE 仍在，靠 Service invariant 保护。"""
        env = practice_capability_env
        sql = env.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE name='practice_topic_evidence_outputs'"
        ).fetchone()[0]
        normalized = " ".join(sql.split()).upper()
        assert "ON DELETE CASCADE" in normalized


class TestRevokedTimelineUI:
    def test_revoked_evidence_rendered_in_timeline(
        self, practice_capability_env, qtbot
    ):
        from app.ui.capability_dialog import CapabilityEvidenceDialog

        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora,
                             [r.repo["id"], r.bench["id"], r.ckpt["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "证据关联错误")
        dlg = CapabilityEvidenceDialog(
            r.lora.name, ev["knowledge_point_id"], env.cap,
            practice_capability_service=env.pc,
        )
        qtbot.addWidget(dlg)
        labels = [lbl.text() for lbl in dlg.findChildren(type(dlg.current_label))]
        joined = "\n".join(labels)
        assert "PROJECT（已撤销）" in joined
        assert "证据关联错误" in joined
        assert "撤销时间" in joined
        assert r.project["name"] in joined
        assert "Repository · Benchmark · Checkpoint" in joined
        assert "撤销证据" not in [
            b.text() for b in dlg.findChildren(
                __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton
            )
        ]

    def test_project_detail_shows_revoked_hint(
        self, practice_capability_env, qtbot
    ):
        from app.ui.practice_page import PracticeProjectDetailDialog

        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        dlg = PracticeProjectDetailDialog(
            r.project["id"], env.service, env.route_repo, env.skill_repo,
            env.plan_repo, env.pc,
        )
        qtbot.addWidget(dlg)
        labels = [lbl.text() for lbl in dlg.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel
        )]
        assert any("曾有已撤销证据" in t for t in labels)
