"""昨日 / 历史学习任务补确认对话框。

在进入新一天学习前，若存在“今天以前仍未明确处理”的正式新知识任务，
必须先让用户逐条选择【已完成】/【未完成】。全部选择后才能提交。

安全：
- 本对话框只收集选择，不直接写数据库（提交由
  PastTaskConfirmationService 复用现有 TaskService 完成）；
- 关闭（X / Esc / 取消）不会默认判为未完成，也不会进入今天流程；
- 提交前按钮 disabled。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..services.past_task_service import DECISION_DONE, DECISION_NOT_DONE
from .styles import apply_secondary_button_text


class PastTaskConfirmationDialog(QDialog):
    def __init__(self, tasks, parent=None, route_names=None):
        super().__init__(parent)
        self._tasks = list(tasks)
        self._route_names = dict(route_names or {})
        # task_id -> (radio_done, radio_not_done)
        self._radios: dict[int, tuple[QRadioButton, QRadioButton]] = {}

        dates = {t.scheduled_date for t in self._tasks}
        if len(dates) == 1:
            self.setWindowTitle("昨日学习任务确认")
        else:
            self.setWindowTitle("历史学习任务确认")
        self.setModal(True)
        self.resize(600, 560)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        title = QLabel(self.windowTitle())
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        hint = QLabel(
            "检测到之前有学习任务尚未确认完成状态。请根据实际学习情况补充确认，"
            "完成后才能继续今日学习。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("TaskMeta")
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(10)

        last_date = None
        for t in self._tasks:
            if t.scheduled_date != last_date:
                date_lbl = QLabel(t.scheduled_date)
                date_lbl.setObjectName("TaskTitle")
                col.addWidget(date_lbl)
                last_date = t.scheduled_date

            card = QWidget()
            card.setObjectName("TaskCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.setSpacing(4)

            name = QLabel(t.title)
            name.setWordWrap(True)
            card_layout.addWidget(name)

            if t.route_id is None:
                route_text = "未分类"
            else:
                route_text = self._route_names.get(
                    t.route_id, f"路线{t.route_id}"
                )
            route_lbl = QLabel(f"【{route_text}】")
            route_lbl.setObjectName("ReviewTag")
            card_layout.addWidget(route_lbl)

            minutes = int(getattr(t, "estimated_minutes", 0) or 0)
            meta = QLabel(f"预计 {minutes} 分钟" if minutes else "预计时间：—")
            meta.setObjectName("TaskMeta")
            card_layout.addWidget(meta)

            choices = QHBoxLayout()
            r_done = QRadioButton("已完成")
            r_not = QRadioButton("未完成")
            choices.addWidget(r_done)
            choices.addWidget(r_not)
            choices.addStretch()
            card_layout.addLayout(choices)

            self._radios[t.id] = (r_done, r_not)
            r_done.toggled.connect(self._update_submit_state)
            r_not.toggled.connect(self._update_submit_state)
            col.addWidget(card)

        col.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        btns = QHBoxLayout()
        btns.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.cancel_btn)
        self.cancel_btn.clicked.connect(self.reject)
        self.submit_btn = QPushButton("确认并进入今天")
        self.submit_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.submit_btn)
        self.submit_btn.setEnabled(False)
        self.submit_btn.clicked.connect(self._on_confirm)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.submit_btn)
        root.addLayout(btns)

        self._update_submit_state()

    # ---------- 状态 ----------

    def all_answered(self) -> bool:
        return all(
            r_done.isChecked() or r_not.isChecked()
            for r_done, r_not in self._radios.values()
        )

    def _update_submit_state(self) -> None:
        self.submit_btn.setEnabled(self.all_answered())

    def result_decisions(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for task_id, (r_done, r_not) in self._radios.items():
            if r_done.isChecked():
                out[task_id] = DECISION_DONE
            elif r_not.isChecked():
                out[task_id] = DECISION_NOT_DONE
        return out

    def _on_confirm(self) -> None:
        if not self.all_answered():
            self.error_label.setText("请为每个任务选择「已完成」或「未完成」。")
            self.error_label.setVisible(True)
            return
        self.accept()

    # ---------- 关闭语义 ----------

    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        # Esc 等价于取消（不默认判未完成、不进入今天）
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
