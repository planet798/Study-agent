"""手动添加今日学习任务对话框（Phase A）。

两种任务：
- 普通学习任务（manual todo）：不关联知识点，不进入验收链路；
- 正式知识学习任务（manual knowledge）：
    A. 关联已有 study_topic；
    B. 新建临时知识点（不污染 study_phases）。

本阶段没有 LearningRoute，因此不提供路线选择 / 父路线 / Planner。
"""

from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .styles import apply_secondary_button_text

KIND_TODO = "todo"
KIND_KNOWLEDGE = "knowledge"
SOURCE_EXISTING_TOPIC = "existing_topic"
SOURCE_TEMP_KNOWLEDGE = "temp_knowledge"


class AddLearningTaskDialog(QDialog):
    def __init__(self, topics=None, default_date: str | None = None, parent=None):
        """topics: [{"id": int, "name": str}, ...] 供“关联已有 Topic”下拉。"""
        super().__init__(parent)
        self._topics = list(topics or [])
        self.setWindowTitle("添加学习任务")
        self.setModal(True)
        self.resize(520, 460)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # 任务类型
        type_title = QLabel("任务类型")
        type_title.setObjectName("SectionTitle")
        root.addWidget(type_title)
        self.todo_radio = QRadioButton("普通学习任务")
        self.knowledge_radio = QRadioButton("正式知识学习任务")
        self.todo_radio.setChecked(True)
        root.addWidget(self.todo_radio)
        root.addWidget(self.knowledge_radio)
        self.todo_radio.toggled.connect(self._on_type_changed)

        # 标题
        root.addWidget(QLabel("标题 *"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：刷 LeetCode 3 道 / 强化学习基础")
        root.addWidget(self.title_edit)

        # 描述
        root.addWidget(QLabel("描述"))
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("可选：本次学习的范围 / 目标")
        self.desc_edit.setFixedHeight(80)
        root.addWidget(self.desc_edit)

        # 预计时间 + 日期
        row = QHBoxLayout()
        row.addWidget(QLabel("预计时间（分钟）"))
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 600)
        self.minutes_spin.setSingleStep(5)
        self.minutes_spin.setValue(30)
        row.addWidget(self.minutes_spin)
        row.addSpacing(12)
        row.addWidget(QLabel("日期"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        d = QDate.fromString(default_date or "", "yyyy-MM-dd")
        self.date_edit.setDate(d if d.isValid() else QDate.currentDate())
        row.addWidget(self.date_edit)
        row.addStretch()
        root.addLayout(row)

        # 知识来源（仅正式知识学习任务可见）
        self.knowledge_box = QWidget()
        kbox = QVBoxLayout(self.knowledge_box)
        kbox.setContentsMargins(0, 4, 0, 0)
        kbox.setSpacing(4)
        kbox.addWidget(QLabel("知识来源"))
        self.topic_radio = QRadioButton("关联已有 Topic")
        self.topic_combo = QComboBox()
        self.topic_combo.setEditable(True)
        for t in self._topics:
            self.topic_combo.addItem(t.get("name") or f"Topic {t.get('id')}", t.get("id"))
        if not self._topics:
            self.topic_radio.setEnabled(False)
            self.topic_combo.setEnabled(False)
        self.temp_radio = QRadioButton("新建临时知识点（不绑定现有阶段）")
        self.topic_radio.setChecked(bool(self._topics))
        self.temp_radio.setChecked(not self._topics)
        kbox.addWidget(self.topic_radio)
        kbox.addWidget(self.topic_combo)
        kbox.addWidget(self.temp_radio)
        root.addWidget(self.knowledge_box)
        self.knowledge_box.setVisible(False)

        root.addStretch()

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
        self.ok_btn = QPushButton("添加")
        self.ok_btn.setObjectName("SecondaryButton")
        apply_secondary_button_text(self.ok_btn)
        self.ok_btn.clicked.connect(self._on_confirm)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.ok_btn)
        root.addLayout(btns)

    # ---------- 状态 ----------

    def _on_type_changed(self) -> None:
        is_knowledge = self.knowledge_radio.isChecked()
        self.knowledge_box.setVisible(is_knowledge)

    def _title(self) -> str:
        return self.title_edit.text().strip()

    def _on_confirm(self) -> None:
        if not self._title():
            self.error_label.setText("标题不能为空。")
            self.error_label.setVisible(True)
            return
        if self.knowledge_radio.isChecked() and self.topic_radio.isChecked():
            if self.topic_combo.currentData() is None:
                self.error_label.setText("请选择一个已有 Topic，或改为新建临时知识点。")
                self.error_label.setVisible(True)
                return
        self.accept()

    def result_payload(self) -> dict:
        is_knowledge = self.knowledge_radio.isChecked()
        payload = {
            "kind": KIND_KNOWLEDGE if is_knowledge else KIND_TODO,
            "title": self._title(),
            "description": self.desc_edit.toPlainText().strip(),
            "estimated_minutes": int(self.minutes_spin.value()),
            "scheduled_date": self.date_edit.date().toString("yyyy-MM-dd"),
            "topic_id": None,
        }
        if is_knowledge and self.topic_radio.isChecked():
            payload["topic_id"] = self.topic_combo.currentData()
        return payload
