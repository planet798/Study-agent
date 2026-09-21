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

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..utils.date_utils import today as _today

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
    """复制 DB 到带时间戳的备份文件；绝不覆盖已存在文件。"""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    target_dir = Path(out_dir) if out_dir else src.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = target_dir / f"{src.stem}_before_learning_system_v20_{stamp}.db"
    if dst.exists():
        raise FileExistsError(f"备份已存在，拒绝覆盖: {dst}")
    shutil.copy2(src, dst)
    return str(dst)


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
    return data


def _scalar_list(conn: sqlite3.Connection, sql: str) -> list:
    """列查询容错（旧 schema 缺列时返回空）。"""
    try:
        return sorted({r[0] for r in conn.execute(sql).fetchall() if r[0]})
    except sqlite3.Error:
        return []


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


def verify(conn: sqlite3.Connection, before: Optional[dict] = None) -> dict:
    """完整性校验 + （可选）before/after 对比。"""
    after = inventory(conn)
    result = {
        "schema_version": after["schema_version"],
        "route_problems": route_integrity(conn),
        "evidence_problems": evidence_integrity(conn),
        "before": before,
        "after": after,
        "history_decreases": {},
        "unexplained_growth": {},
    }
    if before:
        for table in HISTORY_TABLES:
            b = (before.get("counts") or {}).get(table)
            a = (after.get("counts") or {}).get(table)
            if b is not None and a is not None and a < b:
                result["history_decreases"][table] = {"before": b, "after": a}
    result["ok"] = (
        not result["route_problems"]
        and not result["evidence_problems"]
        and not result["history_decreases"]
    )
    return result


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
