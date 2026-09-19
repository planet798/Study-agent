"""每日 JD 技术汇总数据访问层（Step 4）。

两张表：
- jd_daily_summaries    ：某天某目标的岗位样本（sample_count + 原始文本）
- jd_daily_skill_stats  ：该样本的技能统计（mention/must/plus + 原始技能名）

设计要点：
- UNIQUE(summary_date, target_type)：同一天同一目标只保留一份；
  重新整理走 upsert（update summary + replace stats），绝不叠加 double count。
- upsert 全程在一个事务里完成：任一步失败 → rollback，绝不留下半份数据。
- skill_id 允许为空，用于保存“未匹配技能”（raw_skill_name 永远保留）。
"""

from __future__ import annotations

import sqlite3

from ..utils.date_utils import now_iso

_STAT_COLUMNS = (
    "summary_id",
    "skill_id",
    "raw_skill_name",
    "mention_count",
    "must_count",
    "plus_count",
)


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class JdDailySummaryRepository:
    """jd_daily_summaries / jd_daily_skill_stats 的读写。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- 读取 ----------

    def get_summary(self, summary_date: str, target_type: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jd_daily_summaries "
            "WHERE summary_date = ? AND target_type = ?",
            (summary_date, target_type),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["stats"] = self.list_stats(out["id"])
        return out

    def get_summary_by_id(self, summary_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jd_daily_summaries WHERE id = ?", (summary_id,)
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["stats"] = self.list_stats(out["id"])
        return out

    def list_stats(self, summary_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jd_daily_skill_stats WHERE summary_id = ? "
            "ORDER BY id ASC",
            (summary_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_unmatched_stats(self) -> list[dict]:
        """所有 skill_id 为空的历史未匹配技能行（用于 alias 修复重试）。"""
        rows = self.conn.execute(
            "SELECT * FROM jd_daily_skill_stats WHERE skill_id IS NULL "
            "ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_stat_skill_id(self, stat_id: int, skill_id: int | None) -> bool:
        """只更新某行的 skill_id；不碰 raw_skill_name / mention_count / summary_id。"""
        cur = self.conn.execute(
            "UPDATE jd_daily_skill_stats SET skill_id = ? WHERE id = ?",
            (skill_id, stat_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def list_summaries(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        target_type: str | None = None,
    ) -> list[dict]:
        """按日期范围列出汇总（含 stats）。范围闭区间。"""
        conds, args = [], []
        if start_date:
            conds.append("summary_date >= ?")
            args.append(start_date)
        if end_date:
            conds.append("summary_date <= ?")
            args.append(end_date)
        if target_type:
            conds.append("target_type = ?")
            args.append(target_type)
        sql = "SELECT * FROM jd_daily_summaries"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY summary_date ASC, id ASC"
        rows = self.conn.execute(sql, tuple(args)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = self.list_stats(d["id"])
            out.append(d)
        return out

    def list_stats_rows(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        target_type: str | None = None,
    ) -> list[dict]:
        """联表返回窗口内的 stats 行（含 summary_date / target_type）。"""
        conds, args = [], []
        if start_date:
            conds.append("s.summary_date >= ?")
            args.append(start_date)
        if end_date:
            conds.append("s.summary_date <= ?")
            args.append(end_date)
        if target_type:
            conds.append("s.target_type = ?")
            args.append(target_type)
        sql = (
            "SELECT st.*, s.summary_date AS summary_date, "
            "s.target_type AS target_type, s.sample_count AS sample_count "
            "FROM jd_daily_skill_stats st "
            "JOIN jd_daily_summaries s ON s.id = st.summary_id"
        )
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY s.summary_date ASC, st.id ASC"
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    # ---------- 写入 ----------

    def upsert_summary(
        self,
        summary_date: str,
        target_type: str,
        sample_count: int,
        raw_text: str = "",
        note: str = "",
        stats: list[dict] | None = None,
    ) -> dict:
        """原子地创建/更新一份每日汇总（并整体替换其 skill stats）。

        stats 每项：{skill_id, raw_skill_name, mention_count,
                     must_count, plus_count}
        """
        ts = now_iso()
        try:
            existing = self.conn.execute(
                "SELECT id FROM jd_daily_summaries "
                "WHERE summary_date = ? AND target_type = ?",
                (summary_date, target_type),
            ).fetchone()
            if existing is not None:
                summary_id = existing["id"]
                self.conn.execute(
                    "UPDATE jd_daily_summaries SET sample_count = ?, "
                    "raw_text = ?, note = ?, updated_at = ? WHERE id = ?",
                    (sample_count, raw_text or "", note or "", ts, summary_id),
                )
                self.conn.execute(
                    "DELETE FROM jd_daily_skill_stats WHERE summary_id = ?",
                    (summary_id,),
                )
            else:
                cur = self.conn.execute(
                    "INSERT INTO jd_daily_summaries "
                    "(summary_date, target_type, sample_count, raw_text, note,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (summary_date, target_type, sample_count, raw_text or "",
                     note or "", ts, ts),
                )
                summary_id = cur.lastrowid

            for st in stats or []:
                self.conn.execute(
                    "INSERT INTO jd_daily_skill_stats "
                    "(summary_id, skill_id, raw_skill_name, mention_count,"
                    " must_count, plus_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        summary_id,
                        st.get("skill_id"),
                        st.get("raw_skill_name") or "",
                        int(st.get("mention_count") or 0),
                        int(st.get("must_count") or 0),
                        int(st.get("plus_count") or 0),
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_summary(summary_date, target_type)

    def delete_summary(self, summary_date: str, target_type: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM jd_daily_summaries "
            "WHERE summary_date = ? AND target_type = ?",
            (summary_date, target_type),
        )
        # 显式删除子表（不依赖 FK 级联设置），保证无悬挂行
        if cur.rowcount:
            self.conn.execute(
                "DELETE FROM jd_daily_skill_stats WHERE summary_id NOT IN "
                "(SELECT id FROM jd_daily_summaries)"
            )
        self.conn.commit()
        return bool(cur.rowcount)


class JdSkillCandidateRepository:
    """jd_skill_candidates 的读写（JD 新技能候选池）。

    - 候选不是正式 skill：只有用户确认（accept）后才创建 skills 记录。
    - raw_names 用 JSON 数组保存，保留用户原始写法。
    - status ∈ {candidate, accepted, ignored}；upsert 不会覆盖已处理的状态。
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        try:
            import json

            names = json.loads(d.get("raw_names") or "[]")
        except (TypeError, ValueError):
            names = []
        d["raw_names"] = names if isinstance(names, list) else []
        return d

    def get(self, candidate_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jd_skill_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        return self._decode(row)

    def get_by_canonical(self, canonical_name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jd_skill_candidates WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
        return self._decode(row)

    def list_all(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM jd_skill_candidates WHERE status = ? "
                "ORDER BY frequency_30d DESC, mention_count_30d DESC, id ASC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jd_skill_candidates "
                "ORDER BY frequency_30d DESC, mention_count_30d DESC, id ASC"
            ).fetchall()
        return [self._decode(r) for r in rows]

    def upsert_candidate(
        self,
        canonical_name: str,
        raw_names: list[str],
        mention_count_30d: int,
        sample_count_30d: int,
        frequency_30d: float,
        first_seen: str | None = None,
        last_seen: str | None = None,
    ) -> dict:
        """幂等写入候选；已存在时只更新统计，绝不覆盖 status。"""
        import json

        from ..utils.date_utils import now_iso

        name = (canonical_name or "").strip()
        if not name:
            raise ValueError("候选规范名不能为空")
        raw_json = json.dumps(sorted({r for r in raw_names if r}), ensure_ascii=False)
        existing = self.get_by_canonical(name)
        ts = now_iso()
        if existing is not None:
            first = min(
                [x for x in (existing.get("first_seen"), first_seen) if x]
                or [ts]
            )
            last = max(
                [x for x in (existing.get("last_seen"), last_seen) if x]
                or [ts]
            )
            self.conn.execute(
                "UPDATE jd_skill_candidates SET raw_names = ?, "
                "mention_count_30d = ?, sample_count_30d = ?, frequency_30d = ?, "
                "first_seen = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (raw_json, int(mention_count_30d), int(sample_count_30d),
                 float(frequency_30d), first, last, ts, existing["id"]),
            )
            self.conn.commit()
            return self.get(existing["id"])
        cur = self.conn.execute(
            "INSERT INTO jd_skill_candidates "
            "(canonical_name, raw_names, mention_count_30d, sample_count_30d, "
            " frequency_30d, first_seen, last_seen, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)",
            (name, raw_json, int(mention_count_30d), int(sample_count_30d),
             float(frequency_30d), first_seen or ts, last_seen or ts, ts, ts),
        )
        self.conn.commit()
        return self.get(cur.lastrowid)

    def set_status(
        self, candidate_id: int, status: str,
        accepted_skill_name: str | None = None,
        commit: bool = True,
    ) -> dict | None:
        if status not in ("candidate", "accepted", "ignored"):
            raise ValueError(f"未知候选状态: {status}")
        from ..utils.date_utils import now_iso

        if accepted_skill_name is not None:
            self.conn.execute(
                "UPDATE jd_skill_candidates SET status = ?, "
                "accepted_skill_name = ?, updated_at = ? WHERE id = ?",
                (status, accepted_skill_name, now_iso(), candidate_id),
            )
        else:
            self.conn.execute(
                "UPDATE jd_skill_candidates SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (status, now_iso(), candidate_id),
            )
        if commit:
            self.conn.commit()
        return self.get(candidate_id)
