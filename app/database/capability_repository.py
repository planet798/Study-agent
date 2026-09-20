"""Capability Evidence 数据访问层（Phase 3）。

所有 SQL 集中在此，不放到 UI / Service。
禁止物理 DELETE；撤销用 is_active=0 + revoked_at + revocation_reason。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..services.capability import is_valid_evidence_level
from ..utils.date_utils import now_iso


class CapabilityEvidenceRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 转换 ----------

    @staticmethod
    def _from_row(row) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d["is_active"] = bool(d.get("is_active"))
        try:
            d["details"] = json.loads(d.get("details_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            d["details"] = {}
        return d

    # ---------- 写 ----------

    def create_or_update_by_key(
        self,
        *,
        knowledge_point_id: int,
        capability_level: int,
        evidence_type: str,
        evidence_key: str,
        source_task_id: int | None = None,
        assessment_attempt_id: int | None = None,
        learning_outcome_id: int | None = None,
        description: str = "",
        details: dict | None = None,
    ) -> dict:
        """幂等写入：同一 evidence_key 只保留“最强”证据，绝不降级。"""
        if not is_valid_evidence_level(capability_level):
            raise ValueError(f"非法 capability_level: {capability_level!r}")
        if not evidence_key:
            raise ValueError("evidence_key 不能为空")
        details_json = json.dumps(details or {}, ensure_ascii=False)
        existing = self.get_by_key(evidence_key)
        if existing is not None:
            # 只升级，不降级；同 level 时补充 details（不覆盖更强证据）
            if int(capability_level) > int(existing["capability_level"]):
                self.conn.execute(
                    "UPDATE capability_evidence SET capability_level = ?, "
                    "evidence_type = ?, source_task_id = COALESCE(?, source_task_id),"
                    " assessment_attempt_id = COALESCE(?, assessment_attempt_id),"
                    " learning_outcome_id = COALESCE(?, learning_outcome_id),"
                    " description = ?, details_json = ?, is_active = 1, "
                    " revoked_at = NULL, revocation_reason = NULL "
                    "WHERE id = ?",
                    (
                        int(capability_level), evidence_type, source_task_id,
                        assessment_attempt_id, learning_outcome_id,
                        description or "", details_json, int(existing["id"]),
                    ),
                )
                self.conn.commit()
            return self.get(existing["id"])
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO capability_evidence "
            "(knowledge_point_id, capability_level, evidence_type, evidence_key,"
            " source_task_id, assessment_attempt_id, learning_outcome_id,"
            " description, details_json, is_active, created_at,"
            " revoked_at, revocation_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL)",
            (
                int(knowledge_point_id), int(capability_level), evidence_type,
                evidence_key, source_task_id, assessment_attempt_id,
                learning_outcome_id, description or "", details_json, ts,
            ),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def revoke(
        self, evidence_id: int, reason: str = ""
    ) -> Optional[dict]:
        """撤销证据（不物理删除）。"""
        comp = self.get(evidence_id)
        if comp is None:
            return None
        self.conn.execute(
            "UPDATE capability_evidence SET is_active = 0, revoked_at = ?, "
            "revocation_reason = ? WHERE id = ?",
            (now_iso(), reason or "", int(evidence_id)),
        )
        self.conn.commit()
        return self.get(evidence_id)

    # ---------- 读 ----------

    def get(self, evidence_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM capability_evidence WHERE id = ?",
            (int(evidence_id),),
        ).fetchone()
        return self._from_row(row)

    def get_by_key(self, evidence_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM capability_evidence WHERE evidence_key = ?",
            (evidence_key,),
        ).fetchone()
        return self._from_row(row)

    def list_by_kp(
        self, knowledge_point_id: int, active_only: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM capability_evidence WHERE knowledge_point_id = ?"
        args: list = [int(knowledge_point_id)]
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY capability_level ASC, id ASC"
        return [self._from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def list_active_by_kp(self, knowledge_point_id: int) -> list[dict]:
        return self.list_by_kp(knowledge_point_id, active_only=True)

    def max_level_by_kp(self, knowledge_point_id: int) -> int:
        row = self.conn.execute(
            "SELECT MAX(capability_level) FROM capability_evidence "
            "WHERE knowledge_point_id = ? AND is_active = 1",
            (int(knowledge_point_id),),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def list_by_route_via_kp(self, route_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.* FROM capability_evidence c "
            "JOIN knowledge_points kp ON kp.id = c.knowledge_point_id "
            "WHERE kp.route_id = ? AND c.is_active = 1 "
            "ORDER BY c.capability_level ASC, c.id ASC",
            (int(route_id),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count_by_level_for_route(self, route_id: int) -> dict[int, int]:
        rows = self.conn.execute(
            "SELECT c.capability_level, COUNT(*) FROM capability_evidence c "
            "JOIN knowledge_points kp ON kp.id = c.knowledge_point_id "
            "WHERE kp.route_id = ? AND c.is_active = 1 "
            "GROUP BY c.capability_level",
            (int(route_id),),
        ).fetchall()
        out = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for level, n in rows:
            out[int(level)] = int(n)
        return out

    def count_active(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0])

    def list_all(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM capability_evidence"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY id ASC"
        return [self._from_row(r) for r in self.conn.execute(sql).fetchall()]
