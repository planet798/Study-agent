"""统计服务：周/月/趋势/习惯指标。

核心原则：统计事实全部由本地代码基于 tasks 表实时计算，
绝不交给 LLM 算数字。LLM 只负责解释。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date, timedelta

from ..database.repository import Task, TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_DONE, STATUS_NOT_DONE
from ..utils.date_utils import add_days, month_range, to_date, today, week_end, week_start


class StatsService:
    def __init__(self, repo: TaskRepository):
        self.repo = repo

    # ---------- 时段内任务 ----------

    def _tasks_between(self, start: str, end: str) -> list[Task]:
        return self.repo.list_between(start, end)

    # ================= 周统计 =================

    def get_weekly_stats(self, start_date: str, end_date: str) -> dict:
        """某时间段（含两端）的统计摘要。start_date 通常为周一。"""
        tasks = self._tasks_between(start_date, end_date)
        base = self._base_stats(tasks, start_date, end_date)
        base["category_stats"] = self._category_stats(tasks)
        base["topic_stats"] = self._topic_stats(tasks)
        return base

    # ================= 月统计 =================

    def get_monthly_stats(self, year: int, month: int) -> dict:
        """某年某月的统计。"""
        start, end = month_range(year, month)
        tasks = self._tasks_between(start, end)
        base = self._base_stats(tasks, start, end)
        base["category_stats"] = self._category_stats(tasks)
        base["topic_stats"] = self._topic_stats(tasks)
        # 延期最多的 Topic
        base["most_postponed_topic"] = self._most_postponed_topic(tasks)
        # 分类排行榜（按完成率降序）
        cats = sorted(
            base["category_stats"],
            key=lambda c: (c["completion_rate"], c["completed_minutes"]),
            reverse=True,
        )
        base["category_ranking"] = cats
        if cats:
            base["best_category"] = cats[0]
            base["worst_category"] = cats[-1]
            base["most_invested_category"] = max(cats, key=lambda c: c["estimated_minutes"])
        else:
            base["best_category"] = None
            base["worst_category"] = None
            base["most_invested_category"] = None
        return base

    # ---------- 基础统计 ----------

    def _base_stats(self, tasks: list[Task], start: str, end: str) -> dict:
        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == STATUS_DONE)
        not_done = sum(1 for t in tasks if t.status == STATUS_NOT_DONE)
        postponed = sum(1 for t in tasks if t.postpone_count > 0)
        completed_minutes = sum(
            t.estimated_minutes for t in tasks if t.status == STATUS_DONE
        )
        estimated = sum(t.estimated_minutes for t in tasks)

        # 学习天数：有任务且完成的天数（更反映实际在学）
        completed_days = {
            t.scheduled_date for t in tasks if t.status == STATUS_DONE
        }
        max_streak = self._max_consecutive_days(completed_days, start, end)

        rate = round(completed / total * 100, 1) if total else 0.0
        return {
            "total_tasks": total,
            "completed_tasks": completed,
            "not_done_tasks": not_done,
            "postponed_tasks": postponed,
            "completion_rate": rate,
            "estimated_minutes": estimated,
            "completed_minutes": completed_minutes,
            "study_days": len(completed_days),
            "streak_days": max_streak,
            "start_date": start,
            "end_date": end,
        }

    @staticmethod
    def _max_consecutive_days(days: set[str], start: str, end: str) -> int:
        """统计 [start,end] 区间内完成天数的最长连续长度。"""
        current = to_date(start)
        end_d = to_date(end)
        best = cur_len = 0
        while current <= end_d:
            if current.strftime("%Y-%m-%d") in days:
                cur_len += 1
                best = max(best, cur_len)
            else:
                cur_len = 0
            current = _date.fromordinal(current.toordinal() + 1)
        return best

    # ---------- 分类 / 主题 ----------

    def _category_stats(self, tasks: list[Task]) -> list[dict]:
        by_cat: dict[str, list[Task]] = defaultdict(list)
        for t in tasks:
            by_cat[t.category or "未分类"].append(t)
        out = []
        for cat, items in by_cat.items():
            completed = sum(1 for t in items if t.status == STATUS_DONE)
            total = len(items)
            rate = round(completed / total * 100, 1) if total else 0.0
            out.append(
                {
                    "category": cat,
                    "total": total,
                    "completed": completed,
                    "completion_rate": rate,
                    "estimated_minutes": sum(t.estimated_minutes for t in items),
                    "completed_minutes": sum(
                        t.estimated_minutes for t in items if t.status == STATUS_DONE
                    ),
                }
            )
        return out

    def _topic_stats(self, tasks: list[Task]) -> list[dict]:
        topic_rows = dict()
        for row in self.repo.conn.execute(
            "SELECT id, name FROM study_topics"
        ).fetchall():
            topic_rows[row["id"]] = row["name"]

        by_topic: dict[int, list[Task]] = defaultdict(list)
        for t in tasks:
            if t.topic_id is not None:
                by_topic[t.topic_id].append(t)

        out = []
        for topic_id, items in by_topic.items():
            completed = sum(1 for t in items if t.status == STATUS_DONE)
            total = len(items)
            rate = round(completed / total * 100, 1) if total else 0.0
            out.append(
                {
                    "topic_id": topic_id,
                    "topic_name": topic_rows.get(topic_id, f"主题{topic_id}"),
                    "total": total,
                    "completed": completed,
                    "postponed": sum(1 for t in items if t.postpone_count > 0),
                    "completion_rate": rate,
                }
            )
        out.sort(key=lambda x: (x["completion_rate"], x["total"]), reverse=True)
        return out

    def _most_postponed_topic(self, tasks: list[Task]) -> dict | None:
        topic_rows = {
            row["id"]: row["name"]
            for row in self.repo.conn.execute("SELECT id, name FROM study_topics")
        }
        counts: dict[int, int] = defaultdict(int)
        for t in tasks:
            if t.topic_id is not None:
                counts[t.topic_id] += t.postpone_count
        if not counts:
            return None
        worst_id = max(counts, key=counts.get)
        return {"topic_id": worst_id, "topic_name": topic_rows.get(worst_id), "count": counts[worst_id]}

    # ================= 学习趋势 =================

    def get_learning_trend(self, days: int = 30, end_date: str | None = None) -> dict:
        """最近 days 天（截至 end_date，缺省今天）的学习趋势。

        返回按日期升序的每日数据点 + 各平均值与趋势。
        """
        anchor = end_date or today()
        start = add_days(anchor, -(days - 1))
        points = []
        day = start
        while day <= anchor:
            stats = self.repo.stats_by_date(day)
            tasks = self.repo.list_by_date(day)
            completed_minutes = sum(
                t.estimated_minutes for t in tasks if t.status == STATUS_DONE
            )
            estimated = sum(t.estimated_minutes for t in tasks)
            postponed = sum(1 for t in tasks if t.postpone_count > 0)
            points.append(
                {
                    "date": day,
                    "total_tasks": stats["total"],
                    "completed_tasks": stats["done"],
                    "completion_rate": stats["rate"] / 100.0,  # 0~1
                    "estimated_minutes": estimated,
                    "completed_minutes": completed_minutes,
                    "postponed_tasks": postponed,
                }
            )
            day = add_days(day, 1)

        def _avg(attr: str, n: int) -> float:
            if n <= 0:
                return 0.0
            vals = [p[attr] for p in points[-n:]]
            return round(sum(vals) / n, 2)

        # 平均完成率（0~1 形式），平均学习时间（分钟/天）
        trend = {
            "points": points,
            "avg_completion_7": _avg("completion_rate", 7),
            "avg_completion_30": _avg("completion_rate", days),
            "avg_study_minutes_7": _avg("completed_minutes", 7),
            "avg_study_minutes_30": _avg("completed_minutes", days),
            "completion_series": [p["completion_rate"] for p in points],
            "study_minutes_series": [p["completed_minutes"] for p in points],
            "postpone_series": [p["postponed_tasks"] for p in points],
        }
        trend["completion_trend"] = _trend_word(trend["completion_series"])
        trend["study_time_trend"] = _trend_word(trend["study_minutes_series"])
        trend["postpone_trend"] = _trend_word(trend["postpone_series"])
        return trend

    # ================= 学习习惯指标 =================

    def get_habit_stats(self, end_date: str | None = None) -> dict:
        """全量学习习惯指标（本地计算）。"""
        anchor = end_date or today()
        tasks = self.repo.conn.execute(
            "SELECT * FROM tasks WHERE scheduled_date <= ?",
            (anchor,),
        ).fetchall()
        tasks = [Task(**dict(r)) for r in tasks]
        if not tasks:
            return self._empty_habit()

        total = len(tasks)
        completed = [t for t in tasks if t.status == STATUS_DONE]
        not_all = [t for t in tasks if t.status != STATUS_DONE]

        # 最常延期的分类
        cat_postpone: dict[str, int] = defaultdict(int)
        topic_postpone: dict[str, int] = defaultdict(int)
        for t in tasks:
            if t.postpone_count > 0:
                cat_postpone[t.category or "未分类"] += t.postpone_count
                if t.topic_id is not None:
                    topic_postpone[self._topic_name(t.topic_id)] += t.postpone_count

        # 连续天数
        completed_days = {t.scheduled_date for t in completed}
        max_streak = self._max_consecutive_days_all(completed_days)
        current_streak = self._current_streak(completed_days, anchor)

        # 最长连续未完成天数
        bad_days = self._unfinished_days(not_all, tasks, anchor)
        max_bad_streak = self._max_consecutive_days_all(bad_days)

        # 学习天数（有完成的天）
        study_days = len(completed_days)
        done_minutes = sum(t.estimated_minutes for t in completed)

        avg_postpone = round(
            sum(t.postpone_count for t in tasks) / total, 2
        )

        return {
            "most_postponed_category": max(cat_postpone, key=cat_postpone.get)
            if cat_postpone else None,
            "most_postponed_topic": max(topic_postpone, key=topic_postpone.get)
            if topic_postpone else None,
            "avg_daily_tasks": round(total / (study_days or 1), 2),
            "avg_daily_study_minutes": round(done_minutes / (study_days or 1), 2),
            "avg_completion_rate": round(len(completed) / total * 100, 1),
            "max_streak_days": max_streak,
            "current_streak_days": current_streak,
            "max_unfinished_streak_days": max_bad_streak,
            "avg_task_postpone_count": avg_postpone,
        }

    def _empty_habit(self) -> dict:
        return {
            "most_postponed_category": None,
            "most_postponed_topic": None,
            "avg_daily_tasks": 0.0,
            "avg_daily_study_minutes": 0.0,
            "avg_completion_rate": 0.0,
            "max_streak_days": 0,
            "current_streak_days": 0,
            "max_unfinished_streak_days": 0,
            "avg_task_postpone_count": 0.0,
        }

    def _topic_name(self, topic_id: int) -> str:
        row = self.repo.conn.execute(
            "SELECT name FROM study_topics WHERE id = ?", (topic_id,)
        ).fetchone()
        return row["name"] if row else f"主题{topic_id}"

    @staticmethod
    def _max_consecutive_days_all(days: set[str]) -> int:
        if not days:
            return 0
        sorted_dates = sorted(days)
        best = cur = 1
        prev = _date.fromisoformat(sorted_dates[0])
        for s in sorted_dates[1:]:
            d = _date.fromisoformat(s)
            if (d - prev).days == 1:
                cur += 1
                best = max(best, cur)
            else:
                cur = 1
            prev = d
        return best

    @staticmethod
    def _current_streak(completed_days: set[str], anchor: str) -> int:
        cur = _date.fromisoformat(anchor)
        streak = 0
        while cur.strftime("%Y-%m-%d") in completed_days:
            streak += 1
            cur = cur - timedelta(days=1)
        return streak

    def _unfinished_days(self, not_all: list[Task], all_tasks: list[Task], anchor: str) -> set[str]:
        """有任务但没有任何完成的天（未完成天）。"""
        done_days = {t.scheduled_date for t in all_tasks if t.status == STATUS_DONE}
        has_task_days = {t.scheduled_date for t in all_tasks}
        return has_task_days - done_days


def _trend_word(series: list[float]) -> str:
    """基于首尾/斜率判断趋势：上升 / 下降 / 持平。"""
    if len(series) < 2:
        return "持平"
    # 简单线性斜率（归一化）
    n = len(series)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0.0
    if slope > 0.02:
        return "上升"
    if slope < -0.02:
        return "下降"
    return "持平"
