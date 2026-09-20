"""Practice / Project 数据访问层（Phase 4）。

所有 SQL 集中在此；不放到 UI / Service。

- practice_projects
- practice_project_routes / _skills / _topics（N:N，UNIQUE 幂等）
- practice_milestones
- practice_outputs
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..services.practice import (
    ALL_MILESTONE_STATUSES,
    ALL_PROJECT_SOURCES,
    ALL_PROJECT_STATUSES,
    ALL_PROJECT_TYPES,
    OUTPUT_TYPES,
)
from ..utils.date_utils import now_iso


def _dec_dict(text) -> dict:
    try:
        data = json.loads(text) if text else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


class PracticeProjectRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 转换 ----------

    @staticmethod
    def _from_row(row) -> Optional[dict]:
        if row is None:
            return None
        return dict(row)

    # ---------- project CRUD ----------

    def create(
        self,
        name: str,
        project_type: str = "other",
        status: str = "planned",
        description: str = "",
        goal: str = "",
        source: str = "manual",
        started_at: str | None = None,
        target_date: str | None = None,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名称不能为空")
        if project_type not in ALL_PROJECT_TYPES:
            raise ValueError(f"非法 project_type: {project_type!r}")
        if status not in ALL_PROJECT_STATUSES:
            raise ValueError(f"非法 status: {status!r}")
        if source not in ALL_PROJECT_SOURCES:
            raise ValueError(f"非法 source: {source!r}")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO practice_projects "
            "(name, description, goal, project_type, status, source,"
            " started_at, target_date, completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (name, description or "", goal or "", project_type, status, source,
             started_at, target_date, ts, ts),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, project_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM practice_projects WHERE id = ?", (int(project_id),)
        ).fetchone()
        return self._from_row(row)

    def list_all(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM practice_projects"
        args: list = []
        if status is not None:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY updated_at DESC, id DESC"
        return [self._from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def list_active(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM practice_projects WHERE status != 'archived' "
            "ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_archived(self) -> list[dict]:
        return self.list_all(status="archived")

    _UPDATABLE = (
        "name", "description", "goal", "project_type", "status", "source",
        "archived_from_status", "started_at", "target_date", "completed_at",
    )

    def update(self, project_id: int, **fields) -> Optional[dict]:
        sets = {k: v for k, v in fields.items() if k in self._UPDATABLE}
        if "project_type" in sets and sets["project_type"] not in ALL_PROJECT_TYPES:
            raise ValueError("非法 project_type")
        if "status" in sets and sets["status"] not in ALL_PROJECT_STATUSES:
            raise ValueError("非法 status")
        if not sets:
            return self.get(project_id)
        sets["updated_at"] = now_iso()
        clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE practice_projects SET {clause} WHERE id = ?",
            (*sets.values(), int(project_id)),
        )
        self.conn.commit()
        return self.get(project_id)

    def delete(self, project_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_projects WHERE id = ?", (int(project_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- relations: routes ----------

    def add_route(self, project_id: int, route_id: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO practice_project_routes "
            "(project_id, route_id, created_at) VALUES (?, ?, ?)",
            (int(project_id), int(route_id), now_iso()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_route(self, project_id: int, route_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_project_routes "
            "WHERE project_id = ? AND route_id = ?",
            (int(project_id), int(route_id)),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_route_ids(self, project_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT route_id FROM practice_project_routes WHERE project_id = ? "
            "ORDER BY route_id",
            (int(project_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # ---------- relations: skills ----------

    def add_skill(self, project_id: int, skill_id: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO practice_project_skills "
            "(project_id, skill_id, created_at) VALUES (?, ?, ?)",
            (int(project_id), int(skill_id), now_iso()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_skill(self, project_id: int, skill_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_project_skills "
            "WHERE project_id = ? AND skill_id = ?",
            (int(project_id), int(skill_id)),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_skill_ids(self, project_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT skill_id FROM practice_project_skills WHERE project_id = ? "
            "ORDER BY skill_id",
            (int(project_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # ---------- relations: topics ----------

    def add_topic(self, project_id: int, topic_id: int) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO practice_project_topics "
            "(project_id, topic_id, created_at) VALUES (?, ?, ?)",
            (int(project_id), int(topic_id), now_iso()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_topic(self, project_id: int, topic_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_project_topics "
            "WHERE project_id = ? AND topic_id = ?",
            (int(project_id), int(topic_id)),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_topic_ids(self, project_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT topic_id FROM practice_project_topics WHERE project_id = ? "
            "ORDER BY topic_id",
            (int(project_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # ---------- reverse queries ----------

    def list_by_route(self, route_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT p.* FROM practice_projects p "
            "JOIN practice_project_routes r ON r.project_id = p.id "
            "WHERE r.route_id = ? ORDER BY p.updated_at DESC, p.id DESC",
            (int(route_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_skill(self, skill_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT p.* FROM practice_projects p "
            "JOIN practice_project_skills s ON s.project_id = p.id "
            "WHERE s.skill_id = ? ORDER BY p.updated_at DESC, p.id DESC",
            (int(skill_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_topic(self, topic_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT p.* FROM practice_projects p "
            "JOIN practice_project_topics t ON t.project_id = p.id "
            "WHERE t.topic_id = ? ORDER BY p.updated_at DESC, p.id DESC",
            (int(topic_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count_by_route(self, route_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_project_routes WHERE route_id = ?",
            (int(route_id),),
        ).fetchone()
        return int(row[0])

    def count_by_skill(self, skill_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_project_skills WHERE skill_id = ?",
            (int(skill_id),),
        ).fetchone()
        return int(row[0])

    def count_by_topic(self, topic_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_project_topics WHERE topic_id = ?",
            (int(topic_id),),
        ).fetchone()
        return int(row[0])

    def has_history(self, project_id: int) -> bool:
        """是否有 milestone / output / relation（决定能否物理删除）。"""
        pid = int(project_id)
        for table in ("practice_milestones", "practice_outputs",
                      "practice_project_routes", "practice_project_skills",
                      "practice_project_topics"):
            row = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE project_id = ? LIMIT 1", (pid,)
            ).fetchone()
            if row is not None:
                return True
        return False


class PracticeMilestoneRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _from_row(row) -> Optional[dict]:
        if row is None:
            return None
        return dict(row)

    def create(self, project_id: int, title: str, description: str = "",
               status: str = "todo", order_index: int | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("里程碑标题不能为空")
        if status not in ALL_MILESTONE_STATUSES:
            raise ValueError(f"非法 milestone status: {status!r}")
        if order_index is None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(order_index), 0) + 1 "
                "FROM practice_milestones WHERE project_id = ?",
                (int(project_id),),
            ).fetchone()
            order_index = int(row[0])
        ts = now_iso()
        completed_at = ts if status == "done" else None
        cur = self.conn.execute(
            "INSERT INTO practice_milestones "
            "(project_id, title, description, status, order_index,"
            " completed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), title, description or "", status,
             int(order_index), completed_at, ts, ts),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, milestone_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM practice_milestones WHERE id = ?",
            (int(milestone_id),),
        ).fetchone()
        return self._from_row(row)

    def list_by_project(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM practice_milestones WHERE project_id = ? "
            "ORDER BY order_index ASC, id ASC",
            (int(project_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def update(self, milestone_id: int, **fields) -> Optional[dict]:
        allowed = {"title", "description", "status", "order_index",
                   "completed_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "status" in sets and sets["status"] not in ALL_MILESTONE_STATUSES:
            raise ValueError("非法 milestone status")
        if not sets:
            return self.get(milestone_id)
        if "status" in sets:
            if sets["status"] == "done":
                sets.setdefault("completed_at", now_iso())
            else:
                sets["completed_at"] = None
        sets["updated_at"] = now_iso()
        clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE practice_milestones SET {clause} WHERE id = ?",
            (*sets.values(), int(milestone_id)),
        )
        self.conn.commit()
        return self.get(milestone_id)

    def set_status(self, milestone_id: int, status: str) -> Optional[dict]:
        return self.update(milestone_id, status=status)

    def set_order(self, milestone_id: int, order_index: int) -> Optional[dict]:
        return self.update(milestone_id, order_index=int(order_index))

    def reorder(self, project_id: int, ordered_ids: list[int]) -> None:
        for idx, mid in enumerate(ordered_ids):
            self.conn.execute(
                "UPDATE practice_milestones SET order_index = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (idx + 1, now_iso(), int(mid), int(project_id)),
            )
        self.conn.commit()

    def delete(self, milestone_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_milestones WHERE id = ?", (int(milestone_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def count_by_project(self, project_id: int) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) "
            "FROM practice_milestones WHERE project_id = ?",
            (int(project_id),),
        ).fetchone()
        total = int(row[0] or 0)
        done = int(row[1] or 0)
        return done, total


class PracticeOutputRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _from_row(row) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d["details"] = _dec_dict(d.get("details_json"))
        return d

    def create(self, project_id: int, output_type: str, title: str,
               description: str = "", uri: str | None = None,
               details: dict | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("成果标题不能为空")
        if output_type not in OUTPUT_TYPES:
            raise ValueError(f"非法 output_type: {output_type!r}")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO practice_outputs "
            "(project_id, output_type, title, description, uri, details_json,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), output_type, title, description or "",
             (uri or None), json.dumps(details or {}, ensure_ascii=False),
             ts, ts),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, output_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM practice_outputs WHERE id = ?", (int(output_id),)
        ).fetchone()
        return self._from_row(row)

    def list_by_project(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM practice_outputs WHERE project_id = ? "
            "ORDER BY id ASC",
            (int(project_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def update(self, output_id: int, **fields) -> Optional[dict]:
        allowed = {"output_type", "title", "description", "uri", "details"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "output_type" in sets and sets["output_type"] not in OUTPUT_TYPES:
            raise ValueError("非法 output_type")
        if "details" in sets:
            sets["details_json"] = json.dumps(
                sets.pop("details") or {}, ensure_ascii=False
            )
        if not sets:
            return self.get(output_id)
        sets["updated_at"] = now_iso()
        clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE practice_outputs SET {clause} WHERE id = ?",
            (*sets.values(), int(output_id)),
        )
        self.conn.commit()
        return self.get(output_id)

    def delete(self, output_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM practice_outputs WHERE id = ?", (int(output_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def count_by_project(self, project_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_outputs WHERE project_id = ?",
            (int(project_id),),
        ).fetchone()
        return int(row[0])
