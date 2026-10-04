"""实践概览的纯视图与四类独立摘要。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from .components.button import SAButton
from .components.flow_layout import FlowWidget
from .components.status_badge import SAStatusBadge
from .components.workspace_sections import ActionsMenuButton, plain_label
from .design import spacing


@dataclass(frozen=True)
class ProjectMetrics:
    milestones: str
    outputs: str
    evidence: str
    readiness: str


class ProjectMetricsView(FlowWidget):
    def __init__(self, metrics: ProjectMetrics):
        super().__init__(h_spacing=spacing.LG, v_spacing=spacing.SM)
        for name, value in (("里程碑", metrics.milestones), ("成果", metrics.outputs),
                            ("项目能力证据", metrics.evidence), ("学习准备度", metrics.readiness)):
            label = plain_label(f"{name}：{value}")
            label.setWordWrap(False)
            self.add_widget(label)


class PracticeOverviewRow(QFrame):
    requested = Signal(str)

    def __init__(self, *, name: str, status: str, project_type: str,
                 routes: list[str], metrics: ProjectMetrics):
        super().__init__()
        self.setObjectName("PracticeOverviewRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.LG, spacing.LG, spacing.LG, spacing.LG)
        root.setSpacing(spacing.SM)
        head = QHBoxLayout()
        head.addWidget(plain_label(name, "TaskTitle"), 1)
        head.addWidget(SAStatusBadge(status))
        view = SAButton("查看项目", variant="secondary", size="small")
        view.clicked.connect(lambda: self.requested.emit("open"))
        head.addWidget(view)
        actions = [("edit", "编辑项目"), ("restore", "恢复项目") if status == "archived"
                   else ("archive", "归档项目")]
        more = ActionsMenuButton(actions)
        more.setAccessibleName(f"更多项目操作：{name}")
        more.requested.connect(self.requested.emit)
        head.addWidget(more)
        root.addLayout(head)
        root.addWidget(plain_label(project_type))
        if routes:
            root.addWidget(plain_label("关联路线：" + " · ".join(routes)))
        root.addWidget(ProjectMetricsView(metrics))
