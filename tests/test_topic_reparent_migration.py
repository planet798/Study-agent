"""MOVE（reparent）迁移的无损性测试（Phase 1 核心）。

验证：旧 topic / task / kp / assessment / review 的 id 与 mastery 完全保留，
只改变 topic.phase_id、task.route_id、kp.route_id。
"""

from __future__ import annotations

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.services import canonical_routes as C
from app.services.canonical_route_service import CanonicalRouteService
from app.services.route_migration_service import (
    ACTION_MOVE,
    MIGRATION_META_KEY,
    RouteMigrationService,
)


def _seed_history(env, topic_name: str, *, mastery: float = 0.82,
                  task_status: str = "done", extra_task_status: str = "active"):
    """为 legacy topic 造出 task / kp / assessment / review 历史，返回 id 字典。"""
    topic = env.plan_repo.find_topic_by_name_in_route(
        env.legacy_route.id, topic_name
    )
    assert topic is not None, topic_name
    task = env.repo.create(
        title=f"历史任务 {topic_name}", scheduled_date="2026-09-05",
        source="generated", task_type="new", topic_id=topic.id,
        route_id=env.legacy_route.id,
    )
    if task_status != "active":
        env.conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (task_status, task.id)
        )
        env.conn.commit()
    # 第二条（cancelled / manual）用于验证状态不被迁移破坏
    extra = env.repo.create(
        title=f"手动任务 {topic_name}", scheduled_date="2026-09-05",
        source="manual", task_type="manual", topic_id=topic.id,
        route_id=env.legacy_route.id,
    )
    if extra_task_status != "active":
        env.conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (extra_task_status, extra.id),
        )
        env.conn.commit()
    a_repo = AssessmentRepository(env.conn)
    kp = a_repo.get_or_create_knowledge_point_for_topic(
        topic.id, topic.name, topic.description, route_id=env.legacy_route.id
    )
    attempt = a_repo.create_attempt(kp["id"], "[]", task_id=task.id)
    a_repo.update_knowledge_point(
        kp["id"], mastery_estimate=mastery,
        last_assessed_at="2026-09-05T10:00:00", review_count=3,
        next_review_date="2026-09-10", interval_days=5,
    )
    env.conn.execute(
        "INSERT INTO review_schedule "
        "(knowledge_point_id, scheduled_date, interval_days, status, task_id,"
        " source_attempt_id, created_at) "
        "VALUES (?, '2026-09-10', 5, 'pending', ?, ?, '2026-09-05T10:00:00')",
        (kp["id"], task.id, attempt["id"]),
    )
    env.conn.commit()
    return {
        "topic_id": topic.id,
        "task_id": task.id,
        "kp_id": kp["id"],
        "attempt_id": attempt["id"],
    }


class TestMovePreservesHistory:
    def test_clear_move_preserves_all_ids_and_mastery(self, six_route_env):
        env = six_route_env
        ids = _seed_history(env, "Function Calling / Tool Calling", mastery=0.82)
        review_row = env.conn.execute(
            "SELECT id FROM review_schedule WHERE knowledge_point_id = ?",
            (ids["kp_id"],),
        ).fetchone()
        review_id = review_row[0]

        res = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        assert res["migration"]["applied"] is True
        assert res["migration"]["summary"]["move_migratable"] == 44

        # id 全部保留
        assert env.plan_repo.get_topic(ids["topic_id"]) is not None
        assert env.repo.get(ids["task_id"]) is not None
        a_repo = AssessmentRepository(env.conn)
        assert a_repo.get_knowledge_point(ids["kp_id"]) is not None
        assert a_repo.get_attempt(ids["attempt_id"]) is not None
        assert env.conn.execute(
            "SELECT id FROM review_schedule WHERE id = ?", (review_id,)
        ).fetchone() is not None

        # mastery / 复习字段不变
        kp = a_repo.get_knowledge_point(ids["kp_id"])
        assert float(kp["mastery_estimate"]) == pytest.approx(0.82)
        assert kp["review_count"] == 3
        assert kp["next_review_date"] == "2026-09-10"
        assert kp["interval_days"] == 5

        # topic 已 reparent 到 R4；task / kp 的 route 已同步
        r4 = env.route_repo.get_by_key("R4_AI_AGENT")
        topic = env.plan_repo.get_topic(ids["topic_id"])
        phase = env.plan_repo.get_phase(topic.phase_id)
        plan = env.plan_repo.get_plan(phase.plan_id)
        assert plan.route_id == r4.id
        assert phase.name == "Agent 基础协议"
        task = env.repo.get(ids["task_id"])
        assert task.route_id == r4.id
        assert task.topic_id == ids["topic_id"]
        kp = a_repo.get_knowledge_point(ids["kp_id"])
        assert kp["route_id"] == r4.id
        assert kp["topic_id"] == ids["topic_id"]

    def test_other_task_statuses_preserved(self, six_route_env):
        env = six_route_env
        _seed_history(env, "ReAct 推理与行动", task_status="done",
                      extra_task_status="cancelled")
        tasks_before = {
            t.id: (t.status, t.source, t.task_type)
            for t in env.repo.list_by_topic_id(
                env.plan_repo.find_topic_by_name_in_route(
                    env.legacy_route.id, "ReAct 推理与行动").id)
        }
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        for task_id, (status, source, task_type) in tasks_before.items():
            t = env.repo.get(task_id)
            assert t.status == status
            assert t.source == source
            assert t.task_type == task_type

    def test_planner_decision_not_migrated(self, six_route_env):
        env = six_route_env
        env.conn.execute(
            "INSERT INTO planner_decisions "
            "(date, current_phase_id, input_context, ai_response, accepted_tasks,"
            " source, created_at, route_id) "
            "VALUES ('2026-09-05', NULL, '{}', '{}', '[]', 'ai',"
            " '2026-09-05T10:00:00', ?)",
            (env.legacy_route.id,),
        )
        env.conn.commit()
        before = [dict(r) for r in env.conn.execute(
            "SELECT id, route_id FROM planner_decisions"
        ).fetchall()]
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        after = [dict(r) for r in env.conn.execute(
            "SELECT id, route_id FROM planner_decisions"
        ).fetchall()]
        assert before == after
        assert after[0]["route_id"] == env.legacy_route.id

    def test_learning_outcome_linked_move_topic_survives(self, six_route_env):
        env = six_route_env
        ids = _seed_history(env, "RAG 全流程搭建")
        env.conn.execute(
            "INSERT INTO learning_outcomes "
            "(date, kind, title, content, tech_stack, dataset, metrics,"
            " git_commit, github_url, resume_keywords, linked_kp_id,"
            " linked_topic_id, created_at, updated_at) "
            "VALUES ('2026-09-05','topic','RAG 成果','', '[]','','{}','','',"
            " '[]', ?, ?, '2026-09-05T10:00:00','2026-09-05T10:00:00')",
            (ids["kp_id"], ids["topic_id"]),
        )
        env.conn.commit()
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_all()
        row = env.conn.execute(
            "SELECT linked_topic_id, linked_kp_id FROM learning_outcomes"
        ).fetchone()
        assert row[0] == ids["topic_id"]
        assert row[1] == ids["kp_id"]


