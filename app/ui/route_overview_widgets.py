"""路线概览的纯视图：只展示快照，通过信号请求操作。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout

from .components.button import SAButton
from .components.flow_layout import FlowWidget
from .components.progress_bar import SAProgressBar
from .components.status_badge import SAStatusBadge
from .design import spacing


def route_label(text: str, role: str = "TaskMeta") -> QLabel:
    label = QLabel(text)
    label.setObjectName(role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class RouteActionsButton(SAButton):
    """带语义操作 key 的菜单；菜单不执行业务写入。"""

    requested = Signal(str)

    def __init__(self, actions: list[tuple[str, str]], parent=None):
        super().__init__("更多", variant="subtle", size="small", parent=parent)
        self.setAccessibleName("更多路线操作")
        menu = QMenu(self)
        menu.setObjectName("RouteActionsMenu")
        for key, text in actions:
            action = menu.addAction(text)
            action.setData(key)
            action.triggered.connect(
                lambda _checked=False, k=key: self.requested.emit(k)
            )
        self.setMenu(menu)


@dataclass(frozen=True)
class RouteOverview:
    name: str
    key: str
    status: str
    phase: str
    topic: str
    next_activity: str
    priority: str
    course_label: str
    course_percent: int | None
    mastery: str
    capability: str


class RouteOverviewRow(QFrame):
    requested = Signal(str)

    def __init__(self, data: RouteOverview, actions: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("RouteOverviewRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.LG, spacing.LG, spacing.LG, spacing.LG)
        root.setSpacing(spacing.SM)

        head = QHBoxLayout()
        head.addWidget(route_label(data.name, "TaskTitle"), 1)
        if data.key:
            head.addWidget(route_label(data.key, "SARouteKey"))
        head.addWidget(SAStatusBadge(data.status))
        view = SAButton("查看路线", variant="secondary", size="small")
        view.clicked.connect(lambda: self.requested.emit("open"))
        head.addWidget(view)
        more = RouteActionsButton(actions)
        more.setAccessibleName(f"更多路线操作：{data.name}")
        more.requested.connect(self.requested.emit)
        head.addWidget(more)
        root.addLayout(head)

        root.addWidget(route_label(f"当前阶段：{data.phase}"))
        root.addWidget(route_label(f"当前知识点：{data.topic}", "RouteCurrentTopic"))
        root.addWidget(route_label(f"下一步：{data.next_activity}"))
        if data.course_percent is None:
            root.addWidget(route_label(data.course_label))
        else:
            root.addWidget(SAProgressBar(data.course_percent, data.course_label))

        metrics = FlowWidget(h_spacing=spacing.LG, v_spacing=spacing.XS)
        for text in (f"掌握度：{data.mastery}",
                     f"能力证据 Capability：{data.capability}",
                     f"优先级：{data.priority}"):
            label = route_label(text)
            label.setWordWrap(False)
            metrics.add_widget(label)
        root.addWidget(metrics)


class RouteGroupHeading(QFrame):
    requested = Signal(str)

    def __init__(self, name: str, summary: str, goal: str, parent=None):
        super().__init__(parent)
        self.setObjectName("RouteGroupHeading")
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XS, spacing.LG, spacing.XS, spacing.SM)
        head = QHBoxLayout()
        head.addWidget(route_label(name, "SectionTitle"), 1)
        more = RouteActionsButton([("archive", "归档分组")])
        more.requested.connect(self.requested.emit)
        head.addWidget(more)
        root.addLayout(head)
        root.addWidget(route_label(summary))
        if goal:
            root.addWidget(route_label(f"目标：{goal}"))
