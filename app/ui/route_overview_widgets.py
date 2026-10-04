"""路线概览的纯视图：只展示快照，通过信号请求操作。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from .components.button import SAButton
from .components.flow_layout import FlowWidget
from .components.progress_bar import SAProgressBar
from .components.status_badge import SAStatusBadge
from .design import spacing


from .components.workspace_sections import ActionsMenuButton, plain_label as route_label


class RouteActionsButton(ActionsMenuButton):
    """保留路线菜单的既有导入入口与可访问标识。"""

    def __init__(self, actions, parent=None):
        super().__init__(actions, parent)
        self.setAccessibleName("更多路线操作")
        self.menu().setObjectName("RouteActionsMenu")


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
