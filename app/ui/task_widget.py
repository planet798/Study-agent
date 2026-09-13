"""单任务卡片组件。

职责：
- 纯展示层：根据 Task 对象渲染卡片内容；
- 通过信号（complete_requested / not_done_requested / postpone_requested）
  把用户操作抛给上层，自身不调用 service / repository。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..database.repository import Task
from ..database.schema import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_NOT_DONE,
)
from .styles import apply_secondary_button_text

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
    assessment_requested = Signal(int)  # 开始验收（Phase 7）


    def __init__(
        self,
        task: Task,
        parent: QWidget | None = None,
        assessment_label: str = "开始验收",
    ):
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self._task = task
        self._assessment_label = assessment_label

        # 便于测试定位
        self._build_ui()
        self.render(task)

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(4)

        # 第一行：标题 + 分类 + 优先级
        top = QHBoxLayout()
        self.title_label = QLabel("")
        self.title_label.setObjectName("TaskTitle")
        self.category_label = QLabel("")
        self.category_label.setObjectName("TaskMeta")
        self.priority_label = QLabel("")
        self.priority_label.setObjectName("TaskMeta")
        top.addWidget(self.title_label)
        top.addStretch()
        top.addWidget(self.category_label)
        top.addWidget(self.priority_label)
        root.addLayout(top)

        # 描述
        self.desc_label = QLabel("")
        self.desc_label.setObjectName("TaskDesc")
        self.desc_label.setWordWrap(True)
        self.desc_label.setVisible(False)
        root.addWidget(self.desc_label)

        # 预计时间
        self.time_label = QLabel("")
        self.time_label.setObjectName("TaskMeta")
        root.addWidget(self.time_label)

        # 原因（not_done 时显示）
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

        # 状态区（完成 / 未完成按钮或状态徽标）
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        root.addLayout(self.action_row)

    @staticmethod
    def _can_assess(task: Task) -> bool:
        """该任务是否应该展示验收入口。

        只要任务有 knowledge_point_id，或至少有 topic_id（可在点击时由
        安全网现场补 kp），就显示验收入口；无 topic 的 manual / 临时任务
        不显示，也不会被乱建 kp。
        """
        return (
            task.knowledge_point_id is not None or task.topic_id is not None
        )

    def _add_assessment_button(self) -> None:
        """统一添加验收入口（避免重复创建同一个按钮）。"""
        if getattr(self, "assessment_btn", None) is not None:
            return
        self.assessment_btn = QPushButton(self._assessment_label)
        self.assessment_btn.setObjectName("PrimaryButton")
        self.assessment_btn.clicked.connect(
            lambda: self.assessment_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.assessment_btn)

    def _add_action_buttons(self) -> None:
        """active 状态显示 [完成] [未完成]（可验收任务另加验收入口）。"""
        self.complete_btn = QPushButton("完成")
        self.complete_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.complete_btn)
        self.complete_btn.clicked.connect(
            lambda: self.complete_requested.emit(self._task.id)
        )
        self.not_done_btn = QPushButton("未完成")
        self.not_done_btn.setObjectName("DangerButton")
        self.not_done_btn.clicked.connect(
            lambda: self.not_done_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.complete_btn)
        self.action_row.addWidget(self.not_done_btn)
        if self._can_assess(self._task):
            self._add_assessment_button()
        self.action_row.addStretch()

    def _add_done_state(self) -> None:
        """done 状态显示：已完成。

        完成任务 ≠ 掌握知识：done 的正式/额外任务仍保留验收入口，
        验收才产生 mastery / weak_points / next_review_date。
        """
        self.done_label = QLabel("已完成")
        self.done_label.setObjectName("DoneBadge")
        self.action_row.addWidget(self.done_label)
        if self._can_assess(self._task):
            self._add_assessment_button()
        self.action_row.addStretch()

    def _add_not_done_state(self) -> None:
        """not_done 状态显示：原因 + [延期到明天]。"""
        self.postpone_btn = QPushButton("延期到明天")
        self.postpone_btn.setObjectName("PostponeButton")
        self.postpone_btn.clicked.connect(
            lambda: self.postpone_requested.emit(self._task.id)
        )
        self.action_row.addWidget(self.postpone_btn)
        self.action_row.addStretch()

    # ---------- 渲染 ----------

    def render(self, task: Task) -> None:
        """根据 Task 刷新卡片全部内容。"""
        self._task = task

        # 标题 + 分类 + 优先级
        self.title_label.setText(task.title)
        self.category_label.setText(f"分类：{task.category or '未分类'}")
        prio = _PRIORITY_TEXT.get(task.priority, "?")
        self.priority_label.setText(f"优先级：{prio}")

        # 描述
        has_desc = bool((task.description or "").strip())
        self.desc_label.setVisible(has_desc)
        if has_desc:
            self.desc_label.setText(task.description)

        # 预计时间
        self.time_label.setText(
            f"预计时间：{format_minutes(task.estimated_minutes)}"
        )

        # 卡片样式：完成 / 延期状态配色
        self.setProperty("done", "true" if task.status == STATUS_DONE else "false")
        self.setProperty("postponing", "true" if task.postpone_count >= 1 else "false")
        self.style().unpolish(self)
        self.style().polish(self)

        # 清理旧状态区（每次重渲染重建）
        self._clear_action_row()

        if task.status == STATUS_ACTIVE:
            self._add_action_buttons()
        elif task.status == STATUS_DONE:
            self._add_done_state()
        elif task.status == STATUS_NOT_DONE:
            self.reason_label.setVisible(True)
            self.reason_label.setText(f"未完成原因：{task.reason or ''}")
            self._add_not_done_state()

        # 延期警告（连续 >= 3 次）
        if task.status != STATUS_DONE and task.postpone_count >= 3:
            self.warning_label.setVisible(True)
        else:
            self.warning_label.setVisible(False)

    def _clear_action_row(self) -> None:
        while self.action_row.count():
            item = self.action_row.takeAt(0)
            w = item.widget()
            if w is not None:
                # 立即脱离父对象，避免 deleteLater 延迟删除期间：
                # 旧按钮仍作为子对象存在（重复显示 / findChildren 重复）。
                w.setParent(None)
                w.deleteLater()
        # 删除旧的按钮引用，避免重渲染时误判“已存在”而漏加，
        # 同时让 hasattr(widget, "assessment_btn") 真实反映是否展示了验收入口。
        for name in ("assessment_btn", "complete_btn", "not_done_btn"):
            if hasattr(self, name):
                delattr(self, name)

    def task(self) -> Task:
        return self._task
