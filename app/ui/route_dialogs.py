"""学习路线相关对话框（Phase C）。

- CreateLearningRouteDialog：手动创建学习路线 / 分组；
- AddPhaseDialog：为某条路线的手动计划添加阶段；
- AddTopicDialog：为某阶段添加知识点。

本阶段不做 AI 生成路线。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .styles import apply_secondary_button_text

PRIORITY_LABELS = {1: "低", 2: "较低", 3: "中", 4: "高", 5: "最高"}


def priority_text(priority: int) -> str:
    p = int(priority or 3)
    return f"{PRIORITY_LABELS.get(p, '中')}（{p}）"


def priority_stars(priority: int) -> str:
    p = max(1, min(5, int(priority or 3)))
    return "★" * p + "☆" * (5 - p)


class CreateLearningRouteDialog(QDialog):
    """手动创建路线；默认 learning，可高级切换为 group。"""

    def __init__(self, groups=None, parent=None):
        super().__init__(parent)
        self._groups = list(groups or [])
        self.setWindowTitle("新建学习路线")
        self.setModal(True)
        self.resize(460, 460)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("新建学习路线（手动创建）")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：强化学习 / 数据结构与算法 / C++")
        form.addRow("路线名称 *", self.name_edit)

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("（顶级路线）", None)
        for g in self._groups:
            self.parent_combo.addItem(g.get("name") or f"分组{g.get('id')}", g.get("id"))
        form.addRow("父路线", self.parent_combo)

        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("例如：准备算法实习面试，掌握 RL 基础")
        form.addRow("目标", self.goal_edit)

        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setFixedHeight(70)
        form.addRow("描述", self.desc_edit)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, 5)
        self.priority_spin.setValue(3)
        form.addRow("优先级", self.priority_spin)

        self.planning_check = QCheckBox("启用自动规划")
        self.planning_check.setChecked(False)
        form.addRow("自动规划", self.planning_check)

        self.group_check = QCheckBox("高级：创建为分组（group，不含具体学习内容）")
        form.addRow("类型", self.group_check)
        root.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        root.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("SecondaryButton")
        apply_secondary_button_text(cancel)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("创建")
        ok.setObjectName("SecondaryButton")
        apply_secondary_button_text(ok)
        ok.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _on_confirm(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("路线名称不能为空。")
            self.error_label.setVisible(True)
            return
        if self.group_check.isChecked() and self.parent_combo.currentData() is not None:
            self.error_label.setText("分组只能创建在顶级（不能挂在其它分组下）。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "parent_id": self.parent_combo.currentData(),
            "goal": self.goal_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "priority": int(self.priority_spin.value()),
            "planning_enabled": self.planning_check.isChecked(),
            "is_group": self.group_check.isChecked(),
        }


class EditLearningRouteDialog(QDialog):
    """调整路线名称 / 目标 / 描述 / 优先级（不改结构）。"""

    def __init__(self, route, parent=None):
        super().__init__(parent)
        self.setWindowTitle("调整路线")
        self.setModal(True)
        self.resize(440, 380)
        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("调整路线")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        self.name_edit = QLineEdit(route.name)
        form.addRow("路线名称 *", self.name_edit)
        self.goal_edit = QLineEdit(route.goal or "")
        form.addRow("目标", self.goal_edit)
        self.desc_edit = QPlainTextEdit(route.description or "")
        self.desc_edit.setFixedHeight(70)
        form.addRow("描述", self.desc_edit)
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, 5)
        self.priority_spin.setValue(int(route.priority or 3))
        form.addRow("优先级", self.priority_spin)
        root.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        root.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("SecondaryButton")
        apply_secondary_button_text(cancel)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("SecondaryButton")
        apply_secondary_button_text(ok)
        ok.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _on_confirm(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("路线名称不能为空。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "goal": self.goal_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "priority": int(self.priority_spin.value()),
        }


class AddPhaseDialog(QDialog):
    def __init__(self, default_order: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加阶段")
        self.setModal(True)
        self.resize(420, 300)
        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("添加阶段")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：RL 基础")
        form.addRow("阶段名称 *", self.name_edit)
        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("例如：掌握 MDP / 值函数 / 策略")
        form.addRow("阶段目标", self.goal_edit)
        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 999)
        self.order_spin.setValue(int(default_order))
        form.addRow("顺序", self.order_spin)
        root.addLayout(form)

        hint = QLabel("手动路线按阶段顺序推进，不要求填写绝对日期。")
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        root.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("SecondaryButton")
        apply_secondary_button_text(cancel)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("添加")
        ok.setObjectName("SecondaryButton")
        apply_secondary_button_text(ok)
        ok.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _on_confirm(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("阶段名称不能为空。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "goal": self.goal_edit.text().strip(),
            "order_index": int(self.order_spin.value()),
        }


class AddTopicDialog(QDialog):
    def __init__(self, default_order: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加知识点")
        self.setModal(True)
        self.resize(460, 380)
        root = QVBoxLayout(self)
        root.setSpacing(8)
        title = QLabel("添加知识点")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：MDP / Policy / Value Function")
        form.addRow("标题 *", self.title_edit)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setFixedHeight(80)
        form.addRow("描述", self.desc_edit)
        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 600)
        self.minutes_spin.setSingleStep(5)
        self.minutes_spin.setValue(30)
        form.addRow("预计时间（分钟）", self.minutes_spin)
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, 5)
        self.priority_spin.setValue(3)
        form.addRow("优先级", self.priority_spin)
        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 999)
        self.order_spin.setValue(int(default_order))
        form.addRow("顺序", self.order_spin)
        root.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e74c3c;")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)
        root.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("SecondaryButton")
        apply_secondary_button_text(cancel)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("添加")
        ok.setObjectName("SecondaryButton")
        apply_secondary_button_text(ok)
        ok.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _on_confirm(self) -> None:
        if not self.title_edit.text().strip():
            self.error_label.setText("知识点标题不能为空。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def result_payload(self) -> dict:
        return {
            "name": self.title_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "estimated_minutes": int(self.minutes_spin.value()),
            "priority": int(self.priority_spin.value()),
            "order_index": int(self.order_spin.value()),
        }
