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


class PracticeTopicEvidenceRepository:
    """PracticeTopicEvidence 数据访问（Phase 5）。

    所有 SQL 集中在此。默认 commit，但事务类调用可传 commit=False，
    以便与 CapabilityEvidence 在同一事务内原子创建/撤销。
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 转换 ----------

    @staticmethod
    def _from_row(row) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d["is_active"] = bool(d.get("is_active"))
        return d

    # ---------- practice_topic_evidence ----------

    def create(
        self,
        *,
        project_id: int,
        topic_id: int,
        knowledge_point_id: int,
        usage_description: str,
        commit: bool = True,
    ) -> dict:
        usage_description = (usage_description or "").strip()
        if not usage_description:
            raise ValueError("项目使用说明不能为空")
        cur = self.conn.execute(
            "INSERT INTO practice_topic_evidence "
            "(project_id, topic_id, knowledge_point_id, usage_description,"
            " is_active, created_at, revoked_at, revocation_reason) "
            "VALUES (?, ?, ?, ?, 1, ?, NULL, NULL)",
            (int(project_id), int(topic_id), int(knowledge_point_id),
             usage_description, now_iso()),
        )
        if commit:
            self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, evidence_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM practice_topic_evidence WHERE id = ?",
            (int(evidence_id),),
        ).fetchone()
        return self._from_row(row)

    def get_active_by_project_topic(
        self, project_id: int, topic_id: int
    ) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM practice_topic_evidence "
            "WHERE project_id = ? AND topic_id = ? AND is_active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (int(project_id), int(topic_id)),
        ).fetchone()
        return self._from_row(row)

    def list_by_project(
        self, project_id: int, active_only: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM practice_topic_evidence WHERE project_id = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY id ASC"
        rows = self.conn.execute(sql, (int(project_id),)).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_topic(
        self, topic_id: int, active_only: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM practice_topic_evidence WHERE topic_id = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY id ASC"
        rows = self.conn.execute(sql, (int(topic_id),)).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_kp(
        self, knowledge_point_id: int, active_only: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM practice_topic_evidence WHERE knowledge_point_id = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY id ASC"
        rows = self.conn.execute(sql, (int(knowledge_point_id),)).fetchall()
        return [self._from_row(r) for r in rows]

    def revoke(
        self, evidence_id: int, reason: str = "", commit: bool = True
    ) -> Optional[dict]:
        """撤销（不物理删除，保留历史行）。"""
        if self.get(evidence_id) is None:
            return None
        self.conn.execute(
            "UPDATE practice_topic_evidence SET is_active = 0, revoked_at = ?,"
            " revocation_reason = ? WHERE id = ?",
            (now_iso(), reason or "", int(evidence_id)),
        )
        if commit:
            self.conn.commit()
        return self.get(evidence_id)

    def has_active_topic_reference(self, project_id: int, topic_id: int) -> bool:
        return self.get_active_by_project_topic(project_id, topic_id) is not None

    def has_any_evidence(self, project_id: int) -> bool:
        """含 revoked 历史行：决定项目能否物理删除。"""
        row = self.conn.execute(
            "SELECT 1 FROM practice_topic_evidence WHERE project_id = ? LIMIT 1",
            (int(project_id),),
        ).fetchone()
        return row is not None

    def count_active_by_project(self, project_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence "
            "WHERE project_id = ? AND is_active = 1",
            (int(project_id),),
        ).fetchone()
        return int(row[0])

    def count_active_by_topic(self, topic_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence "
            "WHERE topic_id = ? AND is_active = 1",
            (int(topic_id),),
        ).fetchone()
        return int(row[0])

    def count_active_by_kp(self, knowledge_point_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence "
            "WHERE knowledge_point_id = ? AND is_active = 1",
            (int(knowledge_point_id),),
        ).fetchone()
        return int(row[0])

    def count_created_between(self, start: str, end: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence "
            "WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        ).fetchone()
        return int(row[0])

    def list_active_created(self) -> list[dict]:
        """active evidence 的 (topic_id, created_at) 轻量列表（月总结用）。"""
        rows = self.conn.execute(
            "SELECT topic_id, created_at FROM practice_topic_evidence "
            "WHERE is_active = 1 ORDER BY id ASC"
        ).fetchall()
        return [{"topic_id": int(r[0]), "created_at": r[1]} for r in rows]

    # ---------- evidence ↔ outputs ----------

    def add_output(
        self, evidence_id: int, output_id: int, commit: bool = True
    ) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO practice_topic_evidence_outputs "
            "(evidence_id, output_id, created_at) VALUES (?, ?, ?)",
            (int(evidence_id), int(output_id), now_iso()),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def list_output_ids(self, evidence_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT output_id FROM practice_topic_evidence_outputs "
            "WHERE evidence_id = ? ORDER BY output_id",
            (int(evidence_id),),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def list_outputs(self, evidence_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT o.* FROM practice_outputs o "
            "JOIN practice_topic_evidence_outputs l ON l.output_id = o.id "
            "WHERE l.evidence_id = ? ORDER BY o.id ASC",
            (int(evidence_id),),
        ).fetchall()
        return [PracticeOutputRepository._from_row(r) for r in rows]

    def has_active_output_reference(self, output_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM practice_topic_evidence_outputs l "
            "JOIN practice_topic_evidence e ON e.id = l.evidence_id "
            "WHERE l.output_id = ? AND e.is_active = 1 LIMIT 1",
            (int(output_id),),
        ).fetchone()
        return row is not None

    def has_any_output_reference(self, output_id: int) -> bool:
        """历史完整性：只要 **曾经** 被任意 evidence 使用过（active/revoked）。"""
        row = self.conn.execute(
            "SELECT 1 FROM practice_topic_evidence_outputs "
            "WHERE output_id = ? LIMIT 1",
            (int(output_id),),
        ).fetchone()
        return row is not None

    def has_any_topic_reference(self, project_id: int, topic_id: int) -> bool:
        """历史完整性：project/topic 只要出现过任意 evidence（含 revoked）。"""
        row = self.conn.execute(
            "SELECT 1 FROM practice_topic_evidence "
            "WHERE project_id = ? AND topic_id = ? LIMIT 1",
            (int(project_id), int(topic_id)),
        ).fetchone()
        return row is not None

    # ---------- knowledge_points（Phase 5 确定性路径） ----------

    def find_knowledge_point_by_topic(self, topic_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE topic_id = ? "
            "ORDER BY id ASC LIMIT 1",
            (int(topic_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_knowledge_point_by_name_route(
        self, name: str, route_id: int | None
    ) -> Optional[dict]:
        if route_id is None:
            row = self.conn.execute(
                "SELECT * FROM knowledge_points WHERE name = ? "
                "AND route_id IS NULL ORDER BY id ASC LIMIT 1",
                (name,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM knowledge_points WHERE name = ? AND route_id = ? "
                "ORDER BY id ASC LIMIT 1",
                (name, int(route_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def link_knowledge_point_to_topic(
        self, knowledge_point_id: int, topic_id: int, commit: bool = False
    ) -> Optional[dict]:
        """只补 topic 关系；不改 mastery / review / assessment。"""
        self.conn.execute(
            "UPDATE knowledge_points SET topic_id = ?, updated_at = ? WHERE id = ?",
            (int(topic_id), now_iso(), int(knowledge_point_id)),
        )
        if commit:
            self.conn.commit()
        return self.find_knowledge_point_by_topic(topic_id)

    def insert_topic_linked_knowledge_point(
        self, *, topic_id: int, route_id: int, name: str,
        description: str = "", commit: bool = False,
    ) -> dict:
        """确定性创建 topic-linked KP：mastery=0 / 无 assessment / 无 review。"""
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO knowledge_points "
            "(topic_id, route_id, name, description, first_learned_at,"
            " last_assessed_at, mastery_estimate, review_count, next_review_date,"
            " interval_days, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, 0.0, 0, NULL, 0, ?, ?)",
            (int(topic_id), int(route_id), name, description or "", ts, ts),
        )
        if commit:
            self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
