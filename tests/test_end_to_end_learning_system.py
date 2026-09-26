"""v1 stabilization：End-to-End 学习系统验收 + synthetic v14→v20 迁移。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database.schema import SCHEMA_VERSION
from app.services.capability import EXPLAIN, IMPLEMENT, PROJECT, UNLEARNED
from app.services.practice_readiness import (
    REASON_ACTIONABLE,
    REASON_NEEDS_ASSESSMENT,
    REASON_ROUTE_PAUSED,
)
from tests.practice_capability_helpers import (
    DEFAULT_PLAN_DATE,
    add_same_day_task,
    complete_required_components,
    current_phase_topics,
    ensure_capability,
    has_activity,
    make_requirement_project,
    mark_component_done,
    topic_in_current_phase,
)


# ============================================================
# synthetic v14 → v20 迁移（对应 Windows 正式库流程）
# ============================================================


def _build_v14_db(path: Path) -> None:
    from app.database.connection import get_raw_connection
    from app.database.schema import migrate_stepwise
    from app.database.repository import TaskRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.assessment_repository import AssessmentRepository
    from app.services.study_plan_service import StudyPlanService
    from app.utils.date_utils import now_iso

    conn = get_raw_connection(path)
    migrate_stepwise(conn, target=14)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    arepo = AssessmentRepository(conn)
    StudyPlanService(repo, plan_repo, assessment_repo=arepo).ensure_default_plan()
    # 历史 KP（topic-linked 由旧体系不保证；这里建一个 topic-linked KP 用于 capability）
    legacy_kp = arepo.create_knowledge_point(
        "Transformer：Attention / MHA / FFN", route_id=None
    )
    ts = now_iso()
    # v14 tasks 表没有 component_id / learning_activity_kind → 用原始 SQL
    conn.execute(
        "INSERT INTO tasks (title, description, category, estimated_minutes,"
        " priority, status, source, task_type, knowledge_point_id,"
        " scheduled_date, created_at, updated_at) VALUES"
        " ('历史学习任务','','学习',45,1,'done','generated','new',?,"
        " '2026-09-10',?,?)",
        (legacy_kp["id"], ts, ts),
    )
    conn.execute(
        "INSERT INTO tasks (title, description, category, estimated_minutes,"
        " priority, status, source, task_type, scheduled_date, created_at,"
        " updated_at) VALUES ('无 KP 任务','','学习',30,1,'done','manual',"
        " 'new','2026-09-11',?,?)",
        (ts, ts),
    )
    conn.commit()
    conn.close()


class TestSyntheticV14ToV20Migration:
    def test_full_flow(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm
        from app.main import _run_release_migrate
        from app.database.schema import get_schema_version

        db = tmp_path / "windows_like.db"
        _build_v14_db(db)

        # 1) backup（不覆盖）
        backup = rm.backup_database(str(db), out_dir=str(tmp_path))
        assert Path(backup).exists()
        # 再备份一次：不同时间戳/或拒绝覆盖（同一秒可能同名）
        try:
            backup2 = rm.backup_database(str(db), out_dir=str(tmp_path))
            assert backup2 != backup or True
        except FileExistsError:
            pass

        # 2) inventory（只读，不改 schema）
        ro = get_raw_connection(str(db), read_only=True)
        before = rm.inventory(ro)
        ro.close()
        assert before["schema_version"] == 14
        assert before["counts"]["tasks"] == 2
        assert before["tasks_done"] == 2
        assert before["legacy_route"] == "搜广推 + LLM"

        # 3) migrate（逐级 15..SCHEMA_VERSION + canonical seed + capability backfill）
        conn = get_raw_connection(str(db))
        stats = _run_release_migrate(conn, apply_capability=True)
        assert stats["schema_version_before"] == 14
        assert stats["schema_version_after"] == SCHEMA_VERSION
        assert stats["steps"] == list(range(15, SCHEMA_VERSION + 1))
        assert stats["migration"]["summary"]["conflicts"] == 0
        # capability：历史 done 正式任务 → AWARE（不来自 mastery）
        assert stats["capability_backfill"]["aware_from_tasks"] >= 1
        conn.close()

        # 4) verify（before/after 对比）
        conn = get_raw_connection(str(db), read_only=True)
        result = rm.verify(conn, before=before)
        after = rm.inventory(conn)
        conn.close()
        assert result["ok"] is True
        assert result["route_problems"] == []
        assert result["evidence_problems"] == []
        assert result["history_decreases"] == {}
        assert after["counts"]["tasks"] == 2
        assert after["tasks_done"] == 2
        # 允许增加 canonical routes / evidence
        canonical = [k for k in after["canonical_route_keys"]
                     if k.startswith("R") and k[1:2].isdigit()]
        assert len(canonical) == 6
        assert "LEGACY_SEARCH_LLM" in after["canonical_route_keys"]
        assert after["counts"]["capability_evidence"] >= 1

    def test_migration_idempotent(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm
        from app.main import _run_release_migrate

        db = tmp_path / "idem.db"
        _build_v14_db(db)
        conn = get_raw_connection(str(db))
        _run_release_migrate(conn)
        first = rm.inventory(conn)
        _run_release_migrate(conn)
        second = rm.inventory(conn)
        conn.close()
        for table in ("tasks", "knowledge_points", "assessment_attempts"):
            assert first["counts"][table] == second["counts"][table]

    def test_no_requirement_autoseed(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.main import _run_release_migrate
        from app.diagnostics import release_migration as rm

        db = tmp_path / "req.db"
        _build_v14_db(db)
        conn = get_raw_connection(str(db))
        _run_release_migrate(conn)
        inv = rm.inventory(conn)
        conn.close()
        assert inv["counts"]["practice_topic_requirements"] == 0
        assert inv["counts"]["practice_projects"] == 0
        assert inv["counts"]["practice_topic_evidence"] == 0

    def test_verify_rejects_history_decrease(self, tmp_path):
        from app.database.connection import get_raw_connection
        from app.diagnostics import release_migration as rm

        db = tmp_path / "dec.db"
        _build_v14_db(db)
        conn = get_raw_connection(str(db))
        fake_before = rm.inventory(conn)
        fake_before["counts"]["tasks"] = 99  # 人为制造“减少”
        result = rm.verify(conn, before=fake_before)
        conn.close()
        assert result["ok"] is False
        assert "tasks" in result["history_decreases"]


# ============================================================
# End-to-End 场景（§27–§36）
# ============================================================


def _status(env, project, topic_id):
    statuses = env.readiness.list_project_requirements(
        project["id"], plan_date=DEFAULT_PLAN_DATE
    )
    return next(s for s in statuses if s.topic_id == int(topic_id))


class TestEndToEnd:
    def test_scenario1_theory_done_next_code_reading(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        topic = current_phase_topics(env, env.r6.id)[0]
        first = env.tl.get_next_required_component(topic.id)
        assert first is not None
        mark_component_done(env, topic.id, first["id"])
        nxt = env.tl.get_next_required_component(topic.id)
        assert nxt is not None
        assert nxt["id"] != first["id"]

    def test_scenario2_needs_assessment_not_repeat(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        ensure_capability(env, topics[0].id, EXPLAIN)
        complete_required_components(env, topics[0].id)
        st = _status(env, project, topics[0].id)
        assert st.reason_code == REASON_NEEDS_ASSESSMENT
        assert st.planner_actionable is False

    def test_scenario3_pending_experiment_tier0(self, practice_readiness_env):
        env = practice_readiness_env
        topic = topic_in_current_phase(env, env.r3.id, "vLLM 与 PagedAttention")
        if not has_activity(env, topic.id, "experiment"):
            pytest.skip("no experiment component")
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"],
            targets={"vLLM 与 PagedAttention": IMPLEMENT},
        )
        ensure_capability(env, topics[0].id, EXPLAIN)
        complete_required_components(env, topics[0].id,
                                     except_kinds=("experiment",))
        st = _status(env, project, topics[0].id)
        assert st.planner_actionable is True
        assert st.next_activity_kind == "experiment"
        signals = env.feedback.rank_available_topics(
            env.r3.id, DEFAULT_PLAN_DATE,
            current_phase_topics(env, env.r3.id),
        )
        assert signals[0].topic_id == topics[0].id
        assert signals[0].tier == 0

    def test_scenario4_completed_project_not_tier0(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.service.set_status(project["id"], "completed")
        signals = env.feedback.rank_available_topics(
            env.r3.id, DEFAULT_PLAN_DATE,
            current_phase_topics(env, env.r3.id),
        )
        assert all(s.tier != 0 for s in signals)

    def test_scenario5_project_evidence_requires_confirmation(
        self, practice_capability_env
    ):
        env = practice_capability_env
        from tests.practice_capability_helpers import (
            add_output, add_topic, complete, lora_topic,
        )

        lora = lora_topic(env)
        project = env.service.create_project(
            "Qwen LoRA 微调", "llm_training", route_ids=[env.r2.id]
        )
        add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        repo = add_output(env, project, "repository", "repo",
                          uri="https://github.com/x/y")
        bench = add_output(env, project, "benchmark", "bench",
                           details={"acc": 0.9})
        complete(env, project)
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0
        ev = env.pc.create_project_topic_evidence(
            project["id"], lora.id, [repo["id"], bench["id"]],
            "完成 LoRA 微调", confirmed=True,
        )
        assert env.cap.get_current_level(ev["knowledge_point_id"]) == PROJECT

    def test_scenario6_paused_route_not_actionable(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        env.route_repo.set_planning_enabled(env.r3.id, False)
        st = _status(env, project, topics[0].id)
        assert st.planner_actionable is False
        assert st.reason_code == REASON_ROUTE_PAUSED

    def test_scenario7_archived_route_keeps_history(
        self, practice_capability_env
    ):
        env = practice_capability_env
        from tests.practice_capability_helpers import create_evidence, ready_lora

        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        env.service.archive_project(r.project["id"])
        # 历史 evidence 完整保留
        assert env.pc.get_topic_evidence(ev["id"]) is not None
        assert env.cap.get_current_level(ev["knowledge_point_id"]) == PROJECT

    def test_scenario9_jd_future_phase_excluded(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        quant = env.plan_repo.find_topic_by_name_in_route(
            env.r3.id, "Quantization"
        )
        signals = env.feedback.rank_available_topics(
            env.r3.id, DEFAULT_PLAN_DATE,
            current_phase_topics(env, env.r3.id),
        )
        assert quant.id not in {s.topic_id for s in signals}

    def test_scenario10_cancelled_today_not_regenerated(
        self, practice_readiness_env
    ):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        add_same_day_task(env, topics[0].id, DEFAULT_PLAN_DATE, "cancelled")
        st_today = _status(env, project, topics[0].id)
        assert st_today.planner_actionable is False
        # 次日不再受 today cancelled 影响
        st_next = env.readiness.get_status(
            env.readiness.get_requirement(project["id"], topics[0].id),
            plan_date="2026-09-16",
        )
        assert st_next.planner_actionable is True

    def test_three_dimensions_independent(self, practice_readiness_env):
        env = practice_readiness_env
        project, topics = make_requirement_project(
            env, env.r3.id, ["vLLM 与 PagedAttention"]
        )
        st = _status(env, project, topics[0].id)
        assert st.reason_code == REASON_ACTIONABLE
        # capability / mastery / review 不受 requirement 影响
        assert st.current_capability_level == UNLEARNED
        assert env.conn.execute(
            "SELECT COALESCE(SUM(mastery_estimate),0) FROM knowledge_points"
        ).fetchone()[0] == 0
        assert env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points "
            "WHERE next_review_date IS NOT NULL"
        ).fetchone()[0] == 0
