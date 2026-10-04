"""共享的逐级发布迁移执行器；GUI 与 CLI 均复用此流程。"""
from __future__ import annotations

def _conn_db_path(conn) -> str | None:
    """从连接取主 DB 文件路径（用于 pre-flight 副本）。"""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list"):
            if name == "main" and path:
                return str(path)
    except Exception:  # noqa: BLE001
        return None
    return None



def run_release_migrate(
    conn, apply_capability: bool = False, skip_preflight: bool = False
) -> dict:
    """schema 逐级迁移 → canonical seed/迁移（→ 可选 capability backfill）。

    默认先在不接触正式库的临时副本上 pre-flight；conflict/错误时抛异常，
    保证正式库在任何业务修改前就中止。
    """
    if not skip_preflight:
        db_path = _conn_db_path(conn)
        if db_path:
            from app.diagnostics import release_migration as _rm

            pre = _rm.dry_run_on_copy(db_path)
            if not pre.get("ok"):
                raise RuntimeError(
                    "migration preflight failed: "
                    f"stage={pre.get('stage')} "
                    f"conflicts={pre.get('conflicts')} "
                    f"source_problems={pre.get('source_problems')} "
                    f"error={pre.get('error')}"
                )
    from app.database.assessment_repository import AssessmentRepository
    from app.database.capability_repository import CapabilityEvidenceRepository
    from app.database.learning_route_repository import LearningRouteRepository
    from app.database.repository import TaskRepository
    from app.database.schema import get_schema_version, migrate_stepwise
    from app.database.skill_repository import SkillRepository
    from app.database.study_plan_repository import StudyPlanRepository
    from app.database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from app.services.canonical_route_service import CanonicalRouteService
    from app.services.capability_service import CapabilityService
    from app.services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    steps: list[int] = []
    version_before = get_schema_version(conn)
    final = migrate_stepwise(conn, on_step=steps.append)
    plan_repo = StudyPlanRepository(conn)
    route_repo = LearningRouteRepository(conn)
    skill_repo = SkillRepository(conn)
    tl = TopicLearningProfileService(
        conn, TopicLearningComponentRepository(conn)
    )
    canonical = CanonicalRouteService(
        conn, route_repo, plan_repo, skill_repo,
        topic_learning_service=tl,
    ).ensure_all()
    mig = canonical.get("migration") or {}
    conflicts = int(mig.get("conflicts") or 0)
    if conflicts:
        raise RuntimeError(
            f"六路线迁移存在 conflicts={conflicts}，已停止（请先人工处理）"
        )
    cap_stats = None
    if apply_capability:
        svc = CapabilityService(conn, CapabilityEvidenceRepository(conn))
        preview = svc.preview()
        if preview.get("conflicts"):
            raise RuntimeError(
                f"capability backfill 存在 conflicts={preview['conflicts']}，已停止"
            )
        cap_stats = svc.backfill()
    return {
        "schema_version_before": version_before,
        "schema_version_after": final,
        "steps": steps,
        "routes": canonical.get("route_ids"),
        "migration": mig,
        "route_skill_links": canonical.get("route_skill_links"),
        "topic_skill_links": canonical.get("topic_skill_links"),
        "capability_backfill": cap_stats,
    }

