"""月总结页面组件（UI-4 redesign）。

轻量学习复盘，不是 BI dashboard：
- Month Navigator（Fluent chevron 图标）
- Summary Metrics（4 个核心 SAStatCard + secondary stats）
- Learning Activity（分类排行榜 / highlights）
- Route Progress（路线维度）
- Practice Evidence（只计数，不平均 Capability）
- AI Monthly Insight

只展示 SummaryService 提供的数据；不新增计算语义、不引入 chart library。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..utils.date_utils import today as _default_today
from .components.card import SACard
from .components.info_banner import SAInfoBanner
from .components.progress_bar import SAProgressBar
from .components.section_header import SASectionHeader
from .components.stat_card import SAStatCard
from .components.tag import SATag
from .design import icons as _icons
from .design import spacing as _spacing


def _h_mm(minutes: int) -> str:
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} 小时 {m} 分"
    if h:
        return f"{h} 小时"
    return f"{m} 分钟"


def _add_pair(lay: QVBoxLayout, label: str, value: str) -> None:
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(_spacing.SM)
    k = QLabel(label)
    k.setObjectName("SAValueLabel")
    v = QLabel(value)
    v.setObjectName("SAValueStrong")
    v.setWordWrap(True)
    rl.addWidget(k)
    rl.addWidget(v, stretch=1)
    lay.addWidget(row)


def _ai_section(ai_summary_json: str | None) -> QWidget:
    """把 AI 总结 JSON 渲染成说明区；无则显示 info banner。"""
    import json

    card = SACard()
    card.add_widget(SASectionHeader("AI 月度解读"))
    lay = card.body_layout

    def _unavailable() -> QWidget:
        banner = SAInfoBanner(
            "AI 解读暂不可用",
            "AI 解读暂不可用，本地统计仍可正常查看。",
            variant="info",
        )
        lay.addWidget(banner)
        return card

    if not ai_summary_json:
        return _unavailable()
    try:
        data = json.loads(ai_summary_json)
    except json.JSONDecodeError:
        return _unavailable()

    overview = data.get("overview")
    if overview:
        _add_pair(lay, "总览", str(overview))
    # production 文案统一为月度中性表达（不再出现 weekly 标签）。
    for key, label in (
        ("strengths", "做得好的地方"),
        ("problems", "主要问题"),
        ("weaknesses", "本月不足"),
        ("recommendations", "建议"),
        ("next_week_focus", "后续重点"),
        ("next_month_focus", "下月重点"),
    ):
        items = data.get(key) or []
        if items:
            lines = "；".join(str(i) for i in items)
            _add_pair(lay, label, lines)
    return card


class MonthlySummaryPage(QWidget):
    """月总结页面：月份导航 + 核心指标 + 分类/路线/项目 + AI 解读。"""

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

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            _spacing.XXL, _spacing.LG, _spacing.XXL, _spacing.LG
        )
        outer.setSpacing(_spacing.LG)

        # Month navigator
        head = QHBoxLayout()
        head.setSpacing(_spacing.SM)
        self.prev_btn = self._nav_btn(
            _icons.IconName.CHEVRON_LEFT, "上一月", self._prev
        )
        self.next_btn = self._nav_btn(
            _icons.IconName.CHEVRON_RIGHT, "下一月", self._next
        )
        self.period_label = QLabel("")
        self.period_label.setObjectName("SASectionSubtitle")
        head.addWidget(self.prev_btn)
        head.addStretch()
        self.period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.period_label)
        head.addStretch()
        head.addWidget(self.next_btn)
        outer.addLayout(head)

        # Core summary metrics（4 个）
        self.core_row = QHBoxLayout()
        self.core_row.setSpacing(_spacing.MD)
        self.completed_card = SAStatCard(
            "完成任务", "0", icon_name=_icons.IconName.CHECK
        )
        self.rate_card = SAStatCard("完成率", "0%")
        self.minutes_card = SAStatCard(
            "实际学习时长", "0 分钟", icon_name=_icons.IconName.CALENDAR
        )
        self.days_card = SAStatCard(
            "学习天数", "0", icon_name=_icons.IconName.BOOK
        )
        for card in (self.completed_card, self.rate_card,
                     self.minutes_card, self.days_card):
            self.core_row.addWidget(card, stretch=1)
        outer.addLayout(self.core_row)

        # Empty hint（本月无记录）
        self.empty_banner = SAInfoBanner(
            "本月暂无学习记录", "换个月份看看，或先完成一些学习任务。",
            variant="info",
        )
        self.empty_banner.setVisible(False)
        outer.addWidget(self.empty_banner)

        # Secondary stats
        secondary_card = SACard()
        secondary_title = QLabel("更多统计")
        secondary_title.setObjectName("SASectionTitle")
        secondary_card.add_widget(secondary_title)
        self.summary_grid = secondary_card
        self.summary_layout = secondary_card.body_layout
        outer.addWidget(secondary_card)

        # Scrollable workspace
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        workspace = QWidget()
        self.workspace_layout = QVBoxLayout(workspace)
        self.workspace_layout.setContentsMargins(0, 0, 6, 0)
        self.workspace_layout.setSpacing(_spacing.LG)

        self._build_category_card()
        self._build_route_card()
        self._build_practice_card()

        self.ai_container = QWidget()
        self.ai_layout = QVBoxLayout(self.ai_container)
        self.ai_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.addWidget(self.ai_container)
        self.workspace_layout.addStretch()
        self.scroll.setWidget(workspace)
        outer.addWidget(self.scroll, stretch=1)

    @staticmethod
    def _nav_btn(icon_name, tooltip, slot):
        from .components.button import SAIconButton

        btn = SAIconButton(
            icon_name, tooltip=tooltip, accessible_name=tooltip
        )
        btn.clicked.connect(slot)
        return btn

    def _build_category_card(self) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("分类排行"))
        self.ranking_label = QLabel("")
        self.ranking_label.setObjectName("TaskMeta")
        self.ranking_label.setWordWrap(True)
        card.add_widget(self.ranking_label)
        self.ranking_rows = QVBoxLayout()
        self.ranking_rows.setContentsMargins(0, 0, 0, 0)
        self.ranking_rows.setSpacing(_spacing.SM)
        card.add_layout(self.ranking_rows)
        self.workspace_layout.addWidget(card)

    def _build_route_card(self) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("学习路线"))
        self.route_layout = card.body_layout
        self.route_label = QLabel("")
        self.route_label.setObjectName("TaskMeta")
        self.route_label.setWordWrap(True)
        self.route_layout.addWidget(self.route_label)
        self.workspace_layout.addWidget(card)

    def _build_practice_card(self) -> None:
        card = SACard()
        card.add_widget(SASectionHeader("项目能力证据"))
        self.practice_title = QLabel("")
        self.practice_title.setVisible(False)
        self.practice_layout = card.body_layout
        self.practice_label = QLabel("")
        self.practice_label.setObjectName("TaskMeta")
        self.practice_label.setWordWrap(True)
        self.practice_layout.addWidget(self.practice_label)
        self.workspace_layout.addWidget(card)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        result = self.summary_service.get_monthly_summary(self.year, self.month)
        stats = result["stats"]
        self.period_label.setText(f"{result['start']} ~ {result['end']}")

        total_tasks = int(stats.get("total_tasks", 0) or 0)
        self.completed_card.set_value(str(stats.get("completed_tasks", 0)))
        self.rate_card.set_value(f"{stats.get('completion_rate', 0):.1f}%")
        self.minutes_card.set_value(_h_mm(stats.get("completed_minutes", 0)))
        self.days_card.set_value(str(stats.get("study_days", 0)))
        self.empty_banner.setVisible(total_tasks == 0)

        # Secondary stats（兼容旧 summary_layout 文本断言）
        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        secondary = [
            ("总任务", str(stats.get("total_tasks", 0))),
            ("完成", str(stats.get("completed_tasks", 0))),
            ("延期次数", str(stats.get("postponed_tasks", 0))),
            ("预计时长", _h_mm(stats.get("estimated_minutes", 0))),
            ("最长连续学习", str(stats.get("streak_days", 0))),
        ]
        for label, value in secondary:
            row = QLabel(f"{label}：{value}")
            row.setObjectName("TaskMeta")
            self.summary_layout.addWidget(row)

        self._render_ranking(stats)
        self._render_routes(stats)
        self._render_practice_evidence(result)

        while self.ai_layout.count():
            item = self.ai_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.ai_layout.addWidget(_ai_section(result.get("ai_summary")))

    def _render_ranking(self, stats: dict) -> None:
        while self.ranking_rows.count():
            item = self.ranking_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        rows = stats.get("category_ranking", [])[:8]
        if not rows:
            self.ranking_label.setText("暂无分类数据")
            self.ranking_label.setVisible(True)
        else:
            self.ranking_label.setVisible(False)
            for c in rows:
                rate = float(c.get("completion_rate") or 0)
                self.ranking_rows.addWidget(SAProgressBar(
                    value=rate,
                    label=(f"{c['category']}　{c['completed']}/{c['total']}"
                           f"　{_h_mm(c.get('estimated_minutes', 0))}"),
                ))

        highlight = []
        if stats.get("best_category"):
            b = stats["best_category"]
            highlight.append(f"完成率较高：{b['category']} {b['completion_rate']:.0f}%")
        if stats.get("worst_category"):
            w = stats["worst_category"]
            highlight.append(f"需要关注：{w['category']} {w['completion_rate']:.0f}%")
        if stats.get("most_invested_category"):
            m = stats["most_invested_category"]
            highlight.append(f"投入较多：{m['category']} {m['estimated_minutes']} 分钟")
        if stats.get("most_postponed_topic"):
            mp = stats["most_postponed_topic"]
            highlight.append(f"延期较多：{mp['topic_name']}（{mp['count']} 次）")
        if highlight:
            tag_row = QHBoxLayout()
            tag_row.setSpacing(_spacing.SM)
            for text in highlight:
                tag_row.addWidget(SATag(text, "neutral"))
            tag_row.addStretch()
            holder = QWidget()
            holder.setLayout(tag_row)
            self.ranking_rows.addWidget(holder)

    def _render_routes(self, stats: dict) -> None:
        rows = stats.get("route_stats", [])
        if not rows:
            self.route_label.setText("暂无路线数据")
            return
        lines = []
        for rs in rows:
            parts = [
                f"完成任务：{rs.get('done_tasks', 0)}",
                f"课程覆盖：{rs.get('covered_topics', 0)} Topic",
                f"验收：{rs.get('assessment_evidence_count', 0)}",
                f"掌握：{rs.get('mastered_count', 0)}",
            ]
            weak = rs.get("weak_topics") or []
            if weak:
                parts.append(f"薄弱：{'、'.join(weak[:3])}")
            lines.append(f"{rs.get('route_name', '—')}：" + "　".join(parts))
        self.route_label.setText("\n".join(lines))

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
