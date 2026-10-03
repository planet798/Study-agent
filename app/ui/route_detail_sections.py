"""路线详情分区与课程行；无数据库依赖，只发出交互信号。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .components.button import SAButton
from .components.flow_layout import FlowWidget
from .components.progress_bar import SAProgressBar
from .components.status_badge import SAStatusBadge
from .design import spacing
from .route_overview_widgets import RouteActionsButton, route_label


class RouteDetailSection(QFrame):
    expanded_changed = Signal(str, bool)

    def __init__(self, key: str, title: str, summary: str = "", *, expanded=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("RouteDetailSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XS, spacing.MD, spacing.XS, spacing.MD)
        root.setSpacing(spacing.SM)
        self.header_layout = QHBoxLayout()
        self.header_layout.addWidget(route_label(title, "SectionTitle"), 1)
        if summary:
            self.header_layout.addWidget(route_label(summary))
        self.toggle = SAButton("", variant="subtle", size="small")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(f"展开或收起：{title}")
        self.header_layout.addWidget(self.toggle)
        root.addLayout(self.header_layout)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, spacing.XS, 0, 0)
        self.body_layout.setSpacing(spacing.SM)
        root.addWidget(self.body)
        self.toggle.setChecked(expanded)
        self._apply_expanded(expanded)
        self.toggle.toggled.connect(self._on_toggle)

    def _apply_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setText("收起" if expanded else "展开")

    def _on_toggle(self, expanded):
        self._apply_expanded(expanded)
        self.expanded_changed.emit(self.key, expanded)


@dataclass(frozen=True)
class RouteTopicRow:
    id: int
    name: str
    minutes: int
    complete: bool
    activities: str
    has_profile: bool


class RoutePhaseSection(RouteDetailSection):
    requested = Signal(str, int)

    def __init__(self, phase_id: int, title: str, goal: str,
                 topics: list[RouteTopicRow], *, archived: bool, expanded: bool):
        super().__init__(f"phase:{phase_id}", title,
                         f"{sum(t.complete for t in topics)}/{len(topics)} 已完成",
                         expanded=expanded)
        if not archived:
            add = SAButton("添加知识点", variant="secondary", size="small")
            add.clicked.connect(lambda: self.requested.emit("add_topic", phase_id))
            self.header_layout.insertWidget(self.header_layout.count() - 1, add)
            more = RouteActionsButton([("delete_phase", "删除阶段")])
            more.setAccessibleName(f"阶段操作：{title}")
            more.requested.connect(lambda key: self.requested.emit(key, phase_id))
            self.header_layout.addWidget(more)
        if goal:
            self.body_layout.addWidget(route_label(f"目标：{goal}"))
        if not topics:
            self.body_layout.addWidget(route_label("暂无知识点"))
        for topic in topics:
            self._add_topic(topic, archived)

    def _add_topic(self, topic, archived):
        row = QFrame()
        row.setObjectName("RouteTopicRow")
        root = QVBoxLayout(row)
        root.setContentsMargins(spacing.SM, spacing.SM, spacing.SM, spacing.SM)
        root.setSpacing(spacing.XS)
        head = QHBoxLayout()
        head.addWidget(route_label(topic.name, "TaskTitle"), 1)
        if topic.complete:
            head.addWidget(SAStatusBadge("completed"))
        actions = []
        if topic.has_profile:
            actions.append(("edit_profile", "学习组成"))
        if not archived:
            actions.append(("delete_topic", "删除知识点"))
        if actions:
            menu = RouteActionsButton(actions)
            menu.setAccessibleName(f"知识点操作：{topic.name}")
            menu.requested.connect(lambda key, tid=topic.id: self.requested.emit(key, tid))
            head.addWidget(menu)
        root.addLayout(head)
        root.addWidget(route_label(f"预计 {topic.minutes} 分钟"))
        if topic.activities:
            root.addWidget(route_label(topic.activities))
        self.body_layout.addWidget(row)


class RouteDetailSummary(QWidget):
    def __init__(self, *, goal: str, phase: str, topic: str, next_activity: str,
                 status: str, course_label: str, course_percent: int | None,
                 mastery: str, capability: str):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XS, spacing.XS, spacing.XS, spacing.SM)
        root.setSpacing(spacing.SM)
        head = QHBoxLayout()
        head.addWidget(route_label(goal or "尚未设置学习目标"), 1)
        head.addWidget(SAStatusBadge(status))
        root.addLayout(head)
        root.addWidget(route_label(f"当前阶段：{phase}"))
        root.addWidget(route_label(f"当前知识点：{topic}", "RouteCurrentTopic"))
        root.addWidget(route_label(f"下一步：{next_activity}"))
        if course_percent is None:
            root.addWidget(route_label(course_label))
        else:
            root.addWidget(SAProgressBar(course_percent, course_label))
        metrics = FlowWidget(h_spacing=spacing.LG, v_spacing=spacing.XS)
        for text in (f"掌握度：{mastery}", f"能力证据：{capability}"):
            label = route_label(text)
            label.setWordWrap(False)
            metrics.add_widget(label)
        root.addWidget(metrics)
