"""月总结页面组件。

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

from ..utils.date_utils import month_range, today as _default_today


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


class MonthlySummaryPage(QWidget):
    """月总结页面：选择月份 + 统计 + 分类排行榜 + AI。"""

    def __init__(self, summary_service, today_provider=None,
                 practice_capability_service=None, parent=None):
        super().__init__(parent)
        self.summary_service = summary_service
        self.today_provider = today_provider or _default_today
        self.practice_capability_service = practice_capability_service
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

        # Phase E：路线维度（本地真实统计）
        route_title = QLabel("学习路线")
        route_title.setObjectName("SectionTitle")
        outer.addWidget(route_title)
        self.route_label = QLabel("")
        self.route_label.setWordWrap(True)
        self.route_label.setObjectName("TaskMeta")
        outer.addWidget(self.route_label)

        # Phase 5：本月项目能力证据（只计数，不平均 Capability）
        self.practice_title = QLabel("项目能力证据")
        self.practice_title.setObjectName("SectionTitle")
        outer.addWidget(self.practice_title)
        self.practice_label = QLabel("")
        self.practice_label.setWordWrap(True)
        self.practice_label.setObjectName("TaskMeta")
        outer.addWidget(self.practice_label)

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

        # 路线维度（不硬算整体 mastery %）
        route_lines = []
        for rs in stats.get("route_stats", []):
            parts = [
                f"完成任务：{rs.get('done_tasks', 0)}",
                f"课程覆盖：{rs.get('covered_topics', 0)} Topic",
                f"验收：{rs.get('assessment_evidence_count', 0)}",
                f"掌握：{rs.get('mastered_count', 0)}",
                f"复习：{rs.get('review_count', 0)}",
            ]
            weak = rs.get("weak_topics") or []
            if weak:
                parts.append(f"薄弱：{'、'.join(weak[:3])}")
            route_lines.append(f"{rs.get('route_name', '—')}：" + "　".join(parts))
        self.route_label.setText(
            "\n".join(route_lines) if route_lines else "暂无路线数据"
        )

        self._render_practice_evidence(result)

        while self.ai_layout.count():
            item = self.ai_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.ai_layout.addWidget(_ai_section(result.get("ai_summary")))

    def _render_practice_evidence(self, result: dict) -> None:
        """本月新增项目能力证据：只显示计数与 Topic 名称，绝不平均 Capability。"""
        if self.practice_capability_service is None:
            self.practice_label.setText("未启用项目能力证据")
            return
        try:
            rows = self.practice_capability_service.list_active_created()
        except Exception:  # noqa: BLE001
            self.practice_label.setText("未启用项目能力证据")
            return
        start, end = result["start"], result["end"]
        month_rows = [r for r in rows if start <= (r["created_at"] or "")[:10] <= end]
        if not month_rows:
            self.practice_label.setText("本月新增项目能力证据：PROJECT：0")
            return
        names = []
        for r in month_rows:
            topic = self.practice_capability_service.plan_repo.get_topic(
                int(r["topic_id"])
            )
            names.append(getattr(topic, "name", "") if topic else "—")
        self.practice_label.setText(
            f"本月新增项目能力证据：PROJECT：{len(month_rows)}\n"
            + "、".join(n for n in names if n)
        )

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
