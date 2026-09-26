"""验收与知识点数据访问层（Repository）。

管理 v2/v3 迁移创建的两张表：
- knowledge_points    ：知识点级档案（掌握度、复习计数、下次复习日等）
- assessment_attempts ：一次验收的题目 / 用户答案 / AI 判题结果

只负责 CRUD，业务规则（校验、判题、掌握度更新）在 service 层。
返回普通 dict（sqlite3.Row 展开），方便 JSON 相关字段直接读写。
"""

from __future__ import annotations

import re
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
    "route_id",
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


def _normalize_kp_name(name: str) -> str:
    """知识点名称规范化：去首尾空白 + 折叠连续空白（含全角空格）；不做模糊匹配。"""
    return re.sub(r"\s+", " ", (name or "").strip())


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
        route_id: int | None = None,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("知识点名不能为空")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO knowledge_points "
            "(topic_id, route_id, name, description, first_learned_at,"
            " last_assessed_at, mastery_estimate, review_count, next_review_date,"
            " interval_days, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, 0.0, 0, NULL, 0, ?, ?)",
            (topic_id, route_id, name, description or "", ts, ts),
        )
        self.conn.commit()
        return self.get_knowledge_point(cur.lastrowid)

    def get_knowledge_point(self, knowledge_point_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?", (knowledge_point_id,)
        ).fetchone()
        return _row(row)

    def get_knowledge_point_by_name_and_route(
        self, name: str, route_id: int | None
    ) -> dict | None:
        """按 (规范化 name, route_id) 精确查找（Phase C 多路线唯一性）。

        注意：正式 knowledge_point identity 必须使用 kp_id / topic_id / route_id；
        **禁止**任何全局按 name 的 identity lookup（已删除旧 get_knowledge_point_by_name）。
        """
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
        return _row(row)

    def get_knowledge_point_by_topic(self, topic_id: int) -> dict | None:
        """按 study_topic.id 取该主题唯一的知识点（不存在返回 None）。"""
        if topic_id is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM knowledge_points WHERE topic_id = ? "
            "ORDER BY id ASC LIMIT 1",
            (topic_id,),
        ).fetchone()
        return _row(row)

    def get_or_create_knowledge_point_for_topic(
        self,
        topic_id: int,
        name: str,
        description: str = "",
        route_id: int | None = None,
    ) -> dict:
        """幂等地取得/创建某个 study_topic 对应的唯一知识点。

        一个 topic 只允许对应一个 knowledge_point。幂等保证：
        1. 先按 topic_id 命中直接复用，不修改任何 mastery / 复习字段；
        2. 未命中再按 (name, route_id) 查找（旧数据可能已有同路线同名但 topic_id
           为空的 kp，此时复用并补上 topic_id，只改关系）；不同路线的同名 kp
           不会被误用；
        3. 仍未命中才 INSERT；由 UNIQUE(name, route_id) 兜底，重复触发时回查返回。

        所有分支都只写 topic_id / route_id（以及 updated_at），绝不伪造验收证据。
        """
        if topic_id is None:
            raise ValueError("topic_id 不能为空")
        existing = self.get_knowledge_point_by_topic(topic_id)
        if existing is not None:
            # 只补 route 关系（不覆盖任何验收证据）
            if route_id is not None and existing.get("route_id") is None:
                return self.update_knowledge_point(
                    existing["id"], route_id=route_id
                )
            return existing
        clean = (name or "").strip()
        if not clean:
            raise ValueError("知识点名不能为空")
        by_name = self.get_knowledge_point_by_name_and_route(clean, route_id)
        if by_name is not None:
            if by_name.get("topic_id") is None:
                return self.update_knowledge_point(
                    by_name["id"], topic_id=topic_id, route_id=route_id
                )
            return by_name
        try:
            return self.create_knowledge_point(
                clean, description, topic_id, route_id=route_id
            )
        except sqlite3.IntegrityError:
            again = self.get_knowledge_point_by_topic(topic_id) or \
                self.get_knowledge_point_by_name_and_route(clean, route_id)
            if again is not None:
                return again
            raise

    def get_or_create_manual_knowledge_point(
        self, name: str, description: str = "", route_id: int | None = None
    ) -> dict:
        """幂等地取得/创建用户手写的临时知识点（topic_id 保持 NULL）。

        用于“今天主动学一个正式知识点，但当前没有对应 study_topic”的场景：
        - 不创建/不污染 study_phases / study_topics；
        - 唯一语义 = 规范化名称 + route_id（Phase C）：
          同一路线同名复用；不同路线同名允许不同 kp；
        - 不做 contains 模糊匹配；不覆盖任何验收证据；
        - route_id=None 保持未分类兼容。
        """
        clean = _normalize_kp_name(name)
        if not clean:
            raise ValueError("知识点名不能为空")
        existing = self.get_knowledge_point_by_name_and_route(clean, route_id)
        if existing is not None:
            return existing
        try:
            return self.create_knowledge_point(
                clean, description, None, route_id=route_id
            )
        except sqlite3.IntegrityError:
            again = self.get_knowledge_point_by_name_and_route(clean, route_id)
            if again is not None:
                return again
            raise

    def list_knowledge_points(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM knowledge_points ORDER BY id ASC"
        ).fetchall()
        return _rows(rows)

    # ---------- Phase E：route-scoped 查询（kp 是知识身份核心） ----------

    def list_knowledge_points_by_route(self, route_id: int | None) -> list[dict]:
        """某路线的全部知识点（route_id=None → 未分类）。"""
        if route_id is None:
            rows = self.conn.execute(
                "SELECT * FROM knowledge_points WHERE route_id IS NULL "
                "ORDER BY id ASC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM knowledge_points WHERE route_id = ? "
                "ORDER BY id ASC",
                (int(route_id),),
            ).fetchall()
        return _rows(rows)

    def list_assessed_by_route(self, route_id: int | None) -> list[dict]:
        """某路线中已有真实验收证据（last_assessed_at 非空）的知识点。"""
        return [
            kp for kp in self.list_knowledge_points_by_route(route_id)
            if kp.get("last_assessed_at")
        ]

    def list_mastered_by_route(
        self, route_id: int | None, threshold: float = 0.85
    ) -> list[dict]:
        """某路线中已达到 mastered 阈值的知识点（需有验收证据）。"""
        return [
            kp for kp in self.list_assessed_by_route(route_id)
            if float(kp.get("mastery_estimate") or 0.0) >= float(threshold)
        ]

    def list_weak_by_route(
        self, route_id: int | None, weak_threshold: float = 0.5
    ) -> list[dict]:
        """某路线中的薄弱知识点（低掌握或有 weak_points，仅真实证据）。"""
        from ..services.knowledge_evidence import (
            _attempt_weak_points,
            _latest_judged_attempt,
        )

        out = []
        for kp in self.list_assessed_by_route(route_id):
            if float(kp.get("mastery_estimate") or 0.0) < float(weak_threshold):
                out.append(kp)
                continue
            attempt = _latest_judged_attempt(self, kp["id"])
            if _attempt_weak_points(attempt):
                out.append(kp)
        return out

    def ensure_knowledge_point_route_consistency(self) -> int:
        """确定性修复：topic-linked kp 的 route 必须等于 topic 的 route。

        - 只处理 topic_id 非空且可推导 route 的 kp；
        - manual kp（topic_id=NULL）不猜、不动；
        - 幂等；返回修复条数。
        """
        fixed = 0
        for kp in self.list_knowledge_points():
            topic_id = kp.get("topic_id")
            if topic_id is None:
                continue
            row = self.conn.execute(
                "SELECT p.route_id FROM study_topics t "
                "JOIN study_phases ph ON ph.id = t.phase_id "
                "JOIN study_plans  p  ON p.id  = ph.plan_id "
                "WHERE t.id = ?",
                (int(topic_id),),
            ).fetchone()
            derived = row[0] if row and row[0] is not None else None
            if derived is not None and kp.get("route_id") != derived:
                self.update_knowledge_point(kp["id"], route_id=int(derived))
                fixed += 1
        return fixed

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

    def find_pending_attempt_for_task(self, task_id: int) -> dict | None:
        """查找某任务最新一条尚未判题的验收记录（用于 UI 继续验收）。"""
        row = self.conn.execute(
            "SELECT * FROM assessment_attempts WHERE task_id = ? "
            "AND judge_status = 'pending' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return _row(row)

    # ---------- review_schedule ----------

    def get_review_schedule(self, schedule_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM review_schedule WHERE id = ?", (schedule_id,)
        ).fetchone()
        return _row(row)

    def list_review_schedules_for_kp(self, knowledge_point_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_schedule WHERE knowledge_point_id = ? "
            "ORDER BY id ASC",
            (knowledge_point_id,),
        ).fetchall()
        return _rows(rows)

    def list_review_schedules_by_date(self, date_str: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_schedule WHERE scheduled_date = ? "
            "ORDER BY id ASC",
            (date_str,),
        ).fetchall()
        return _rows(rows)

    def find_pending_review_for_task(self, task_id: int) -> dict | None:
        """查找某复习任务对应的未完成调度（用于完成后关闭）。"""
        row = self.conn.execute(
            "SELECT * FROM review_schedule WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return _row(row)
