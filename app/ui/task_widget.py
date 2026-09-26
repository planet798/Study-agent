"""单任务卡片组件。

职责：
- 纯展示层：根据 Task 对象渲染卡片内容；
- 通过信号（complete_requested / not_done_requested / postpone_requested）
  把用户操作抛给上层，自身不调用 service / repository。

UI-3：
- 标签统一用 SATag（不再用 【】 文本前缀 + 单一 ReviewTag）；
- 操作按钮迁移到 SAButton（primary / secondary / danger / subtle）；
- Done ≠ Mastery：done 但仍可验收的文案保持不变。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

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
    """一个任务卡片。"""

    # 用户操作信号（task_id）
    complete_requested = Signal(int)
    not_done_requested = Signal(int)
    postpone_requested = Signal(int)
    remove_requested = Signal(int)  # 移除今日任务（Phase A）
    assessment_requested = Signal(int)  # 开始验收（Phase 7）

    def __init__(
        self,
        task: Task,
        parent: QWidget | None = None,
        assessment_label: str = "开始验收",
        route_name: str | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self._task = task
        self._assessment_label = assessment_label
        self._route_name = route_name

        self._build_ui()
        self.render(task)

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        # 标签行：路线 / 学习活动 / 来源
        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        self.route_tag_label = SATag("", "accent")
        self.activity_tag_label = SATag("", "info")
        self.source_tag_label = SATag("", "neutral")
        for tag in (
            self.route_tag_label,
            self.activity_tag_label,
            self.source_tag_label,
        ):
            tag_row.addWidget(tag)
        tag_row.addStretch()
        root.addLayout(tag_row)

        # 标题
        self.title_label = QLabel("")
        self.title_label.setObjectName("TaskTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        # 描述
        self.desc_label = QLabel("")
        self.desc_label.setObjectName("TaskDesc")
        self.desc_label.setWordWrap(True)
        self.desc_label.setVisible(False)
        root.addWidget(self.desc_label)

        # 元信息：时间 · 优先级 · 分类
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.time_label = QLabel("")
        self.time_label.setObjectName("TaskMeta")
        self.priority_label = QLabel("")
        self.priority_label.setObjectName("TaskMeta")
        self.category_label = QLabel("")
        self.category_label.setObjectName("TaskMeta")
        meta_row.addWidget(self.time_label)
        meta_row.addWidget(self.priority_label)
        meta_row.addStretch()
        meta_row.addWidget(self.category_label)
        root.addLayout(meta_row)

        # 未完成原因（not_done 时显示）
        self.reason_label = QLabel("")
        self.reason_label.setObjectName("TaskReason")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)
        root.addWidget(self.reason_label)

        # 延期警告
        self.warning_label = QLabel(POSTPONE_WARNING)
        self.warning_label.setObjectName("PostponeWarning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        root.addWidget(self.warning_label)

        # 操作区（每次重渲染重建）
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        root.addLayout(self.action_row)

    @staticmethod
    def _can_assess(task: Task) -> bool:
        """该任务是否应该展示验收入口。"""
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
        """轻量来源标签：Agent 规划 / 自定义 / 自定义知识。"""
        if task.source == "generated":
            return "Agent 规划"
        if task.source == "manual":
            if task.knowledge_point_id is not None or task.topic_id is not None:
                return "自定义知识"
            return "自定义"
        return ""

    def _add_remove_button(self) -> None:
        """移除今日任务。"""
        self.remove_btn = SAButton("移除今日任务", variant="subtle", size="small")
        self.remove_btn.clicked.connect(
            lambda: self.remove_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.remove_btn)

    def _add_action_buttons(self) -> None:
        """active 状态：[完成] [开始验收] [未完成] [移除]。"""
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
        """done 状态：已完成 + （可验收则）验收入口。

        完成任务 ≠ 掌握知识：done 的正式/额外任务仍保留验收入口。
        """
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

    # ---------- 渲染 ----------

    def render(self, task: Task) -> None:
        """根据 Task 刷新卡片全部内容。"""
        self._task = task

        self.title_label.setText(task.title)
        self.title_label.setVisible(bool(task.title))

        # 来源标签
        source_tag = self._source_tag(task)
        self.source_tag_label.setText(source_tag)
        self.source_tag_label.setVisible(bool(source_tag))

        # 路线标签：非 NULL 显示路线名，NULL 显示未分类
        if task.route_id is None:
            route_text = "未分类"
            route_variant = "neutral"
        else:
            route_text = self._route_name or f"路线{task.route_id}"
            route_variant = "accent"
        self.route_tag_label.setText(route_text)
        self.route_tag_label.set_variant(route_variant)
        self.route_tag_label.setVisible(True)

        # 学习活动标签
        activity = getattr(task, "learning_activity_kind", None)
        if activity:
            from ..services.learning_activity import activity_label

            self.activity_tag_label.setText(activity_label(activity))
            self.activity_tag_label.setVisible(True)
        else:
            self.activity_tag_label.setVisible(False)

        # 描述
        has_desc = bool((task.description or "").strip())
        self.desc_label.setVisible(has_desc)
        if has_desc:
            self.desc_label.setText(task.description)

        # 元信息
        prio = _PRIORITY_TEXT.get(task.priority, "?")
        self.time_label.setText(format_minutes(task.estimated_minutes))
        self.priority_label.setText(f"优先级 {prio}")
        category = (task.category or "").strip()
        self.category_label.setText(f"分类：{category}" if category else "")
        self.category_label.setVisible(bool(category))

        # 卡片样式：完成 / 延期状态配色
        self.setProperty("done", "true" if task.status == STATUS_DONE else "false")
        self.setProperty("postponing", "true" if task.postpone_count >= 1 else "false")
        self.style().unpolish(self)
        self.style().polish(self)

        # 清理旧状态区（每次重渲染重建）
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

        # 延期警告（连续 >= 3 次）
        self.warning_label.setVisible(
            task.status != STATUS_DONE and task.postpone_count >= 3
        )

    def _clear_action_row(self) -> None:
        while self.action_row.count():
            item = self.action_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for name in ("assessment_btn", "complete_btn", "not_done_btn", "remove_btn",
                     "postpone_btn", "done_label", "cancelled_label"):
            if hasattr(self, name):
                delattr(self, name)

    def task(self) -> Task:
        return self._task
