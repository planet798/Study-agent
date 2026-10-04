"""实践详情的纯视图：折叠分区、里程碑和成果行。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .components.button import SAButton
from .components.status_badge import SAStatusBadge
from .components.workspace_sections import ActionsMenuButton, CollapsibleSection, plain_label
from .design import spacing
from .practice_overview_widgets import ProjectMetrics, ProjectMetricsView


class PracticeDetailSection(CollapsibleSection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setObjectName("PracticeDetailSection")


class PracticeDetailSummary(QWidget):
    def __init__(self, *, goal: str, status: str, project_type: str, metrics: ProjectMetrics):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XS, spacing.SM, spacing.XS, spacing.SM)
        root.setSpacing(spacing.SM)
        head = QHBoxLayout()
        head.addWidget(plain_label(goal or "尚未设置项目目标"), 1)
        head.addWidget(SAStatusBadge(status))
        root.addLayout(head)
        root.addWidget(plain_label(project_type))
        root.addWidget(ProjectMetricsView(metrics))


class PracticeMilestonesSection(PracticeDetailSection):
    requested = Signal(str, int)
    add_requested = Signal()

    def __init__(self, milestones: list[dict], *, expanded=True):
        super().__init__("milestones", "里程碑", f"{len(milestones)} 项", expanded=expanded)
        add = SAButton("新增里程碑", variant="secondary", size="small")
        add.clicked.connect(lambda: self.add_requested.emit())
        self.header_layout.insertWidget(self.header_layout.count() - 1, add)
        if not milestones:
            self.body_layout.addWidget(plain_label("尚未设置里程碑"))
        for milestone in milestones:
            self._add_row(milestone)

    def _add_row(self, milestone):
        row = QFrame()
        row.setObjectName("PracticeMilestoneRow")
        root = QVBoxLayout(row)
        root.setContentsMargins(spacing.SM, spacing.SM, spacing.SM, spacing.SM)
        head = QHBoxLayout()
        head.addWidget(plain_label(milestone['title'], "TaskTitle"), 1)
        head.addWidget(SAStatusBadge({"todo": "planned", "in_progress": "in_progress",
                                   "done": "completed"}[milestone['status']],
                                   text={"todo": "待开始", "in_progress": "进行中",
                                         "done": "已完成"}[milestone['status']]))
        if milestone['status'] != 'done':
            key, text = (("start", "开始") if milestone['status'] == 'todo'
                         else ("complete", "标记完成"))
            advance = SAButton(text, variant="secondary", size="small")
            advance.clicked.connect(lambda: self.requested.emit(key, milestone['id']))
            head.addWidget(advance)
        actions = [("edit", "编辑里程碑"), ("delete", "删除里程碑")]
        if milestone['status'] == 'done':
            actions.insert(0, ("reset", "重置为待开始"))
        more = ActionsMenuButton(actions)
        more.setAccessibleName(f"里程碑操作：{milestone['title']}")
        more.requested.connect(lambda key: self.requested.emit(key, milestone['id']))
        head.addWidget(more)
        root.addLayout(head)
        root.addWidget(plain_label(f"顺序 {milestone['order_index']}"))
        self.body_layout.addWidget(row)


class PracticeOutputsSection(PracticeDetailSection):
    requested = Signal(str, int)
    add_requested = Signal()

    def __init__(self, outputs: list[dict], *, expanded=True):
        super().__init__("outputs", "项目成果", f"{len(outputs)} 项", expanded=expanded)
        add = SAButton("新增成果", variant="secondary", size="small")
        add.clicked.connect(lambda: self.add_requested.emit())
        self.header_layout.insertWidget(self.header_layout.count() - 1, add)
        if not outputs:
            self.body_layout.addWidget(plain_label("暂无项目成果"))
        for output in outputs:
            self._add_row(output)

    def _add_row(self, output):
        row = QFrame()
        row.setObjectName("PracticeOutputRow")
        root = QVBoxLayout(row)
        root.setContentsMargins(spacing.SM, spacing.SM, spacing.SM, spacing.SM)
        root.setSpacing(spacing.XS)
        head = QHBoxLayout()
        head.addWidget(plain_label(output['title'], "TaskTitle"), 1)
        more = ActionsMenuButton([("edit", "编辑成果"), ("delete", "删除成果")])
        more.setAccessibleName(f"成果操作：{output['title']}")
        more.requested.connect(lambda key: self.requested.emit(key, output['id']))
        head.addWidget(more)
        root.addLayout(head)
        root.addWidget(plain_label(output['type_label']))
        if output.get('uri'):
            uri = plain_label(output['uri'])
            uri.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                        | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            uri.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            uri.setAccessibleName(f"成果地址：{output['title']}")
            root.addWidget(uri)
        self.body_layout.addWidget(row)
