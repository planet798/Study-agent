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
