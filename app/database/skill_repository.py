"""技能 / JD / 学习成果 数据访问层（Phase A）。

对应 v6 迁移新增的三张表：
- skills            : 技能实体
- jds               : 真实 JD 记录（保留原文）
- learning_outcomes : 学习成果 → 简历素材底座

只负责 CRUD 与基本校验；确定性优先级计算在 app/services/skill_service.py。
JSON 字段（jd_frequency / prerequisites / linked_topics / parsed /
tech_stack / metrics / resume_keywords）在此层做编解码，避免把字符串细节
泄漏到 service。
"""

from __future__ import annotations

import datetime
import json

# 技能 tier 与状态的可选值（集中定义，供校验与文档使用）
TIERS = ("S", "A", "B", "C")
STATUSES = ("not_started", "learning", "mastered", "deferred")
CATEGORIES = ("core", "fundamental", "recsys", "llm", "infra",
              "low_priority", "connector", "other")

# learning_outcomes 的 kind 可选值
OUTCOME_KINDS = ("note", "topic", "project", "experiment", "course")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _enc(value) -> str:
    """把任意 JSON 兼容对象编码成字符串。"""
    return json.dumps(value, ensure_ascii=False)


def _dec(raw: str | None, fallback):
    """把存储字符串解码回对象；空/非法时返回 fallback。"""
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _validate_tier(tier: str) -> str:
    tier = (tier or "").strip().upper()
    if tier not in TIERS:
        raise ValueError(f"非法 tier: {tier!r}，只能是 {TIERS}")
    return tier


def _validate_status(status: str) -> str:
    status = (status or "").strip()
    if status not in STATUSES:
        raise ValueError(f"非法 status: {status!r}，只能是 {STATUSES}")
    return status


