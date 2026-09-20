"""端到端六路线迁移测试（Phase 1）。

模拟“真实历史 DB → 备份副本 → 迁移”的完整流程：
- 先在一个 DB 上造出旧“搜广推 + LLM”的学习历史；
- 复制为副本，只在副本上跑迁移；
- 验证副本迁移无损、原库不受影响。
"""

from __future__ import annotations

import shutil

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.connection import get_connection
from app.database.learning_route_repository import (
    ROUTE_KEY_LEGACY_SEARCH_LLM,
    LearningRouteRepository,
)
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services import canonical_routes as C
from app.services.canonical_route_service import CanonicalRouteService
from app.services.route_migration_service import (
    MIGRATION_META_KEY,
    RouteMigrationService,
)
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService


def _build_historical_db(path) -> dict:
    """在给定路径建出一个带学习历史的 legacy DB，返回初始计数与 id。"""
    conn = get_connection(path)
    repo = TaskRepository(conn)
    plan_repo = StudyPlanRepository(conn)
    StudyPlanService(repo, plan_repo).ensure_default_plan()
    route_repo = LearningRouteRepository(conn)
    legacy = route_repo.get_default_learning_route()
    skill_repo = SkillRepository(conn)
    SkillService(skill_repo, plan_repo=plan_repo, assessment_repo=None) \
        .seed_from_career_context()

    ids = {}
    a_repo = AssessmentRepository(conn)
    for name in ("Function Calling / Tool Calling", "RAG 全流程搭建",
                 "LoRA / QLoRA", "推荐系统整体架构"):
        topic = plan_repo.find_topic_by_name_in_route(legacy.id, name)
        task = repo.create(
            title=f"学 {name}", scheduled_date="2026-09-05",
            source="generated", task_type="new", topic_id=topic.id,
            route_id=legacy.id,
        )
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (task.id,))
        kp = a_repo.get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, topic.description, route_id=legacy.id
        )
        attempt = a_repo.create_attempt(kp["id"], "[]", task_id=task.id)
        a_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.77,
            last_assessed_at="2026-09-05T10:00:00", review_count=1,
        )
        conn.execute(
            "INSERT INTO review_schedule "
            "(knowledge_point_id, scheduled_date, interval_days, status,"
            " task_id, source_attempt_id, created_at) "
            "VALUES (?, '2026-09-06', 1, 'pending', ?, ?,"
            " '2026-09-05T10:00:00')",
            (kp["id"], task.id, attempt["id"]),
        )
        conn.commit()
        ids[name] = {"topic_id": topic.id, "task_id": task.id, "kp_id": kp["id"],
                     "attempt_id": attempt["id"]}

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("tasks", "knowledge_points", "assessment_attempts",
                  "review_schedule", "study_topics")
    }
    conn.close()
    return {"ids": ids, "counts": counts, "legacy_route_id": legacy.id}


class TestEndToEndMigration:
    def test_backup_copy_migrated_original_untouched(self, tmp_path):
        original = tmp_path / "study_agent_before_six_routes.db"
        _build_historical_db(original)
        backup = tmp_path / "work_copy.db"
        shutil.copyfile(original, backup)

        # 原库不动
        conn_orig = get_connection(original)
        assert conn_orig.execute(
            "SELECT COUNT(*) FROM learning_routes WHERE route_key IS NOT NULL"
        ).fetchone()[0] == 0
        conn_orig.close()

        # 副本迁移
        conn = get_connection(backup)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        result = CanonicalRouteService(
            conn, route_repo, plan_repo, skill_repo
        ).ensure_all()
        assert result["migration"]["applied"] is True
        s = result["migration"]["summary"]
        assert s["move_migratable"] == 44
        assert s["split_new"] == 9
        assert s["manual_review"] == 1
        assert s["conflicts"] == 0
        # 六条 canonical route 都可规划
        assert len(result["route_ids"]) == 6
        conn.close()

    def test_inventory_and_preview(self, tmp_path):
        path = tmp_path / "legacy.db"
        _build_historical_db(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        mig = RouteMigrationService(conn, route_repo, plan_repo)

        inv = mig.inventory()
        assert inv["legacy_route_id"] is not None
        assert inv["study_topics"] == 54
        assert inv["tasks"] == 4
        assert inv["knowledge_points"] == 4
        assert inv["duplicate_active_plans"] == []

        CanonicalRouteService(conn, route_repo, plan_repo).ensure_pre_migration()
        preview = mig.preview()
        assert preview.has_conflicts is False
        assert preview.summary()["move"] == 44
        assert preview.summary()["split_new"] == 9
        conn.close()

    def test_migration_preserves_evidence_counts(self, tmp_path):
        path = tmp_path / "legacy.db"
        built = _build_historical_db(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        before = built["counts"]
        CanonicalRouteService(
            conn, route_repo, plan_repo, skill_repo
        ).ensure_all()
        after = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in before
        }
        # study_topics 会新增 canonical topics；其余证据数量不变
        assert after["tasks"] == before["tasks"]
        assert after["knowledge_points"] == before["knowledge_points"]
        assert after["assessment_attempts"] == before["assessment_attempts"]
        assert after["review_schedule"] == before["review_schedule"]
        assert after["study_topics"] >= before["study_topics"]
        conn.close()

    def test_meta_flag_and_idempotency(self, tmp_path):
        path = tmp_path / "legacy.db"
        _build_historical_db(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        svc = CanonicalRouteService(conn, route_repo, plan_repo, skill_repo)
        svc.ensure_all()
        mig = RouteMigrationService(conn, route_repo, plan_repo)
        assert mig.get_meta(MIGRATION_META_KEY) == "1"
        # 再跑一次：没有可迁移项
        second = mig.apply_if_safe()
        assert second["applied"] is False
        assert second["reason"] == "nothing_to_move"
        # 六路线数量稳定
        assert len(route_repo.get_canonical_routes() if hasattr(
            route_repo, "get_canonical_routes") else
            [route_repo.get_by_key(k) for k in C.CANONICAL_LEARNING_KEYS]) == 6
        conn.close()

    def test_legacy_archived_and_evidence_still_queryable(self, tmp_path):
        path = tmp_path / "legacy.db"
        built = _build_historical_db(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo).ensure_all()
        legacy = route_repo.get_by_key(ROUTE_KEY_LEGACY_SEARCH_LLM)
        assert legacy.status == "archived"
        # 旧 EVIDENCE 仍可按 legacy route 查到（SPLIT/KEEP topic 留在 legacy）
        remaining = plan_repo.list_topics_by_route(legacy.id)
        assert len(remaining) == 10
        conn.close()

    def test_clear_move_task_route_updated(self, tmp_path):
        path = tmp_path / "legacy.db"
        built = _build_historical_db(path)
        conn = get_connection(path)
        route_repo = LearningRouteRepository(conn)
        plan_repo = StudyPlanRepository(conn)
        skill_repo = SkillRepository(conn)
        CanonicalRouteService(conn, route_repo, plan_repo, skill_repo).ensure_all()
        # Function Calling → R4；task/kp route 同步，id 不变
        info = built["ids"]["Function Calling / Tool Calling"]
        r4 = route_repo.get_by_key("R4_AI_AGENT")
        row = conn.execute(
            "SELECT route_id FROM tasks WHERE id = ?", (info["task_id"],)
        ).fetchone()
        assert row[0] == r4.id
        kp = conn.execute(
            "SELECT route_id, mastery_estimate FROM knowledge_points WHERE id = ?",
            (info["kp_id"],),
        ).fetchone()
        assert kp[0] == r4.id
        assert float(kp[1]) == pytest.approx(0.77)
        conn.close()