class TestMoveConflicts:
    def test_kp_name_collision_blocks_move(self, six_route_env):
        env = six_route_env
        ids = _seed_history(env, "Function Calling / Tool Calling")
        canonical = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        )
        canonical.ensure_pre_migration()
        r4_id = env.route_repo.get_by_key("R4_AI_AGENT").id
        a_repo = AssessmentRepository(env.conn)
        a_repo.create_knowledge_point(
            "Function Calling / Tool Calling", "", route_id=r4_id
        )
        mig = RouteMigrationService(env.conn, env.route_repo, env.plan_repo)
        preview = mig.preview()
        entry = next(
            e for e in preview.move
            if e.old_topic_id == ids["topic_id"]
        )
        assert any("kp_name_collision" in c for c in entry.conflicts)
        # strict apply 拒绝
        result = mig.apply(allow_partial=False)
        assert result["applied"] is False
        assert result["reason"] == "conflicts_present"

    def test_partial_apply_skips_conflict(self, six_route_env):
        env = six_route_env
        ids = _seed_history(env, "Planning 任务规划")
        canonical = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        )
        canonical.ensure_pre_migration()
        r4_id = env.route_repo.get_by_key("R4_AI_AGENT").id
        a_repo = AssessmentRepository(env.conn)
        a_repo.create_knowledge_point("Planning 任务规划", "", route_id=r4_id)
        result = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).apply(allow_partial=True)
        skipped_names = {s["old_name"] for s in result["skipped"]}
        assert "Planning 任务规划" in skipped_names
        # 其它 topic 仍迁移
        assert len(result["moved"]) > 0
        assert env.plan_repo.get_topic(ids["topic_id"]) is not None


class TestTransactionRollback:
    def test_rollback_on_missing_target(self, six_route_env):
        env = six_route_env
        _seed_history(env, "Chunking 分块策略")
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_pre_migration()
        mig = RouteMigrationService(env.conn, env.route_repo, env.plan_repo)
        preview = mig.preview()
        entry = next(
            e for e in preview.move if e.old_name == "Chunking 分块策略"
        )
        entry.target_phase = "不存在的阶段"
        # 手动调用内部移动：应抛错且不影响 topic
        topic = env.plan_repo.get_topic(entry.old_topic_id)
        before_phase = topic.phase_id
        with pytest.raises(ValueError):
            mig._move_topic(entry)
        topic_after = env.plan_repo.get_topic(entry.old_topic_id)
        assert topic_after.phase_id == before_phase


class TestMigrationIdempotency:
    def test_apply_twice_is_idempotent(self, six_route_env):
        env = six_route_env
        canonical = CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        )
        canonical.ensure_all()
        first = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).preview()
        assert first.summary()["move"] == 0  # 已无 legacy MOVE topic
        second = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).apply_if_safe()
        assert second["applied"] is False
        assert second["reason"] == "nothing_to_move"
        # meta 已记录
        assert RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).get_meta(MIGRATION_META_KEY) == "1"

    def test_preview_reports_move_action(self, six_route_env):
        env = six_route_env
        CanonicalRouteService(
            env.conn, env.route_repo, env.plan_repo, env.skill_repo
        ).ensure_pre_migration()
        preview = RouteMigrationService(
            env.conn, env.route_repo, env.plan_repo
        ).preview()
        entries = {e.old_name: e for e in preview.entries}
        assert entries["LoRA / QLoRA"].action == ACTION_MOVE
        assert entries["LoRA / QLoRA"].target_route_key == "R2_LLM_POST_TRAINING"
        assert entries["LoRA / QLoRA"].target_phase == "参数高效微调"
