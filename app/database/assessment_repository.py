"""验收与知识点数据访问层（Repository）。

管理 v2/v3 迁移创建的两张表：
- knowledge_points    ：知识点级档案（掌握度、复习计数、下次复习日等）
- assessment_attempts ：一次验收的题目 / 用户答案 / AI 判题结果

只负责 CRUD，业务规则（校验、判题、掌握度更新）在 service 层。
返回普通 dict（sqlite3.Row 展开），方便 JSON 相关字段直接读写。
"""

from __future__ import annotations

import sqlite3

from ..utils.date_utils import now_iso

# 允许通过 update_attempt 修改的字段白名单
_ATTEMPT_UPDATABLE = (
    "answers_json",
    "ai_result_json",
    "mastery_estimate",
    "result_level",
    "weak_points_json",
    "judge_status",
    "judge_error",
)

# 允许通过 update_knowledge_point 修改的字段白名单
_KNOWLEDGE_POINT_UPDATABLE = (
    "topic_id",
    "name",
    "description",
    "first_learned_at",
    "last_assessed_at",
    "mastery_estimate",
    "review_count",
    "next_review_date",
    "interval_days",
    "updated_at",
)


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


class AssessmentRepository:
    """knowledge_points / assessment_attempts 的读写。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- knowledge_points ----------

    def create_knowledge_point(
        self,
        name: str,
        description: str = "",
        topic_id: int | None = None,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("知识点名不能为空")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO knowledge_points "
            "(topic_id, name, description, first_learned_at, last_assessed_at,"
            " mastery_estimate, review_count, next_review_date, interval_days,"
            " created_at, updated_at) "
            "VALUES (?, ?, ?, NULL, NULL, 0.0, 0, NULL, 0, ?, ?)",
            (topic_id, name, description or "", ts, ts),
        )
        self.conn.commit()
        return self.get_knowledge_point(cur.lastrowid)

    def get_knowledge_point(self, knowledge_point_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?", (knowledge_point_id,)
        ).fetchone()
        return _row(row)

    def get_knowledge_point_by_name(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE name = ?", (name,)
        ).fetchone()
        return _row(row)

    def list_knowledge_points(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM knowledge_points ORDER BY id ASC"
        ).fetchall()
        return _rows(rows)

    def update_knowledge_point(
        self, knowledge_point_id: int, **fields
    ) -> dict | None:
        allowed = {
            k: v for k, v in fields.items() if k in _KNOWLEDGE_POINT_UPDATABLE
        }
        if not allowed:
            return self.get_knowledge_point(knowledge_point_id)
        allowed["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in allowed)
        self.conn.execute(
            f"UPDATE knowledge_points SET {set_clause} WHERE id = ?",
            (*allowed.values(), knowledge_point_id),
        )
        self.conn.commit()
        return self.get_knowledge_point(knowledge_point_id)

    # ---------- assessment_attempts ----------

    def create_attempt(
        self,
        knowledge_point_id: int,
        questions_json: str,
        task_id: int | None = None,
    ) -> dict:
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO assessment_attempts "
            "(knowledge_point_id, task_id, questions_json, answers_json,"
            " ai_result_json, mastery_estimate, result_level, weak_points_json,"
            " judge_status, judge_error, created_at) "
            "VALUES (?, ?, ?, '', '', NULL, NULL, '', 'pending', '', ?)",
            (knowledge_point_id, task_id, questions_json, ts),
        )
        self.conn.commit()
        return self.get_attempt(cur.lastrowid)

    def get_attempt(self, attempt_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM assessment_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        return _row(row)

    def list_attempts_for_knowledge_point(
        self, knowledge_point_id: int
    ) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM assessment_attempts WHERE knowledge_point_id = ? "
            "ORDER BY id ASC",
            (knowledge_point_id,),
        ).fetchall()
        return _rows(rows)

    def list_attempts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM assessment_attempts ORDER BY id ASC"
        ).fetchall()
        return _rows(rows)

    def update_attempt(self, attempt_id: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in _ATTEMPT_UPDATABLE}
        if not allowed:
            return self.get_attempt(attempt_id)
        set_clause = ", ".join(f"{k} = ?" for k in allowed)
        self.conn.execute(
            f"UPDATE assessment_attempts SET {set_clause} WHERE id = ?",
            (*allowed.values(), attempt_id),
        )
        self.conn.commit()
        return self.get_attempt(attempt_id)
