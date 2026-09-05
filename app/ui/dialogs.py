"""对话框组件。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NotDoneDialog(QDialog):
    """填写"未完成原因"的对话框。

    规则：
    - 原因文本不能为空，否则不允许提交并提示；
    - 提交成功后通过 accepted/属性读取 reason。
    """

    def __init__(self, task_title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("标记未完成")
        self.setModal(True)
        self.resize(420, 240)

        self.task_title = task_title

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel(f"任务「{task_title}」为什么没有完成？")
        layout.addWidget(hint)

        self.reason_edit = QPlainTextEdit()
        self.reason_edit.setPlaceholderText("请填写未完成原因（必填）")
        layout.addWidget(self.reason_edit)

        self.error_label = QLabel("")
        self.error_label.setObjectName("QErrorMessage")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("提交")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_submit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def reason(self) -> str:
        """读取用户填写的原因（已去首尾空白）。"""
        return self.reason_edit.toPlainText().strip()

    def _on_submit(self) -> None:
        """提交按钮：原因非空才能关闭对话框。"""
        if not self.reason():
            self.error_label.setText("原因不能为空，请填写后再提交。")
            self.error_label.setVisible(True)
            self.reason_edit.setFocus()
            return
        self.accept()

    @staticmethod
    def get_reason(parent: QWidget, task_title: str) -> str | None:
        """便捷方法：弹出对话框，返回原因；用户取消返回 None。"""
        dlg = NotDoneDialog(task_title, parent=parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.reason()
        return None


def show_warning(parent: QWidget, message: str, title: str = "Study Agent") -> None:
    """非致命提醒（非空校验失败等）。"""
    box = QMessageBox(QMessageBox.Icon.Warning, title, message, parent=parent)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText("知道了")
    box.exec()


class AIReviewDialog(QDialog):
    """展示 AI 复核结果，并让用户决定是否延期。

    流程：
    - AI 调用中：显示"正在分析……"
    - AI 成功：显示"AI 判断" + 合理程度/建议延期/分析/建议
    - AI 不可用：显示"AI 暂时不可用"，仍保留手动延期按钮

    决定性操作（延期与否）永远由用户拍板，AI 只提供建议。
    """

    postpone_requested = Signal(int)
    no_postpone_requested = Signal(int)

    def __init__(
        self,
        task,
        parent: QWidget | None = None,
        show_loading: bool = True,
    ):
        super().__init__(parent)
        self.task = task
        self.setWindowTitle("AI 判断")
        self.setModal(False)  # 非模态，允许用户在分析期间继续操作/关闭窗口
        self.resize(460, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(f"«{task.title}» 未完成原因复核")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        # 加载中提示
        self.loading_label = QLabel("正在分析……")
        self.loading_label.setObjectName("PostponeWarning")
        layout.addWidget(self.loading_label)

        # 结果区
        self.reasonable_label = QLabel("合理程度：--")
        self.postpone_label = QLabel("建议延期：--")
        self.suggested_date_label = QLabel("建议日期：--")
        self.analysis_label = QLabel("分析：--")
        self.analysis_label.setWordWrap(True)
        self.suggestion_label = QLabel("建议：--")
        self.suggestion_label.setWordWrap(True)
        for w in (
            self.reasonable_label,
            self.postpone_label,
            self.suggested_date_label,
            self.analysis_label,
            self.suggestion_label,
        ):
            w.setObjectName("TaskMeta")
            layout.addWidget(w)

        # AI 不可用提示
        self.error_label = QLabel("")
        self.error_label.setObjectName("QErrorMessage")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addStretch()

        # 操作按钮：AI 只是辅助，用户始终能手动决定
        self.postpone_btn = QPushButton("延期到明天")
        self.postpone_btn.setObjectName("PostponeButton")
        self.no_postpone_btn = QPushButton("不延期")
        self.no_postpone_btn.clicked.connect(self._on_no_postpone)
        self.postpone_btn.clicked.connect(self._on_postpone)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.no_postpone_btn)
        btn_row.addWidget(self.postpone_btn)
        layout.addLayout(btn_row)

        self._result_ready = False
        if show_loading:
            self.show_loading()

    # ---------- 状态 ----------

    def show_loading(self) -> None:
        """显示"正在分析……"，隐藏结果/错误区。"""
        self.loading_label.setVisible(True)
        for w in (
            self.reasonable_label,
            self.postpone_label,
            self.suggested_date_label,
            self.analysis_label,
            self.suggestion_label,
        ):
            w.setVisible(False)
        self.error_label.setVisible(False)
        self._result_ready = False

    def show_result(self, review) -> None:
        """展示合法复核结果。"""
        self.loading_label.setVisible(False)
        self.error_label.setVisible(False)
        self.reasonable_label.setText(f"合理程度：{int(round(review.score * 100))}%")
        postpone_text = "是" if review.should_postpone else "否"
        self.postpone_label.setText(f"建议延期：{postpone_text}")
        date_text = review.suggested_date or "--"
        self.suggested_date_label.setText(f"建议日期:{date_text}")
        self.analysis_label.setText(f"分析：{review.analysis}")
        self.suggestion_label.setText(f"建议：{review.suggestion}")
        for w in (
            self.reasonable_label,
            self.postpone_label,
            self.suggested_date_label,
            self.analysis_label,
            self.suggestion_label,
        ):
            w.setVisible(True)
        self._result_ready = True

    def show_unavailable(self, message: str) -> None:
        """AI 不可用：保留手动操作，仅提示。"""
        self.loading_label.setVisible(False)
        for w in (
            self.reasonable_label,
            self.postpone_label,
            self.suggested_date_label,
            self.analysis_label,
            self.suggestion_label,
        ):
            w.setVisible(False)
        self.error_label.setText(f"AI 暂时不可用：{message}")
        self.error_label.setVisible(True)
        self._result_ready = False

    # ---------- 用户操作 ----------

    def _on_postpone(self) -> None:
        self.postpone_requested.emit(self.task.id)
        self.accept()

    def _on_no_postpone(self) -> None:
        self.no_postpone_requested.emit(self.task.id)
        self.accept()

    def has_result(self) -> bool:
        return self._result_ready

