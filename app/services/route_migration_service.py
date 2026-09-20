"""六路线历史 Topic 迁移（Phase 1）。

三种策略：
- **MOVE**：保留旧 topic id，reparent 到目标 canonical phase；同步
  topic-linked `tasks.route_id` 与 `knowledge_points.route_id`。
  assessment / review / mastery **不复制、不删除、不改分**。
- **SPLIT_NEW**：旧 Topic 留在 LEGACY；新 route-specific Topic 由
  canonical seed 创建（不带旧 mastery）。
- **KEEP_LEGACY / MANUAL_REVIEW**：什么都不动。

安全：
- `preview()` 输出每个 Topic 的动作与 conflict，不写库；
- `apply(allow_partial=False)` 在有 conflict 时拒绝执行；
- `apply_if_safe()` 仅在 preview 无 conflict 时执行；
- 每个 MOVE 在事务内完成；任何一步失败 rollback；
- 幂等：重复 apply 不会重复迁移（旧 topic 已不在 legacy 集合中）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from ..database.learning_route_repository import (
    ROUTE_KEY_LEGACY_SEARCH_LLM,
    LearningRoute,
    LearningRouteRepository,
)
from ..database.study_plan_repository import StudyPlanRepository
from . import canonical_routes as C

ACTION_MOVE = "MOVE"
ACTION_SPLIT_NEW = "SPLIT_NEW"
ACTION_KEEP_LEGACY = "KEEP_LEGACY"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"

MIGRATION_META_KEY = "six_route_migration_completed"


@dataclass
class TopicMigrationEntry:
    old_topic_id: int
    old_name: str
    action: str
    target_route_key: str = ""
    target_route_name: str = ""
    target_phase: str = ""
    task_count: int = 0
    kp_count: int = 0
    assessment_count: int = 0
    review_count: int = 0
    conflicts: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def migratable(self) -> bool:
        return self.action == ACTION_MOVE and not self.conflicts


@dataclass
class RouteMigrationPreview:
    entries: list[TopicMigrationEntry]
    legacy_route_id: Optional[int]

    @property
    def move(self) -> list[TopicMigrationEntry]:
        return [e for e in self.entries if e.action == ACTION_MOVE]

    @property
    def split_new(self) -> list[TopicMigrationEntry]:
        return [e for e in self.entries if e.action == ACTION_SPLIT_NEW]

    @property
    def keep_legacy(self) -> list[TopicMigrationEntry]:
        return [e for e in self.entries if e.action == ACTION_KEEP_LEGACY]

    @property
    def manual_review(self) -> list[TopicMigrationEntry]:
        return [e for e in self.entries if e.action == ACTION_MANUAL_REVIEW]

    @property
    def conflicts(self) -> list[TopicMigrationEntry]:
        return [e for e in self.entries if e.conflicts]

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def summary(self) -> dict:
        return {
            "move": len(self.move),
            "move_migratable": sum(1 for e in self.move if e.migratable),
            "split_new": len(self.split_new),
            "keep_legacy": len(self.keep_legacy),
            "manual_review": len(self.manual_review),
            "conflicts": len(self.conflicts),
        }


class RouteMigrationService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        route_repo: LearningRouteRepository | None = None,
        plan_repo: StudyPlanRepository | None = None,
    ):
        self.conn = conn
        self.route_repo = route_repo or LearningRouteRepository(conn)
        self.plan_repo = plan_repo or StudyPlanRepository(conn)

    # ================= legacy route / topics =================

    def legacy_route(self) -> Optional[LearningRoute]:
        route = self.route_repo.get_by_key(ROUTE_KEY_LEGACY_SEARCH_LLM)
        if route is not None:
            return route
        # 迁移前 route_key 还未设置：回退到旧默认路线（name-based legacy shim）
        return self.route_repo.get_default_learning_route()

    def _legacy_topics(self) -> list[sqlite3.Row]:
        """旧路线（route_id=legacy）或未绑定路线（route_id IS NULL）的 topics。

        不包含 canonical routes 的 topics（它们 route_id 已绑定），
        避免误把新 seed 的 topic 当作历史 topic。
        """
        legacy = self.legacy_route()
        legacy_id = legacy.id if legacy else -1
        return self.conn.execute(
            "SELECT t.id AS topic_id, t.name AS name, p.route_id AS plan_route_id "
            "FROM study_topics t "
            "JOIN study_phases ph ON ph.id = t.phase_id "
            "JOIN study_plans p ON p.id = ph.plan_id "
            "WHERE p.route_id = ? OR p.route_id IS NULL "
            "ORDER BY t.id ASC",
            (legacy_id,),
        ).fetchall()

    def _topic_history(self, topic_id: int) -> dict:
        task_count = int(self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE topic_id = ?", (topic_id,)
        ).fetchone()[0])
        kp_rows = self.conn.execute(
            "SELECT id FROM knowledge_points WHERE topic_id = ?", (topic_id,)
        ).fetchall()
        kp_ids = [int(r[0]) for r in kp_rows]
        assessment_count = 0
        review_count = 0
        if kp_ids:
            placeholders = ",".join("?" for _ in kp_ids)
            assessment_count = int(self.conn.execute(
                f"SELECT COUNT(*) FROM assessment_attempts "
                f"WHERE knowledge_point_id IN ({placeholders})",
                kp_ids,
            ).fetchone()[0])
            review_count = int(self.conn.execute(
                f"SELECT COUNT(*) FROM review_schedule "
                f"WHERE knowledge_point_id IN ({placeholders})",
                kp_ids,
            ).fetchone()[0])
        return {
            "task_count": task_count,
            "kp_count": len(kp_ids),
            "kp_ids": kp_ids,
            "assessment_count": assessment_count,
            "review_count": review_count,
        }

    # ================= preview =================

    def preview(self) -> RouteMigrationPreview:
        legacy = self.legacy_route()
        entries: list[TopicMigrationEntry] = []
        for row in self._legacy_topics():
            name = row["name"]
            topic_id = int(row["topic_id"])
            history = self._topic_history(topic_id)
            if name in C.LEGACY_MOVE_MAP:
                route_key, phase_name = C.LEGACY_MOVE_MAP[name]
                entry = TopicMigrationEntry(
                    old_topic_id=topic_id, old_name=name, action=ACTION_MOVE,
                    target_route_key=route_key,
                    target_route_name=C.CANONICAL_ROUTES[route_key]["name"],
                    target_phase=phase_name,
                    task_count=history["task_count"],
                    kp_count=history["kp_count"],
                    assessment_count=history["assessment_count"],
                    review_count=history["review_count"],
                )
                entry.conflicts = self._move_conflicts(entry, history)
                entries.append(entry)
            elif name in C.LEGACY_SPLIT_MAP:
                routes = C.LEGACY_SPLIT_MAP[name]
                entries.append(TopicMigrationEntry(
                    old_topic_id=topic_id, old_name=name,
                    action=ACTION_SPLIT_NEW,
                    target_route_key=",".join(routes),
                    target_route_name=" / ".join(
                        C.CANONICAL_ROUTES[r]["name"] for r in routes
                    ),
                    task_count=history["task_count"],
                    kp_count=history["kp_count"],
                    assessment_count=history["assessment_count"],
                    review_count=history["review_count"],
                    note="旧 Topic 保留 LEGACY；新 route-specific Topic 由 seed 创建（不继承 mastery）",
                ))
            elif name in C.LEGACY_MANUAL_REVIEW_TOPICS:
                entries.append(TopicMigrationEntry(
                    old_topic_id=topic_id, old_name=name,
                    action=ACTION_MANUAL_REVIEW,
                    task_count=history["task_count"],
                    kp_count=history["kp_count"],
                    assessment_count=history["assessment_count"],
                    review_count=history["review_count"],
                    note="保留 LEGACY，等待人工确认归属",
                ))
            else:
                entries.append(TopicMigrationEntry(
                    old_topic_id=topic_id, old_name=name,
                    action=ACTION_KEEP_LEGACY,
                    task_count=history["task_count"],
                    kp_count=history["kp_count"],
                    assessment_count=history["assessment_count"],
                    review_count=history["review_count"],
                ))
        return RouteMigrationPreview(entries=entries,
                                     legacy_route_id=legacy.id if legacy else None)

    def _move_conflicts(
        self, entry: TopicMigrationEntry, history: dict
    ) -> list[str]:
        conflicts: list[str] = []
        target_route = self.route_repo.get_by_key(entry.target_route_key)
        if target_route is None:
            conflicts.append("target_route_missing")
            return conflicts
        target_plan = self.plan_repo.get_plan_by_route(target_route.id)
        if target_plan is None:
            conflicts.append("target_plan_missing")
            return conflicts
        phases = {p.name: p for p in self.plan_repo.list_phases(target_plan.id)}
        phase = phases.get(entry.target_phase)
        if phase is None:
            conflicts.append("target_phase_missing")
            return conflicts

        # 同名 topic 冲突：仅当“已存在的同名 topic 有历史”才算真冲突
        existing = self.plan_repo.find_topic_by_name_in_phase(
            phase.id, entry.old_name
        )
        if existing is not None and int(existing.id) != entry.old_topic_id:
            ex_hist = self._topic_history(int(existing.id))
            if ex_hist["task_count"] > 0 or ex_hist["kp_count"] > 0:
                conflicts.append(
                    f"topic_name_collision_with_history(topic_id={existing.id})"
                )

        # kp (name, route_id) 冲突
        for kp_id in history.get("kp_ids", []):
            row = self.conn.execute(
                "SELECT name FROM knowledge_points WHERE id = ?", (kp_id,)
            ).fetchone()
            if row is None:
                continue
            clash = self.conn.execute(
                "SELECT id FROM knowledge_points WHERE name = ? AND route_id = ? "
                "AND id != ?",
                (row[0], target_route.id, kp_id),
            ).fetchone()
            if clash is not None:
                conflicts.append(
                    f"kp_name_collision(kp_id={kp_id}, existing={clash[0]})"
                )
        return conflicts

    # ================= apply =================

    def apply(
        self, allow_partial: bool = True, dry_run: bool = False
    ) -> dict:
        preview = self.preview()
        if preview.has_conflicts and not allow_partial:
            return {
                "applied": False,
                "reason": "conflicts_present",
                "summary": preview.summary(),
                "preview": preview,
            }
        moved: list[dict] = []
        skipped: list[dict] = []
        for entry in preview.move:
            if entry.conflicts:
                skipped.append({
                    "old_topic_id": entry.old_topic_id,
                    "old_name": entry.old_name,
                    "conflicts": list(entry.conflicts),
                })
                continue
            if dry_run:
                moved.append({
                    "old_topic_id": entry.old_topic_id,
                    "old_name": entry.old_name,
                    "target_route_key": entry.target_route_key,
                    "target_phase": entry.target_phase,
                    "dry_run": True,
                })
                continue
            result = self._move_topic(entry)
            moved.append(result)
        if not dry_run and moved:
            self._set_meta(MIGRATION_META_KEY, "1")
        return {
            "applied": not dry_run,
            "moved": moved,
            "skipped": skipped,
            "summary": preview.summary(),
            "preview": preview,
        }

    def apply_if_safe(self) -> dict:
        """preview 无 conflict 时自动 apply；否则跳过并返回报告。"""
        preview = self.preview()
        if preview.has_conflicts:
            return {
                "applied": False,
                "reason": "conflicts_present",
                "summary": preview.summary(),
                "preview": preview,
            }
        if not preview.move:
            return {
                "applied": False,
                "reason": "nothing_to_move",
                "summary": preview.summary(),
                "preview": preview,
            }
        return self.apply(allow_partial=True)

    def _move_topic(self, entry: TopicMigrationEntry) -> dict:
        """事务内完成一个 MOVE：reparent + 同步 task/kp route。"""
        target_route = self.route_repo.get_by_key(entry.target_route_key)
        if target_route is None:
            raise ValueError(f"target route 不存在: {entry.target_route_key}")
        target_plan = self.plan_repo.get_plan_by_route(target_route.id)
        if target_plan is None:
            raise ValueError(f"target plan 不存在: route={target_route.id}")
        phases = {p.name: p for p in self.plan_repo.list_phases(target_plan.id)}
        phase = phases.get(entry.target_phase)
        if phase is None:
            raise ValueError(f"target phase 不存在: {entry.target_phase}")

        conn = self.conn
        conn.execute("BEGIN")
        try:
            # 1) 清理“无历史的同名占位 topic”（seed 可能先创建了占位）
            existing = self.plan_repo.find_topic_by_name_in_phase(
                phase.id, entry.old_name
            )
            if existing is not None and int(existing.id) != entry.old_topic_id:
                ex_hist = self._topic_history(int(existing.id))
                if ex_hist["task_count"] > 0 or ex_hist["kp_count"] > 0:
                    raise ValueError(
                        f"同名 topic 有历史，拒绝覆盖: id={existing.id}"
                    )
                conn.execute(
                    "DELETE FROM study_topics WHERE id = ?", (int(existing.id),)
                )
            # 2) reparent（保留 topic id）
            conn.execute(
                "UPDATE study_topics SET phase_id = ? WHERE id = ?",
                (phase.id, entry.old_topic_id),
            )
            # 3) 同步 topic-linked tasks / kp 的 route
            conn.execute(
                "UPDATE tasks SET route_id = ? WHERE topic_id = ?",
                (target_route.id, entry.old_topic_id),
            )
            conn.execute(
                "UPDATE knowledge_points SET route_id = ? WHERE topic_id = ?",
                (target_route.id, entry.old_topic_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "old_topic_id": entry.old_topic_id,
            "old_name": entry.old_name,
            "target_route_key": entry.target_route_key,
            "target_route_id": target_route.id,
            "target_phase_id": phase.id,
            "target_phase": entry.target_phase,
        }

    # ================= inventory / meta =================

    def inventory(self) -> dict:
        legacy = self.legacy_route()
        legacy_id = legacy.id if legacy else None
        inv: dict = {
            "schema_version": int(
                self.conn.execute("PRAGMA user_version").fetchone()[0]
            ),
            "legacy_route_id": legacy_id,
            "routes": [dict(r) for r in self.conn.execute(
                "SELECT id, route_key, name, route_type, status, priority,"
                " planning_enabled FROM learning_routes ORDER BY id"
            ).fetchall()],
            "plans": [dict(r) for r in self.conn.execute(
                "SELECT id, name, status, route_id FROM study_plans ORDER BY id"
            ).fetchall()],
        }
        for table in (
            "study_phases", "study_topics", "tasks", "knowledge_points",
            "assessment_attempts", "review_schedule", "planner_decisions",
            "skills", "learning_outcomes", "route_skills",
        ):
            try:
                inv[table] = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0])
            except sqlite3.Error:
                inv[table] = None
        if legacy_id is not None:
            inv["legacy_topics"] = [dict(r) for r in self.conn.execute(
                "SELECT t.id, t.name FROM study_topics t "
                "JOIN study_phases ph ON ph.id = t.phase_id "
                "JOIN study_plans p ON p.id = ph.plan_id "
                "WHERE p.route_id = ? ORDER BY t.id",
                (legacy_id,),
            ).fetchall()]
        # active plan 唯一性诊断
        inv["duplicate_active_plans"] = [dict(r) for r in self.conn.execute(
            "SELECT route_id, COUNT(*) AS n FROM study_plans "
            "WHERE status = 'active' AND route_id IS NOT NULL "
            "GROUP BY route_id HAVING n > 1"
        ).fetchall()]
        return inv

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM app_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
