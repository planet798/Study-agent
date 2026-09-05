"""周总结 / 月总结页面组件。

作为主窗口 QStackedWidget 的独立页面，保持现有视觉风格（复用 styles QSS）。
只展示由 SummaryService 提供的本地统计与 AI 总结；AI 不可用时仍显示本地统计。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..utils.date_utils import add_days, month_range, today as _default_today


def _h_mm(minutes: int) -> str:
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    return f"{h} 小时 {m} 分"


def _ai_section(ai_summary_json: str | None) -> QWidget:
    """把 AI 总结 JSON 渲染成说明区；无则提示不可用。"""
    import json

    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 6, 0, 0)
    lay.setSpacing(4)

    title = QLabel("AI 解读")
    title.setObjectName("SectionTitle")
    lay.addWidget(title)

    if not ai_summary_json:
        lbl = QLabel("AI 总结暂不可用（仅显示本地统计）")
        lbl.setObjectName("TaskMeta")
        lay.addWidget(lbl)
        return box

    try:
        data = json.loads(ai_summary_json)
    except json.JSONDecodeError:
        lbl = QLabel("AI 总结暂不可用（仅显示本地统计）")
        lbl.setObjectName("TaskMeta")
        lay.addWidget(lbl)
        return box

    overview = data.get("overview")
    if overview:
        _add_pair(lay, "总览", str(overview))
    for key, label in (
        ("strengths", "做得好的地方"),
        ("problems", "本周主要问题" if "problems" in data else "本月不足"),
        ("weaknesses", "本月不足"),
        ("recommendations", "建议"),
        ("next_week_focus", "下周重点"),
        ("next_month_focus", "下月重点"),
    ):
        items = data.get(key) or []
        if items:
            lines = "；".join(str(i) for i in items)
            _add_pair(lay, label, lines)
    return box


def _add_pair(lay: QVBoxLayout, label: str, value: str) -> None:
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    k = QLabel(label)
    k.setObjectName("TaskMeta")
    k.setFixedWidth(120)
    v = QLabel(value)
    v.setWordWrap(True)
    rl.addWidget(k)
    rl.addWidget(v, stretch=1)
    lay.addWidget(row)


def _stat_grid(stats: dict, keys: list[tuple[str, str]]) -> QWidget:
    """把若干统计键渲染成两两一排的网格。keys: (字段名, 中文标签)。"""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(3)
    for i in range(0, len(keys), 2):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        for key, label in keys[i : i + 2]:
            value = stats.get(key, 0)
            text = f"{label}：{value}"
            if key in ("completion_rate", "not_done_tasks"):  # no-op keep plain
                pass
            lbl = QLabel(text)
            lbl.setObjectName("TaskMeta")
            rl.addWidget(lbl)
            rl.addStretch()
        lay.addWidget(row)
    return box


def _category_text(category_stats: list[dict], top: int = 5) -> str:
    if not category_stats:
        return "暂无数据"
    parts = []
    for c in category_stats[:top]:
        parts.append(
            f"{c['category']} {c['completion_rate']:.0f}%"
            f"（{c['completed']}/{c['total']}）"
        )
    return "　".join(parts)


class WeeklySummaryPage(QWidget):
    """周总结页面：< 上一周 | 本周 | 下一周 > + 统计 + AI。"""

    def __init__(self, summary_service, today_provider=None, parent=None):
        super().__init__(parent)
        self.summary_service = summary_service
        self.today_provider = today_provider or _default_today
        self.current_anchor = self.today_provider()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        head = QHBoxLayout()
        title = QLabel("周总结")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch()

        self.prev_btn = QPushButton("< 上一周")
        self.prev_btn.clicked.connect(self._prev)
        self.period_label = QLabel("")
        self.next_btn = QPushButton("下一周 >")
        self.next_btn.clicked.connect(self._next)
        head.addWidget(self.prev_btn)
        head.addWidget(self.period_label)
        head.addWidget(self.next_btn)
        outer.addLayout(head)

        # 本周学习总结
        summary_title = QLabel("本周学习总结")
        summary_title.setObjectName("AppTitle")
        outer.addWidget(summary_title)
        self.summary_grid = _stat_grid({}, [])
        outer.addWidget(self.summary_grid)

        # 分类表现
        cat_title = QLabel("分类表现")
        cat_title.setObjectName("SectionTitle")
        outer.addWidget(cat_title)
        self.category_label = QLabel("")
        self.category_label.setWordWrap(True)
        self.category_label.setObjectName("TaskMeta")
        outer.addWidget(self.category_label)

        # 本周主要问题
        problem_title = QLabel("本周主要问题")
        problem_title.setObjectName("SectionTitle")
        outer.addWidget(problem_title)
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setObjectName("TaskMeta")
        outer.addWidget(self.problem_label)

        # AI 解读（滚动区域）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.ai_container = QWidget()
        self.ai_layout = QVBoxLayout(self.ai_container)
        self.scroll.setWidget(self.ai_container)
        outer.addWidget(self.scroll, stretch=1)

    def refresh(self) -> None:
        result = self.summary_service.get_weekly_summary(self.current_anchor)
        stats = result["stats"]
        period = f"{result['start']} ~ {result['end']}"
        self.period_label.setText(period)

        self._clear_layout(self.ai_layout)
        self._clear_layout(self.summary_grid.layout())
        grid_keys = [
            ("total_tasks", "总任务"),
            ("completed_tasks", "完成"),
            ("completion_rate", "完成率(%)"),
            ("estimated_minutes", "预计(分钟)"),
            ("completed_minutes", "实际(分钟)"),
            ("study_days", "学习天数"),
            ("streak_days", "连续学习"),
        ]
        for key, label in grid_keys:
            if key == "total_tasks":
                self.total_label = QLabel(f"总任务：{stats.get('total_tasks', 0)}")
            row = QLabel(f"{label}：{self._fmt(key, stats)}")
            row.setObjectName("TaskMeta")
            self.summary_grid.layout().addWidget(row)

        self.category_label.setText(
            "分类表现：" + _category_text(stats.get("category_stats", []))
        )

        # 本周主要问题（延期最多 + 最容易失败）
        problems = []
        most_postponed = stats.get("most_postponed_topic")
        postponed = stats.get("postponed_tasks", 0)
        if postponed:
            if most_postponed:
                problems.append(f"延期最多：{most_postponed['topic_name']}")
            else:
                problems.append(f"延期任务：{postponed} 个")
        cat_stats = stats.get("category_stats", [])
        worst_cat = min(cat_stats, key=lambda c: c["completion_rate"]) if cat_stats else None
        if worst_cat and worst_cat["completed"] == 0:
            problems.append(f"最容易失败的任务分类：{worst_cat['category']}")
        self.problem_label.setText("　".join(problems) if problems else "本周暂无突出问题")

        # AI 解读
        self.ai_layout.addWidget(_ai_section(result.get("ai_summary")))

    @staticmethod
    def _fmt(key: str, stats: dict) -> str:
        v = stats.get(key, 0)
        if key == "completion_rate":
            return f"{v:.1f}"
        return str(v)

    def _clear_layout(self, lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _prev(self) -> None:
        self.current_anchor = add_days(self.current_anchor, -7)
        self._ensure_within_today()
        self.refresh()

    def _next(self) -> None:
        self.current_anchor = add_days(self.current_anchor, 7)
        self.refresh()

    def _ensure_within_today(self) -> None:
        # 不要求强制实现；简单起见允许自由翻页
        pass


class MonthlySummaryPage(QWidget):
    """月总结页面：选择月份 + 统计 + 分类排行榜 + AI。"""

    def __init__(self, summary_service, today_provider=None, parent=None):
        super().__init__(parent)
        self.summary_service = summary_service
        self.today_provider = today_provider or _default_today
        y, m = self._current_ym()
        self.year, self.month = y, m
        self._build_ui()
        self.refresh()

    def _current_ym(self) -> tuple[int, int]:
        from ..utils.date_utils import to_date

        d = to_date(self.today_provider())
        return d.year, d.month

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        head = QHBoxLayout()
        title = QLabel("月总结")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch()
        self.prev_btn = QPushButton("< 上一月")
        self.prev_btn.clicked.connect(self._prev)
        self.period_label = QLabel("")
        self.next_btn = QPushButton("下一月 >")
        self.next_btn.clicked.connect(self._next)
        head.addWidget(self.prev_btn)
        head.addWidget(self.period_label)
        head.addWidget(self.next_btn)
        outer.addLayout(head)

        summary_title = QLabel("本月学习总结")
        summary_title.setObjectName("AppTitle")
        outer.addWidget(summary_title)
        self.summary_grid = QWidget()
        self.summary_layout = QVBoxLayout(self.summary_grid)
        self.summary_layout.setContentsMargins(0, 4, 0, 4)
        outer.addWidget(self.summary_grid)

        ranking_title = QLabel("分类排行榜")
        ranking_title.setObjectName("SectionTitle")
        outer.addWidget(ranking_title)
        self.ranking_label = QLabel("")
        self.ranking_label.setWordWrap(True)
        self.ranking_label.setObjectName("TaskMeta")
        outer.addWidget(self.ranking_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.ai_container = QWidget()
        self.ai_layout = QVBoxLayout(self.ai_container)
        self.scroll.setWidget(self.ai_container)
        outer.addWidget(self.scroll, stretch=1)

    def refresh(self) -> None:
        result = self.summary_service.get_monthly_summary(self.year, self.month)
        stats = result["stats"]
        self.period_label.setText(f"{result['start']} ~ {result['end']}")

        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        keys = [
            ("total_tasks", "总任务"),
            ("completed_tasks", "完成"),
            ("completion_rate", "完成率(%)"),
            ("postponed_tasks", "延期次数"),
            ("estimated_minutes", "预计(分钟)"),
            ("completed_minutes", "实际(分钟)"),
            ("study_days", "学习天数"),
            ("streak_days", "最长连续学习"),
        ]
        for key, label in keys:
            v = stats.get(key, 0)
            text = label
            if key == "completion_rate":
                text = f"{label}：{v:.1f}"
                row = QLabel(text)
            else:
                row = QLabel(f"{label}：{v}")
            row.setObjectName("TaskMeta")
            self.summary_layout.addWidget(row)

        # 分类排行榜
        ranking_lines = []
        for c in stats.get("category_ranking", [])[:8]:
            ranking_lines.append(
                f"{c['category']}：完成率 {c['completion_rate']:.0f}%"
                f"（{c['completed']}/{c['total']}），投入 {c['estimated_minutes']} 分钟"
            )
        highlight = []
        if stats.get("best_category"):
            b = stats["best_category"]
            highlight.append(f"完成率最高：{b['category']} {b['completion_rate']:.0f}%")
        if stats.get("worst_category"):
            w = stats["worst_category"]
            highlight.append(f"完成率最低：{w['category']} {w['completion_rate']:.0f}%")
        if stats.get("most_invested_category"):
            m = stats["most_invested_category"]
            highlight.append(f"投入最多：{m['category']} {m['estimated_minutes']} 分钟")
        if stats.get("most_postponed_topic"):
            mp = stats["most_postponed_topic"]
            highlight.append(
                f"延期最多：{mp['topic_name']}（{mp['count']} 次）"
            )
        self.ranking_label.setText(
            ("\n".join(ranking_lines) if ranking_lines else "暂无分类数据")
            + ("\n" + "　".join(highlight) if highlight else "")
        )

        while self.ai_layout.count():
            item = self.ai_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.ai_layout.addWidget(_ai_section(result.get("ai_summary")))

    def _prev(self) -> None:
        if self.month == 1:
            self.year -= 1
            self.month = 12
        else:
            self.month -= 1
        self.refresh()

    def _next(self) -> None:
        if self.month == 12:
            self.year += 1
            self.month = 1
        else:
            self.month += 1
        self.refresh()
