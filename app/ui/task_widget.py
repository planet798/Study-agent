"""单任务轻量列表项。

纯展示层：保留任务状态、信号和业务操作，只组织视觉呈现。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..database.repository import Task
from ..database.schema import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_NOT_DONE,
)
from .components.button import SAButton
from .components.tag import SATag

_PRIORITY_TEXT = {
    PRIORITY_LOW: "低",
    PRIORITY_MEDIUM: "中",
    PRIORITY_HIGH: "高",
}

POSTPONE_WARNING = "该任务已经连续延期 3 次，请考虑拆分任务或调整计划。"


def format_minutes(minutes: int) -> str:
    """把预计分钟数格式化成中文可读文本。"""
    if minutes <= 0:
        return "未设置"
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} 小时 {m} 分"
    if h:
        return f"{h} 小时"
    return f"{m} 分钟"


class TaskWidget(QFrame):
    """一个保持完整交互的轻量任务行。"""

    complete_requested = Signal(int)
    not_done_requested = Signal(int)
    postpone_requested = Signal(int)
    remove_requested = Signal(int)
    assessment_requested = Signal(int)
    start_study_requested = Signal(int)

    def __init__(
        self,
        task: Task,
        parent: QWidget | None = None,
        assessment_label: str = "开始验收",
        route_name: str | None = None,
        agent_enabled: bool = False,
        agent_label: str = "开始学习",
    ):
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self._task = task
        self._assessment_label = assessment_label
        self._route_name = route_name
        self._agent_enabled = bool(agent_enabled)
        self._agent_label = agent_label

        self._build_ui()
        self.render(task)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 10, 2, 12)
        root.setSpacing(6)

        # 任务标题优先；metadata 以纯文字呈现，避免嵌套彩色胶囊。
        self.title_label = QLabel("")
        self.title_label.setObjectName("TaskTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.route_tag_label = SATag("", "neutral")
        self.activity_tag_label = SATag("", "neutral")
        self.source_tag_label = SATag("", "neutral")
        self.time_label = QLabel("")
        self.priority_label = QLabel("")
        self.category_label = QLabel("")
        for label in (
            self.route_tag_label,
            self.activity_tag_label,
            self.source_tag_label,
            self.time_label,
            self.priority_label,
            self.category_label,
        ):
            if not isinstance(label, SATag):
                label.setObjectName("TaskMeta")
            meta_row.addWidget(label)
        meta_row.addStretch()
        root.addLayout(meta_row)

        self.desc_label = QLabel("")
        self.desc_label.setObjectName("TaskDesc")
        self.desc_label.setWordWrap(True)
        self.desc_label.setVisible(False)
        root.addWidget(self.desc_label)

        self.reason_label = QLabel("")
        self.reason_label.setObjectName("TaskReason")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)
        root.addWidget(self.reason_label)

        self.warning_label = QLabel(POSTPONE_WARNING)
        self.warning_label.setObjectName("PostponeWarning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        root.addWidget(self.warning_label)

        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(6)
        root.addLayout(self.action_row)

    @staticmethod
    def _can_assess(task: Task) -> bool:
        return task.knowledge_point_id is not None or task.topic_id is not None

    def _add_assessment_button(self) -> None:
        if getattr(self, "assessment_btn", None) is not None:
            return
        self.assessment_btn = SAButton(
            self._assessment_label, variant="secondary", size="small"
        )
        self.assessment_btn.clicked.connect(
            lambda: self.assessment_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.assessment_btn)

    @staticmethod
    def _source_tag(task: Task) -> str:
        """轻量来源标记：Agent 规划 / 手动学习 / 知识学习。"""
        if task.source == "generated":
            return "Agent 规划"
        if task.source == "manual":
            if task.knowledge_point_id is not None or task.topic_id is not None:
                return "知识学习"
            return "手动学习"
        return ""

    def _add_remove_button(self) -> None:
        self.remove_btn = SAButton("移除今日任务", variant="subtle", size="small")
        self.remove_btn.clicked.connect(
            lambda: self.remove_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.remove_btn)

    def _add_action_buttons(self) -> None:
        """active 状态；Agent 可用时优先开始任务型学习。"""
        if self._agent_enabled:
            self.start_study_btn = SAButton(
                self._agent_label, variant="primary", size="small"
            )
            self.start_study_btn.clicked.connect(
                lambda: self.start_study_requested.emit(self._task.id)
            )
            self.action_row.addWidget(self.start_study_btn)
            self.complete_btn = SAButton("完成", variant="secondary", size="small")
        else:
            self.complete_btn = SAButton("完成", variant="primary", size="small")
        self.complete_btn.clicked.connect(
            lambda: self.complete_requested.emit(self._task.id)
        )
        self.not_done_btn = SAButton("未完成", variant="danger", size="small")
        self.not_done_btn.clicked.connect(
            lambda: self.not_done_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.complete_btn)
        if self._can_assess(self._task):
            self._add_assessment_button()
        self.action_row.addWidget(self.not_done_btn)
        self._add_remove_button()
        self.action_row.addStretch()

    def _add_done_state(self) -> None:
        """完成不等于掌握；正式任务仍保留验收入口。"""
        self.done_label = QLabel("已完成")
        self.done_label.setObjectName("DoneBadge")
        self.action_row.addWidget(self.done_label)
        if self._can_assess(self._task):
            self._add_assessment_button()
        self.action_row.addStretch()

    def _add_not_done_state(self) -> None:
        self.postpone_btn = SAButton("延期到明天", variant="secondary", size="small")
        self.postpone_btn.clicked.connect(
            lambda: self.postpone_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.postpone_btn)
        self.action_row.addStretch()

    def render(self, task: Task) -> None:
        """根据 Task 刷新可见文本、状态与操作。"""
        self._task = task
        self.title_label.setText(task.title)
        self.title_label.setVisible(bool(task.title))

        source_tag = self._source_tag(task)
        self.source_tag_label.setText(source_tag)
        self.source_tag_label.setVisible(bool(source_tag))

        if task.route_id is None:
            route_text = "未分类"
            route_variant = "neutral"
        else:
            route_text = self._route_name or f"路线{task.route_id}"
            route_variant = "accent"
        self.route_tag_label.setText(route_text)
        self.route_tag_label.setToolTip(route_text)
        self.route_tag_label.set_variant(route_variant)
        self.route_tag_label.setVisible(True)

        activity = getattr(task, "learning_activity_kind", None)
        if activity:
            from ..services.learning_activity import activity_label

            self.activity_tag_label.setText(activity_label(activity))
            self.activity_tag_label.setVisible(True)
        else:
            self.activity_tag_label.setVisible(False)

        has_desc = bool((task.description or "").strip())
        self.desc_label.setVisible(has_desc)
        if has_desc:
            self.desc_label.setText(task.description)

        prio = _PRIORITY_TEXT.get(task.priority, "?")
        self.time_label.setText(format_minutes(task.estimated_minutes))
        self.priority_label.setText(f"优先级 {prio}")
        category = (task.category or "").strip()
        self.category_label.setText(f"分类：{category}" if category else "")
        self.category_label.setVisible(bool(category))

        self.setProperty("done", "true" if task.status == STATUS_DONE else "false")
        self.setProperty("postponing", "true" if task.postpone_count >= 1 else "false")
        self.style().unpolish(self)
        self.style().polish(self)

        self._clear_action_row()
        self.reason_label.setVisible(False)
        if task.status == STATUS_ACTIVE:
            self._add_action_buttons()
        elif task.status == STATUS_DONE:
            self._add_done_state()
        elif task.status == STATUS_CANCELLED:
            self.cancelled_label = QLabel("已移除（今日不再执行）")
            self.cancelled_label.setObjectName("TaskMeta")
            self.action_row.addWidget(self.cancelled_label)
            self.action_row.addStretch()
        elif task.status == STATUS_NOT_DONE:
            self.reason_label.setVisible(True)
            self.reason_label.setText(f"未完成原因：{task.reason or ''}")
            self._add_not_done_state()

        self.warning_label.setVisible(
            task.status != STATUS_DONE and task.postpone_count >= 3
        )

    def _clear_action_row(self) -> None:
        while self.action_row.count():
            item = self.action_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for name in (
            "assessment_btn", "complete_btn", "not_done_btn", "remove_btn",
            "postpone_btn", "done_label", "cancelled_label", "start_study_btn",
        ):
            if hasattr(self, name):
                delattr(self, name)

    def task(self) -> Task:
        return self._task
