"""学习路线（LearningRoute）数据访问层（Phase B）。

数据模型：
- learning_routes : 路线树（group 分组 / learning 可学习路线）
- route_skills    : route N↔N skill（skill 不复制）

层级（重要）：
    learning_routes -> study_plans -> study_phases -> study_topics
route 只挂在 study_plans 上，不给 phase/topic 重复存 route_id。

只负责 CRUD；名称重复 / 父子环等业务规则在
app/services/learning_route_service.py。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..utils.date_utils import now_iso

# route_type
ROUTE_TYPE_GROUP = "group"
ROUTE_TYPE_LEARNING = "learning"
ALL_ROUTE_TYPES = (ROUTE_TYPE_GROUP, ROUTE_TYPE_LEARNING)

# status
ROUTE_STATUS_ACTIVE = "active"
ROUTE_STATUS_ARCHIVED = "archived"
ALL_ROUTE_STATUS = (ROUTE_STATUS_ACTIVE, ROUTE_STATUS_ARCHIVED)

# source
ROUTE_SOURCE_SYSTEM = "system"
ROUTE_SOURCE_MANUAL = "manual"
ROUTE_SOURCE_AI = "ai"
ALL_ROUTE_SOURCES = (ROUTE_SOURCE_SYSTEM, ROUTE_SOURCE_MANUAL, ROUTE_SOURCE_AI)

PRIORITY_MIN = 1
PRIORITY_MAX = 5

# 系统默认路线名称（迁移创建）
DEFAULT_ROUTE_PARENT_NAME = "求职准备"
DEFAULT_ROUTE_LEARNING_NAME = "搜广推 + LLM"

_UPDATABLE = (
    "parent_id",
    "name",
    "description",
    "goal",
    "route_type",
    "status",
    "priority",
    "planning_enabled",
    "source",
    "archived_at",
)


@dataclass
class LearningRoute:
    id: int
    parent_id: int | None = None
    name: str = ""
    description: str = ""
    goal: str = ""
    route_type: str = ROUTE_TYPE_LEARNING
    status: str = ROUTE_STATUS_ACTIVE
    priority: int = 3
    planning_enabled: bool = True
    source: str = ROUTE_SOURCE_MANUAL
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None
    children: list["LearningRoute"] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.route_type == ROUTE_TYPE_GROUP

    @property
    def is_learning(self) -> bool:
        return self.route_type == ROUTE_TYPE_LEARNING

    @property
    def is_archived(self) -> bool:
        return self.status == ROUTE_STATUS_ARCHIVED


class LearningRouteRepository:
    """learning_routes / route_skills 读写。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 写 ----------

    def create(
        self,
        name: str,
        parent_id: int | None = None,
        description: str = "",
        goal: str = "",
        route_type: str = ROUTE_TYPE_LEARNING,
        status: str = ROUTE_STATUS_ACTIVE,
        priority: int = 3,
        planning_enabled: bool = True,
        source: str = ROUTE_SOURCE_MANUAL,
    ) -> LearningRoute:
        name = (name or "").strip()
        if not name:
            raise ValueError("路线名称不能为空")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO learning_routes "
            "(parent_id, name, description, goal, route_type, status, priority,"
            " planning_enabled, source, created_at, updated_at, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                parent_id, name, description or "", goal or "",
                route_type, status, int(priority), int(bool(planning_enabled)),
                source, ts, ts,
            ),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def update(self, route_id: int, **fields: Any) -> LearningRoute | None:
        allowed = {k: v for k, v in fields.items() if k in _UPDATABLE}
        if not allowed:
            return self.get(route_id)
        if "planning_enabled" in allowed:
            allowed["planning_enabled"] = int(bool(allowed["planning_enabled"]))
        allowed["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in allowed)
        self.conn.execute(
            f"UPDATE learning_routes SET {set_clause} WHERE id = ?",
            (*allowed.values(), int(route_id)),
        )
        self.conn.commit()
        return self.get(route_id)

    def delete(self, route_id: int) -> bool:
        """仅用于测试清理；生产不物理删除路线。"""
        cur = self.conn.execute(
            "DELETE FROM learning_routes WHERE id = ?", (int(route_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_priority(self, route_id: int, priority: int) -> LearningRoute | None:
        return self.update(route_id, priority=int(priority))

    def set_planning_enabled(
        self, route_id: int, enabled: bool
    ) -> LearningRoute | None:
        return self.update(route_id, planning_enabled=bool(enabled))

    def archive(self, route_id: int) -> LearningRoute | None:
        """归档：status=archived + planning_enabled=0 + archived_at。

        不删除 phases/topics/tasks/kp/assessment/review/skills。
        """
        return self.update(
            route_id,
            status=ROUTE_STATUS_ARCHIVED,
            planning_enabled=False,
            archived_at=now_iso(),
        )

    def restore(self, route_id: int) -> LearningRoute | None:
        """恢复：status=active，但 planning_enabled 保持 0（需显式恢复自动规划）。"""
        return self.update(
            route_id,
            status=ROUTE_STATUS_ACTIVE,
            planning_enabled=False,
        )

    # ---------- 读 ----------

    @staticmethod
    def _from_row(row) -> LearningRoute:
        d = dict(row)
        d["planning_enabled"] = bool(d.get("planning_enabled"))
        return LearningRoute(**d)

    def get(self, route_id: int) -> LearningRoute | None:
        row = self.conn.execute(
            "SELECT * FROM learning_routes WHERE id = ?", (int(route_id),)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_name_under_parent(
        self, name: str, parent_id: int | None = None
    ) -> LearningRoute | None:
        name = (name or "").strip()
        if parent_id is None:
            row = self.conn.execute(
                "SELECT * FROM learning_routes WHERE name = ? AND parent_id IS NULL "
                "ORDER BY id ASC LIMIT 1",
                (name,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM learning_routes WHERE name = ? AND parent_id = ? "
                "ORDER BY id ASC LIMIT 1",
                (name, int(parent_id)),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> list[LearningRoute]:
        rows = self.conn.execute(
            "SELECT * FROM learning_routes ORDER BY priority DESC, id ASC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_roots(self) -> list[LearningRoute]:
        rows = self.conn.execute(
            "SELECT * FROM learning_routes WHERE parent_id IS NULL "
            "ORDER BY priority DESC, id ASC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_children(self, parent_id: int | None) -> list[LearningRoute]:
        if parent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM learning_routes WHERE parent_id IS NULL "
                "ORDER BY priority DESC, id ASC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM learning_routes WHERE parent_id = ? "
                "ORDER BY priority DESC, id ASC",
                (int(parent_id),),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_learning_routes(
        self, status: str | None = None
    ) -> list[LearningRoute]:
        sql = "SELECT * FROM learning_routes WHERE route_type = 'learning'"
        args: tuple = ()
        if status is not None:
            sql += " AND status = ?"
            args = (status,)
        sql += " ORDER BY priority DESC, id ASC"
        rows = self.conn.execute(sql, args).fetchall()
        return [self._from_row(r) for r in rows]

    def get_default_learning_route(self) -> LearningRoute | None:
        """系统默认 learning route（搜广推 + LLM）。"""
        row = self.conn.execute(
            "SELECT * FROM learning_routes WHERE name = ? "
            "AND route_type = 'learning' AND source = 'system' "
            "ORDER BY id ASC LIMIT 1",
            (DEFAULT_ROUTE_LEARNING_NAME,),
        ).fetchone()
        return self._from_row(row) if row else None

    # ---------- route_skills ----------

    def assign_skill(self, route_id: int, skill_id: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO route_skills (route_id, skill_id, created_at) "
            "VALUES (?, ?, ?)",
            (int(route_id), int(skill_id), now_iso()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def unassign_skill(self, route_id: int, skill_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM route_skills WHERE route_id = ? AND skill_id = ?",
            (int(route_id), int(skill_id)),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_skill_ids(self, route_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT skill_id FROM route_skills WHERE route_id = ? "
            "ORDER BY skill_id ASC",
            (int(route_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def list_route_ids_for_skill(self, skill_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT route_id FROM route_skills WHERE skill_id = ? "
            "ORDER BY route_id ASC",
            (int(skill_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def count_route_skills(self) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) FROM route_skills").fetchone()[0]
        )
