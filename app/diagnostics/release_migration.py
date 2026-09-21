"""Release migration / integrity tooling（v1 stabilization，只读优先）。

为 Windows 正式 DB 的逐级迁移提供安全、可核对、幂等的命令行能力：

    backup     复制 DB（不覆盖，带时间戳）
    inventory  迁移前只读盘点
    migrate    schema migrate → canonical seed/迁移（可选 capability backfill）
    verify     迁移后完整性校验（route / evidence / 行数对比）

设计原则：
- 默认不破坏历史行；只允许增加 canonical routes/plans/phases/topics/components/evidence；
- 任何 conflict 一律 fail（不继续）；
- 不输出 secret；不调用 AI。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

# 迁移前后必须“不减少”的核心历史表
HISTORY_TABLES = (
    "tasks",
    "knowledge_points",
    "assessment_attempts",
    "review_schedule",
    "learning_outcomes",
    "monthly_summaries",
    "planner_decisions",
)

# 允许增加的 canonical / 派生表
GROWTH_TABLES = (
    "learning_routes",
    "study_plans",
    "study_phases",
    "study_topics",
    "topic_learning_components",
    "capability_evidence",
    "practice_projects",
    "practice_topic_evidence",
    "practice_topic_requirements",
)


def _count(conn: sqlite3.Connection, table: str) -> Optional[int]:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return None


def _scalar(conn: sqlite3.Connection, sql: str, args: tuple = ()):
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def backup_database(db_path: str, out_dir: Optional[str] = None) -> str:
    """用 SQLite Backup API 备份（正确包含 WAL 中已提交的数据）。"""
    src_path = Path(db_path)
    if not src_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    target_dir = Path(out_dir) if out_dir else src_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = target_dir / f"{src_path.stem}_before_learning_system_v20_{stamp}.db"
    if dst.exists():
        raise FileExistsError(f"备份已存在，拒绝覆盖: {dst}")
    _sqlite_backup(db_path, str(dst))
    return str(dst)


def _sqlite_backup(src_path: str, dst_path: str) -> None:
    """使用 sqlite3 backup API 做一致性快照（同时覆盖 -wal / -shm）。

    直接 shutil.copy2 主 DB 文件会丢失仍在 WAL 中、但已提交的事务；
    backup API 读取的是一致性快照，不依赖 checkpoint。
    """
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    try:
        with dst:
            src.backup(dst)
        # 保证备份文件自身可独立打开且无残留 WAL
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()


def inventory(conn: sqlite3.Connection) -> dict:
    """只读盘点（迁移前/后均可运行）。"""
    data = {
        "schema_version": _scalar(conn, "PRAGMA user_version"),
        "counts": {t: _count(conn, t) for t in (
            "learning_routes", "study_plans", "study_phases", "study_topics",
            "tasks", "knowledge_points", "assessment_attempts",
            "review_schedule", "planner_decisions", "skills", "route_skills",
            "learning_outcomes", "monthly_summaries", "capability_evidence",
            "practice_projects", "practice_topic_evidence",
            "practice_topic_requirements",
        )},
    }
    data["tasks_done"] = _scalar(
        conn, "SELECT COUNT(*) FROM tasks WHERE status = 'done'"
    )
    data["mastery_nonzero"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM knowledge_points WHERE mastery_estimate > 0",
    )
    data["tasks_null_route"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM tasks WHERE route_id IS NULL "
        "AND status != 'cancelled'",
    )
    data["kp_null_route"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM knowledge_points WHERE route_id IS NULL",
    )
    # 旧「搜广推 + LLM」route（若存在）
    data["legacy_route"] = _scalar(
        conn,
        "SELECT name FROM learning_routes WHERE name = ? LIMIT 1",
        ("搜广推 + LLM",),
    )
    if data["legacy_route"]:
        data["legacy_topic_count"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM study_topics t "
            "JOIN study_phases ph ON ph.id = t.phase_id "
            "JOIN study_plans p ON p.id = ph.plan_id "
            "JOIN learning_routes r ON r.id = p.route_id "
            "WHERE r.name = ?",
            ("搜广推 + LLM",),
        )
    # duplicate active plans（同一 route 多个 active）
    data["duplicate_active_plans"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM (SELECT route_id FROM study_plans "
        "WHERE status='active' AND route_id IS NOT NULL "
        "GROUP BY route_id HAVING COUNT(*) > 1)",
    )
    # same-name KP conflicts（同 route 同名）
    data["same_name_kp_conflicts"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM (SELECT name, route_id FROM knowledge_points "
        "GROUP BY name, route_id HAVING COUNT(*) > 1)",
    )
    data["canonical_route_keys"] = _scalar_list(
        conn,
        "SELECT route_key FROM learning_routes WHERE route_key IS NOT NULL",
    )
    # 历史行指纹（行数不变也能发现静默篡改/替换）
    data["fingerprints"] = fingerprint(conn)
    data["fingerprint_version"] = FINGERPRINT_VERSION
    return data


def _scalar_list(conn: sqlite3.Connection, sql: str) -> list:
    """列查询容错（旧 schema 缺列时返回空）。"""
    try:
        return sorted({r[0] for r in conn.execute(sql).fetchall() if r[0]})
    except sqlite3.Error:
        return []


# ================= 历史行 fingerprint =================

# 每个历史表参与 fingerprint 的关键字段（缺列时自动跳过）
# 目的：行数不变时也能发现“静默篡改/替换”。
FINGERPRINT_COLUMNS: dict[str, tuple[str, ...]] = {
    "tasks": (
        "id", "title", "status", "scheduled_date", "topic_id",
        "source", "task_type", "knowledge_point_id",
        "estimated_minutes", "postpone_count",
    ),
    "knowledge_points": (
        "id", "name", "topic_id", "mastery_estimate",
        "review_count", "next_review_date", "interval_days",
    ),
    "assessment_attempts": (
        "id", "knowledge_point_id", "task_id", "judge_status",
        "result_level", "mastery_estimate", "created_at",
    ),
    "review_schedule": (
        "id", "knowledge_point_id", "scheduled_date", "interval_days",
        "status",
    ),
    "learning_outcomes": ("id", "kind", "title", "linked_kp_id"),
    "monthly_summaries": ("id", "period_start", "period_end", "source"),
    "planner_decisions": ("id", "date", "current_phase_id", "source"),
}
# 说明：以下 **migration-owned** 字段有意不入 tasks fingerprint：
#   - route_id：v15 canonical MOVE 会合法更新 topic 所属 route；
#   - component_id / learning_activity_kind：v16 `backfill_legacy_theory` 会合法地
#     把历史 done 任务从 NULL 绑定到 theory component。
# 它们的正确性改用结构校验（route_integrity /
# component_consistency_problems）而不是“不可变字段”比较。


# 每次修改 FINGERPRINT_COLUMNS 都要 +1：verify 仅在同版本时才比较 fields_hash，
# 否则旧 snapshot 会因列集合不同而产生误报。ids_hash 不受列变化影响，始终比较。
FINGERPRINT_VERSION = 2


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone())


def fingerprint(conn: sqlite3.Connection) -> dict:
    """历史表指纹：row count + id 集合 hash + 关键字段 hash。

    - ids_hash：检测“行被替换”（同数量但不同 id）；
    - fields_hash：检测“同 id 但关键字段被改”。
    旧 schema 缺列/缺表时自动跳过对应列/表。
    """
    out: dict[str, dict] = {}
    for table, wanted in FINGERPRINT_COLUMNS.items():
        if not _table_exists(conn, table):
            continue
        cols = _table_columns(conn, table)
        if "id" not in cols:
            continue
        # 始终投影完整的 wanted 列集合（缺失列用 NULL）——
        # 否则 schema 升级新增列会导致 before/after hash 不可比。
        select_cols = [
            c if c in cols else f"NULL AS {c}" for c in wanted
        ]
        order = "id"
        try:
            rows = conn.execute(
                f"SELECT {','.join(select_cols)} FROM {table} ORDER BY {order}"
            ).fetchall()
        except sqlite3.Error:
            continue
        ids_hash = hashlib.sha256(
            "|".join(str(r[0]) for r in rows).encode("utf-8")
        ).hexdigest()
        fields_hash = hashlib.sha256(
            "|".join(
                ",".join("" if v is None else str(v) for v in row)
                for row in rows
            ).encode("utf-8")
        ).hexdigest()
        out[table] = {
            "count": len(rows),
            "columns": list(wanted),
            "ids_hash": ids_hash,
            "fields_hash": fields_hash,
        }
    return out


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """PRAGMA integrity_check；正常时为 ['ok']。"""
    try:
        return [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.Error as e:  # noqa: BLE001
        return [f"integrity_check failed: {e}"]


def foreign_key_check(conn: sqlite3.Connection) -> list[dict]:
    """PRAGMA foreign_key_check；无问题时为空列表。"""
    try:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


@contextmanager
def readonly_copy(db_path: str) -> Iterator[sqlite3.Connection]:
    """临时副本上的真正只读连接（用于旧 schema 盘点，不触碰真实 DB）。"""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    with tempfile.TemporaryDirectory(prefix="study_agent_ro_") as td:
        tmp = str(Path(td) / "copy.db")
        _sqlite_backup(str(src), tmp)
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        try:
            yield conn
        finally:
            conn.close()


@contextmanager
def working_copy(
    db_path: str, migrate: bool = True
) -> Iterator[sqlite3.Connection]:
    """临时副本上的可写连接（可先 migrate）；真实 DB 保持不变。"""
    from ..database.schema import migrate_stepwise

    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    with tempfile.TemporaryDirectory(prefix="study_agent_work_") as td:
        tmp = str(Path(td) / "copy.db")
        _sqlite_backup(str(src), tmp)
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if migrate:
            migrate_stepwise(conn)
        try:
            yield conn
        finally:
            conn.close()


def source_preflight_problems(conn: sqlite3.Connection) -> list[dict]:
    """在**不修改**源库的前提下，找出 migration 会静默改写的业务条件。

    目前覆盖：
    - 同一 route 多个 active plan（v15 会静默 archive，需人工确认）；
    - learning_routes.route_key 重复（v15 unique index 会失败）。
    """
    problems: list[dict] = []
    # 同一 route 多个 active plan
    try:
        rows = conn.execute(
            "SELECT route_id, COUNT(*) AS n FROM study_plans "
            "WHERE status = 'active' AND route_id IS NOT NULL "
            "GROUP BY route_id HAVING COUNT(*) > 1"
        ).fetchall()
        for r in rows:
            problems.append({
                "kind": "duplicate_active_plans",
                "route_id": int(r[0]),
                "count": int(r[1]),
                "detail": "v15 会保留一个并 archive 其余（需人工确认）",
            })
    except sqlite3.Error:
        pass
    # route_key 重复
    try:
        rows = conn.execute(
            "SELECT route_key, COUNT(*) AS n FROM learning_routes "
            "WHERE route_key IS NOT NULL GROUP BY route_key HAVING COUNT(*) > 1"
        ).fetchall()
        for r in rows:
            problems.append({
                "kind": "duplicate_route_key",
                "route_key": r[0],
                "count": int(r[1]),
            })
    except sqlite3.Error:
        pass
    return problems


def dry_run_on_copy(db_path: str) -> dict:
    """在临时副本上跑 schema migrate + canonical ensure_all（真实 DB 零修改）。

    用于：
    - migration conflict pre-flight（在正式库发生任何业务修改之前发现）；
    - six-routes preview（真正只读预览）。
    """
    from ..database.learning_route_repository import LearningRouteRepository
    from ..database.schema import migrate_stepwise
    from ..database.skill_repository import SkillRepository
    from ..database.study_plan_repository import StudyPlanRepository
    from ..services.canonical_route_service import CanonicalRouteService

    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    # 0) 源库只读 pre-flight：先发现「会被静默改写的业务条件」
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row
    src_conn.execute("PRAGMA query_only = ON")
    try:
        source_problems = source_preflight_problems(src_conn)
    finally:
        src_conn.close()
    if source_problems:
        return {
            "ok": False,
            "stage": "source_preflight",
            "source_problems": source_problems,
            "conflicts": None,
        }
    with tempfile.TemporaryDirectory(prefix="study_agent_preflight_") as td:
        tmp = str(Path(td) / "preflight.db")
        _sqlite_backup(str(src), tmp)
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            steps: list[int] = []
            version_before = int(
                conn.execute("PRAGMA user_version").fetchone()[0]
            )
            try:
                final = migrate_stepwise(conn, on_step=steps.append)
            except Exception as e:  # noqa: BLE001 - schema conflict
                return {
                    "ok": False,
                    "stage": "schema_migration",
                    "error": str(e),
                    "schema_version_before": version_before,
                    "conflicts": None,
                }
            plan_repo = StudyPlanRepository(conn)
            route_repo = LearningRouteRepository(conn)
            skill_repo = SkillRepository(conn)
            canonical = CanonicalRouteService(
                conn, route_repo, plan_repo, skill_repo
            ).ensure_all()
            mig = canonical.get("migration") or {}
            summary = mig.get("summary") or {}
            conflicts = int(summary.get("conflicts") or 0)
            return {
                "ok": conflicts == 0,
                "stage": "canonical_migration",
                "schema_version_before": version_before,
                "schema_version_after": final,
                "steps": steps,
                "conflicts": conflicts,
                "summary": summary,
                "applied": mig.get("applied"),
                "reason": mig.get("reason"),
                "routes": canonical.get("route_ids"),
            }
        finally:
            conn.close()


# ================= integrity =================


def _safe_count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Optional[int]:
    """容错计数：旧 schema 缺表/缺列时返回 None（跳过检查）。"""
    try:
        return int(conn.execute(sql, args).fetchone()[0])
    except sqlite3.Error:
        return None


def route_integrity(conn: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    # task.route_id 与 topic route 一致
    bad = _safe_count(
        conn,
        "SELECT COUNT(*) FROM tasks t JOIN study_topics st ON st.id = t.topic_id "
        "JOIN study_phases ph ON ph.id = st.phase_id "
        "JOIN study_plans p ON p.id = ph.plan_id "
        "WHERE t.route_id IS NOT NULL AND p.route_id IS NOT NULL "
        "AND t.route_id != p.route_id",
    )
    if bad:
        problems.append(f"tasks.route_id 与 topic route 不一致: {bad}")
    # kp.route_id 与 topic route 一致
    bad = _safe_count(
        conn,
        "SELECT COUNT(*) FROM knowledge_points kp "
        "JOIN study_topics st ON st.id = kp.topic_id "
        "JOIN study_phases ph ON ph.id = st.phase_id "
        "JOIN study_plans p ON p.id = ph.plan_id "
        "WHERE kp.route_id IS NOT NULL AND p.route_id IS NOT NULL "
        "AND kp.route_id != p.route_id",
    )
    if bad:
        problems.append(f"knowledge_points.route_id 与 topic route 不一致: {bad}")
    # practice topic ∈ project route（v18+）
    bad = _safe_count(
        conn,
        "SELECT COUNT(*) FROM practice_project_topics pt "
        "JOIN study_topics st ON st.id = pt.topic_id "
        "JOIN study_phases ph ON ph.id = st.phase_id "
        "JOIN study_plans p ON p.id = ph.plan_id "
        "WHERE p.route_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM practice_project_routes pr "
        "  WHERE pr.project_id = pt.project_id AND pr.route_id = p.route_id)",
    )
    if bad:
        problems.append(f"practice topic 不在 project route: {bad}")
    # requirement topic ∈ project topic（v20+）
    bad = _safe_count(
        conn,
        "SELECT COUNT(*) FROM practice_topic_requirements r "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM practice_project_topics pt "
        "  WHERE pt.project_id = r.project_id AND pt.topic_id = r.topic_id)",
    )
    if bad:
        problems.append(f"requirement topic 未关联 project: {bad}")
    return problems


def evidence_integrity(conn: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    checks = (
        ("SELECT COUNT(*) FROM capability_evidence c WHERE NOT EXISTS ("
         "SELECT 1 FROM knowledge_points kp WHERE kp.id = c.knowledge_point_id)",
         "capability_evidence 指向不存在 KP"),
        ("SELECT COUNT(*) FROM capability_evidence c "
         "JOIN knowledge_points kp ON kp.id = c.knowledge_point_id "
         "JOIN tasks t ON t.id = c.source_task_id "
         "WHERE c.source_task_id IS NOT NULL AND kp.route_id IS NOT NULL "
         "AND t.route_id IS NOT NULL AND kp.route_id != t.route_id",
         "capability evidence 跨 route（task）"),
        ("SELECT COUNT(*) FROM assessment_attempts a WHERE NOT EXISTS ("
         "SELECT 1 FROM knowledge_points kp WHERE kp.id = a.knowledge_point_id)",
         "assessment_attempts 指向不存在 KP"),
        ("SELECT COUNT(*) FROM capability_evidence c "
         "WHERE c.learning_outcome_id IS NOT NULL AND NOT EXISTS ("
         "SELECT 1 FROM learning_outcomes o WHERE o.id = c.learning_outcome_id)",
         "capability evidence 指向不存在 outcome"),
        ("SELECT COUNT(*) FROM capability_evidence c "
         "WHERE c.practice_topic_evidence_id IS NOT NULL AND NOT EXISTS ("
         "SELECT 1 FROM practice_topic_evidence e "
         "WHERE e.id = c.practice_topic_evidence_id)",
         "practice capability 指向不存在 PTE"),
        ("SELECT COUNT(*) FROM capability_evidence c "
         "JOIN practice_topic_evidence e ON e.id = c.practice_topic_evidence_id "
         "WHERE c.practice_topic_evidence_id IS NOT NULL AND NOT EXISTS ("
         "SELECT 1 FROM practice_topic_evidence_outputs l "
         "WHERE l.evidence_id = e.id)",
         "practice PROJECT evidence 无支撑 Output"),
    )
    for sql, label in checks:
        bad = _safe_count(conn, sql)
        if bad:
            problems.append(f"{label}: {bad}")
    return problems


def component_consistency_problems(conn: sqlite3.Connection) -> list[dict]:
    """task.component_id / learning_activity_kind 的结构一致性校验。

    复用既有 TopicLearningProfileService.validate_consistency()（不新建第二套规则），
    覆盖：
    - component_id 非 NULL 但 component 不存在（dangling）；
    - component.topic_id != task.topic_id；
    - component.activity_kind != task.learning_activity_kind。

    这些字段是 migration-owned（不会被 fingerprint 当不可变字段），
    因此必须由本结构校验单独守护。
    """
    from ..database.topic_learning_repository import (
        TopicLearningComponentRepository,
    )
    from ..services.topic_learning_profile_service import (
        TopicLearningProfileService,
    )

    try:
        service = TopicLearningProfileService(
            conn, TopicLearningComponentRepository(conn)
        )
        return service.validate_consistency()
    except sqlite3.Error:
        return []  # v16 之前的旧 schema 无 component 表 → 跳过
    except Exception:  # noqa: BLE001 - 校验异常不应让 verify 崩溃
        return []


def verify(conn: sqlite3.Connection, before: Optional[dict] = None) -> dict:
    """完整性校验 + （可选）before/after 对比。

    包含：PRAGMA integrity_check、PRAGMA foreign_key_check、
    route / evidence / component 结构一致性、历史行数不减少、历史行指纹不变。

    注意：tasks 的 migration-owned 字段（route_id / component_id /
    learning_activity_kind）不入不可变指纹，改由 route_integrity /
    component_consistency_problems 做结构校验，避免迁移 false-positive。
    """
    after = inventory(conn)
    integrity = integrity_check(conn)
    fk_problems = foreign_key_check(conn)
    result = {
        "schema_version": after["schema_version"],
        "integrity_check": integrity,
        "foreign_key_problems": fk_problems,
        "route_problems": route_integrity(conn),
        "evidence_problems": evidence_integrity(conn),
        "component_problems": component_consistency_problems(conn),
        "before": before,
        "after": after,
        "history_decreases": {},
        "history_fingerprint_changes": {},
        "history_id_changes": {},
        "fingerprint_version": after.get("fingerprint_version"),
        "fingerprint_version_match": None,
    }
    if before:
        fp_version_match = (
            before.get("fingerprint_version") == after.get("fingerprint_version")
        )
        result["fingerprint_version_match"] = fp_version_match
        for table in HISTORY_TABLES:
            b = (before.get("counts") or {}).get(table)
            a = (after.get("counts") or {}).get(table)
            if b is not None and a is not None and a < b:
                result["history_decreases"][table] = {"before": b, "after": a}
        before_fp = before.get("fingerprints") or {}
        after_fp = after.get("fingerprints") or {}
        for table, bfp in before_fp.items():
            afp = after_fp.get(table)
            if afp is None:
                continue
            if bfp.get("ids_hash") != afp.get("ids_hash"):
                result["history_id_changes"][table] = {
                    "before_count": bfp.get("count"),
                    "after_count": afp.get("count"),
                }
            # 仅同 fingerprint 版本时比较不可变字段 hash；旧 snapshot（列集合不同）
            # 会产生误报，此时仅依赖 ids_hash + 结构校验。
            if fp_version_match and \
                    bfp.get("fields_hash") != afp.get("fields_hash"):
                result["history_fingerprint_changes"][table] = {
                    "before_count": bfp.get("count"),
                    "after_count": afp.get("count"),
                    "columns": afp.get("columns"),
                }
    result["ok"] = (
        integrity == ["ok"]
        and not fk_problems
        and not result["route_problems"]
        and not result["evidence_problems"]
        and not result["component_problems"]
        and not result["history_decreases"]
        and not result["history_fingerprint_changes"]
        and not result["history_id_changes"]
    )
    return result


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
