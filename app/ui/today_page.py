"""TodayPage：今日工作区（纯 UI / View）。

只负责 layout、widgets、visual state 与 UI signals；不调用 service，
不包含 Planner / Task / Review 业务逻辑。业务编排仍由 MainWindow 负责。

信息层级：
    Summary Metrics
    Controls（路线筛选 / 添加任务）
    Phase context
    Planner context
    Focus / 今日学习
    Review / 今日复习
    Career Signals（由 MainWindow 追加）
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .components.button import SAButton
from .components.empty_state import SAEmptyState
from .components.info_banner import SAInfoBanner
from .components.stat_card import SAStatCard
from .design import icons as _icons
from .design import spacing as _spacing


class TodayPage(QWidget):
    add_task_requested = Signal()
    route_filter_changed = Signal()
    replan_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(
            _spacing.XXL, _spacing.LG, _spacing.XXL, _spacing.LG
        )
        root.setSpacing(_spacing.LG)

        # ----- Summary metrics -----
        self.summary_container = QWidget()
        summary_row = QHBoxLayout(self.summary_container)
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(_spacing.MD)
        self.pending_card = SAStatCard(
            "待处理", "0", icon_name=_icons.IconName.PLAY
        )
        self.minutes_card = SAStatCard(
            "预计时长", "0 分钟", icon_name=_icons.IconName.CALENDAR
        )
        self.review_card = SAStatCard(
            "今日复习", "0", icon_name=_icons.IconName.BOOK
        )
        for card in (self.pending_card, self.minutes_card, self.review_card):
            summary_row.addWidget(card, stretch=1)
        root.addWidget(self.summary_container)

        # ----- Controls -----
        controls = QHBoxLayout()
        controls.setSpacing(_spacing.SM)
        controls.addWidget(QLabel("路线筛选"))
        self.route_filter_combo = QComboBox()
        controls.addWidget(self.route_filter_combo)
        self.route_stats_label = QLabel("")
        self.route_stats_label.setObjectName("TaskMeta")
        controls.addWidget(self.route_stats_label)
        controls.addStretch()
        self.add_task_btn = SAButton("＋ 添加学习任务", variant="secondary")
        controls.addWidget(self.add_task_btn)
        root.addLayout(controls)

        # ----- Phase context -----
        self.phase_container = QWidget()
        phase_box = QVBoxLayout(self.phase_container)
        phase_box.setContentsMargins(0, 0, 0, 0)
        phase_box.setSpacing(2)
        self.phase_label = QLabel("")
        self.phase_label.setObjectName("TaskMeta")
        self.phase_goal_label = QLabel("")
        self.phase_goal_label.setObjectName("TaskMeta")
        phase_box.addWidget(self.phase_label)
        phase_box.addWidget(self.phase_goal_label)
        root.addWidget(self.phase_container)

        # ----- Planner context -----
        self.planner_banner = SAInfoBanner("", "", variant="info")
        self.planner_container = self.planner_banner
        self.planner_status_label = self.planner_banner.title_label()
        self.planner_note_label = self.planner_banner.description_label()
        self.planner_replan_btn = SAButton(
            "重新规划今天", variant="secondary", size="small"
        )
        self.planner_banner.set_action(self.planner_replan_btn)
        root.addWidget(self.planner_container)

        # ----- Task list -----
        self.scroll = QScrollArea()
        self.scroll.setObjectName("SATodayScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.viewport().setObjectName("SATodayViewport")
        self.scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.list_container = QWidget()
        self.list_container.setObjectName("SATodayListContainer")
        self.list_container.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(_spacing.SM)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, stretch=1)

        # ----- Empty state -----
        self.empty_action_btn = SAButton("添加学习任务", variant="secondary")
        self.empty_state = SAEmptyState(
            title="今天还没有学习任务",
            description="可以添加任务，或等待学习计划生成。",
            icon_name=_icons.IconName.BOOK,
            action=self.empty_action_btn,
        )
        # 兼容旧属性名
        self.empty_hint = self.empty_state
        root.addWidget(self.empty_state)

        # ----- signals -----
        self.add_task_btn.clicked.connect(self.add_task_requested)
        self.empty_action_btn.clicked.connect(self.add_task_requested)
        self.route_filter_combo.currentIndexChanged.connect(
            lambda *_: self.route_filter_changed.emit()
        )
        self.planner_replan_btn.clicked.connect(self.replan_requested)

    # ---------- View API ----------
    def set_summary_metrics(
        self, pending: int, minutes: int, reviews: int
    ) -> None:
        self.pending_card.set_value(str(int(pending)))
        self.minutes_card.set_value(f"{int(minutes)} 分钟")
        self.review_card.set_value(str(int(reviews)))

    def set_phase_context(self, text: str, goal: str = "") -> None:
        self.phase_label.setText(text)
        self.phase_goal_label.setText(goal)
        self.phase_container.setVisible(True)

    def hide_phase_context(self) -> None:
        self.phase_container.setVisible(False)

    def set_planner_state(
        self,
        status: str,
        note: str = "",
        *,
        available: bool = True,
        replan_enabled: bool = True,
        variant: str = "info",
    ) -> None:
        self.planner_status_label.setText(status)
        self.planner_status_label.setVisible(bool(status))
        self.planner_note_label.setText(note)
        self.planner_note_label.setVisible(bool(note))
        self.planner_banner.set_variant(variant if not available else "info")
        self.planner_replan_btn.setEnabled(replan_enabled)
        self.planner_container.setVisible(True)

    def hide_planner(self) -> None:
        self.planner_container.setVisible(False)

    def clear_tasks(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def add_task_widget(self, widget: QWidget) -> None:
        self.list_layout.addWidget(widget)

    def add_section_header(self, title: str) -> None:
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        self.list_layout.addWidget(lbl)

    def add_section_hint(self, text: str) -> None:
        hint = QLabel(text)
        hint.setObjectName("EmptyHint")
        hint.setWordWrap(True)
        self.list_layout.addWidget(hint)

    def add_content_widget(self, widget: QWidget) -> None:
        self.list_layout.addWidget(widget)

    def finish_tasks(self) -> None:
        self.list_layout.addStretch()

    def set_empty_visible(self, visible: bool) -> None:
        self.empty_state.setVisible(visible)

    def set_scroll_visible(self, visible: bool) -> None:
        self.scroll.setVisible(visible)