class SkillRepository:
    """skills 表读写。"""

    def __init__(self, conn):
        self.conn = conn

    # ---------- 写 ----------

    def create(
        self,
        name: str,
        tier: str = "C",
        category: str = "core",
        status: str = "not_started",
        mastery_ref: str = "",
        jd_frequency: dict | None = None,
        prerequisites: list[str] | None = None,
        linked_topics: list[int] | None = None,
        shared_connector: bool = False,
        priority_score: float = 0.0,
        commit: bool = True,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("技能名称不能为空")
        tier = _validate_tier(tier)
        status = _validate_status(status)
        jd = jd_frequency or {"must": 0, "plus": 0, "total_jds": 0}
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO skills (name, tier, category, status, mastery_ref,"
            " jd_frequency, priority_score, prerequisites, linked_topics,"
            " shared_connector, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name, tier, category, status, mastery_ref or "",
                _enc(jd), float(priority_score),
                _enc(prerequisites or []),
                _enc([int(t) for t in (linked_topics or [])]),
                int(bool(shared_connector)), now, now,
            ),
        )
        if commit:
            self.conn.commit()
        return self.get(cur.lastrowid)

    def create_many(self, specs: list[dict]) -> list[dict]:
        """批量创建（供测试 / 种子）。重复 name 会抛 sqlite3.IntegrityError。"""
        out = []
        for s in specs:
            out.append(self.create(**s))
        return out

    # ---------- 读 ----------

    def get(self, skill_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM skills WHERE id = ?", (int(skill_id),)
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_name(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM skills WHERE name = ?", ((name or "").strip(),)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skills ORDER BY priority_score DESC, name ASC"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_tier(self, tier: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skills WHERE tier = ? "
            "ORDER BY priority_score DESC, name ASC",
            (_validate_tier(tier),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    # ---------- 更新 ----------

    def update(self, skill_id: int, **fields) -> dict | None:
        """就地更新（允许字段名与 skills 列一致）；integer 列做 int() 归一。"""
        if "tier" in fields:
            fields["tier"] = _validate_tier(fields["tier"])
        if "status" in fields:
            fields["status"] = _validate_status(fields["status"])
        if "jd_frequency" in fields:
            fields["jd_frequency"] = _enc(fields["jd_frequency"])
        if "prerequisites" in fields:
            fields["prerequisites"] = _enc(fields["prerequisites"])
        if "linked_topics" in fields:
            fields["linked_topics"] = _enc(
                [int(t) for t in (fields["linked_topics"] or [])]
            )
        if "shared_connector" in fields:
            fields["shared_connector"] = int(bool(fields["shared_connector"]))
        fields["updated_at"] = _now()
        allowed = {
            "name", "tier", "category", "status", "mastery_ref",
            "jd_frequency", "priority_score", "prerequisites",
            "linked_topics", "shared_connector", "updated_at",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get(skill_id)
        set_clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE skills SET {set_clause} WHERE id = ?",
            (*sets.values(), int(skill_id)),
        )
        self.conn.commit()
        return self.get(skill_id)

    def upsert_by_name(self, name: str, **fields) -> dict:
        """按名称幂等写入：已存在则只更新非受保护字段，否则新建。

        受保护字段（不因 seed 覆盖）：
        status / mastery_ref / jd_frequency / priority_score（用户或系统维护的真实状态）。
        """
        existing = self.get_by_name(name)
        if existing is not None:
            protected = {"status", "mastery_ref", "jd_frequency", "priority_score"}
            self.update(existing["id"], **{
                k: v for k, v in fields.items() if k not in protected
            })
            return self.get(existing["id"])
        return self.create(name=name, **fields)

    def delete(self, skill_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM skills WHERE id = ?", (int(skill_id),))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- 转换 ----------

    @staticmethod
    def _from_row(row) -> dict:
        d = dict(row)
        d["jd_frequency"] = _dec(d.get("jd_frequency"),
                                 {"must": 0, "plus": 0, "total_jds": 0})
        d["prerequisites"] = _dec(d.get("prerequisites"), [])
        d["linked_topics"] = _dec(d.get("linked_topics"), [])
        d["shared_connector"] = bool(d.get("shared_connector"))
        d["priority_score"] = float(d.get("priority_score") or 0.0)
        return d


class JdRepository:
    """jds 表读写；raw_text 永远保留原文。"""

    def __init__(self, conn):
        self.conn = conn

    def create(
        self,
        company: str = "",
        title: str = "",
        direction: str = "",
        intern_requirement: str = "",
        raw_text: str = "",
        parsed: dict | None = None,
        uploaded_at: str | None = None,
        content_hash: str = "",
    ) -> dict:
        row = {
            "company": (company or "").strip(),
            "title": (title or "").strip(),
            "direction": (direction or "").strip(),
            "intern_requirement": (intern_requirement or "").strip(),
            "raw_text": raw_text or "",
            "parsed": _enc(parsed or {}),
            "uploaded_at": uploaded_at or datetime.date.today().isoformat(),
            "content_hash": (content_hash or "").strip(),
        }
        cur = self.conn.execute(
            "INSERT INTO jds (company, title, direction, intern_requirement,"
            " raw_text, parsed, uploaded_at, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["company"], row["title"], row["direction"],
                row["intern_requirement"], row["raw_text"], row["parsed"],
                row["uploaded_at"], row["content_hash"],
            ),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, jd_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jds WHERE id = ?", (int(jd_id),)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM jds ORDER BY id ASC").fetchall()
        return [self._from_row(r) for r in rows]

    def list_by_direction(self, direction: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jds WHERE direction = ? ORDER BY id ASC",
            ((direction or "").strip(),),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def get_by_content_hash(self, content_hash: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jds WHERE content_hash = ? "
            "ORDER BY id ASC LIMIT 1",
            ((content_hash or "").strip(),),
        ).fetchone()
        return self._from_row(row) if row else None

    def update(self, jd_id: int, **fields) -> dict | None:
        allowed = {"company", "title", "direction", "intern_requirement",
                   "raw_text", "parsed", "uploaded_at", "content_hash"}
        if "parsed" in fields:
            fields["parsed"] = _enc(fields["parsed"])
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get(jd_id)
        set_clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE jds SET {set_clause} WHERE id = ?",
            (*sets.values(), int(jd_id)),
        )
        self.conn.commit()
        return self.get(jd_id)

    def delete(self, jd_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM jds WHERE id = ?", (int(jd_id),))
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _from_row(row) -> dict:
        d = dict(row)
        d["parsed"] = _dec(d.get("parsed"), {})
        return d


class LearningOutcomeRepository:
    """learning_outcomes 表读写（学习成果 → 简历素材底座）。"""

    def __init__(self, conn):
        self.conn = conn

    def create(
        self,
        date: str = "",
        kind: str = "note",
        title: str = "",
        content: str = "",
        tech_stack: list[str] | None = None,
        dataset: str = "",
        metrics: dict | None = None,
        git_commit: str = "",
        github_url: str = "",
        resume_keywords: list[str] | None = None,
        linked_kp_id: int | None = None,
        linked_topic_id: int | None = None,
        task_id: int | None = None,
        source_attempt_id: int | None = None,
    ) -> dict:
        date = (date or "").strip() or datetime.date.today().isoformat()
        kind = (kind or "note").strip()
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO learning_outcomes (date, kind, title, content,"
            " tech_stack, dataset, metrics, git_commit, github_url,"
            " resume_keywords, linked_kp_id, linked_topic_id, task_id,"
            " source_attempt_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                date, kind or "note", (title or "").strip(), content or "",
                _enc(tech_stack or []), (dataset or "").strip(),
                _enc(metrics or {}), (git_commit or "").strip(),
                (github_url or "").strip(), _enc(resume_keywords or []),
                int(linked_kp_id) if linked_kp_id is not None else None,
                int(linked_topic_id) if linked_topic_id is not None else None,
                int(task_id) if task_id is not None else None,
                int(source_attempt_id) if source_attempt_id is not None else None,
                now, now,
            ),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, outcome_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM learning_outcomes WHERE id = ?", (int(outcome_id),)
        ).fetchone()
        return self._from_row(row) if row else None

    def list_all(self, date: str | None = None,
                 kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM learning_outcomes"
        conds, args = [], []
        if date:
            conds.append("date = ?")
            args.append((date or "").strip())
        if kind:
            conds.append("kind = ?")
            args.append((kind or "").strip())
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY date DESC, id DESC"
        rows = self.conn.execute(sql, tuple(args)).fetchall()
        return [self._from_row(r) for r in rows]

    def get_by_task_id(self, task_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM learning_outcomes WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (int(task_id),),
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_source_attempt_id(self, attempt_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM learning_outcomes WHERE source_attempt_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (int(attempt_id),),
        ).fetchone()
        return self._from_row(row) if row else None

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM learning_outcomes").fetchone()[0])

    def update(self, outcome_id: int, **fields) -> dict | None:
        for key in ("tech_stack", "resume_keywords"):
            if key in fields:
                fields[key] = _enc(fields[key] or [])
        if "metrics" in fields:
            fields["metrics"] = _enc(fields["metrics"] or {})
        fields["updated_at"] = _now()
        allowed = {
            "date", "kind", "title", "content", "tech_stack", "dataset",
            "metrics", "git_commit", "github_url", "resume_keywords",
            "linked_kp_id", "linked_topic_id", "task_id", "source_attempt_id",
            "updated_at",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get(outcome_id)
        set_clause = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE learning_outcomes SET {set_clause} WHERE id = ?",
            (*sets.values(), int(outcome_id)),
        )
        self.conn.commit()
        return self.get(outcome_id)

    def delete(self, outcome_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM learning_outcomes WHERE id = ?", (int(outcome_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _from_row(row) -> dict:
        d = dict(row)
        d["tech_stack"] = _dec(d.get("tech_stack"), [])
        d["metrics"] = _dec(d.get("metrics"), {})
        d["resume_keywords"] = _dec(d.get("resume_keywords"), [])
        return d
