"""手动添加今日学习任务对话框（Phase A / C）。

两种任务：
- 普通学习任务（manual todo）：不关联知识点，不进入验收链路；可选所属路线；
- 正式知识学习任务（manual knowledge）：
    A. 关联已有 study_topic（Topic 下拉严格按所选路线过滤）；
    B. 新建临时知识点（knowledge_point 按 name+route 幂等）。

Phase C 增加“所属路线”选择；没有多路线 Scheduler，路线只用于组织 / 筛选 / 统计。
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

UNCLASSIFIED_LABEL = "未分类"


class AddLearningTaskDialog(QDialog):
    def __init__(
        self,
        topics=None,
        default_date: str | None = None,
        parent=None,
        routes=None,
        topics_by_route=None,
    ):
        """topics: 兼容旧调用（未分类 topic 列表，Phase A）。

        routes: [{"id": int, "name": str}, ...] active learning routes；
        topics_by_route: {route_id: [{"id","name"}, ...]} 按路线分组的 topic。
        """
        super().__init__(parent)
        self._topics = list(topics or [])
        self._routes = list(routes or [])
        self._topics_by_route = {
            int(k): list(v) for k, v in (topics_by_route or {}).items()
        }
        self.setWindowTitle("添加学习任务")
        self.setModal(True)
        self.resize(520, 520)

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

        # 所属路线
        route_row = QHBoxLayout()
        route_row.addWidget(QLabel("所属路线"))
        self.route_combo = QComboBox()
        self.route_combo.addItem(UNCLASSIFIED_LABEL, None)
        for r in self._routes:
            self.route_combo.addItem(r.get("name") or f"路线{r.get('id')}", r.get("id"))
        self.route_combo.currentIndexChanged.connect(self._on_route_changed)
        route_row.addWidget(self.route_combo, stretch=1)
        root.addLayout(route_row)

        # 标题
        root.addWidget(QLabel("标题 *"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：刷 LeetCode 3 道 / 强化学习基础")
        root.addWidget(self.title_edit)

        # 描述
        root.addWidget(QLabel("描述"))
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("可选：本次学习的范围 / 目标")
        self.desc_edit.setFixedHeight(70)
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
        self.temp_radio = QRadioButton("新建临时知识点（不绑定现有阶段）")
        self.temp_radio.setChecked(True)
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

        self._reload_topics()

    # ---------- 状态 ----------

    def _selected_route_id(self):
        return self.route_combo.currentData()

    def _on_type_changed(self) -> None:
        self.knowledge_box.setVisible(self.knowledge_radio.isChecked())

    def _on_route_changed(self) -> None:
        self._reload_topics()

    def _reload_topics(self) -> None:
        """Topic 下拉严格按所选路线过滤（禁止跨路线关联）。"""
        route_id = self._selected_route_id()
        self.topic_combo.clear()
        if route_id is None:
            # 未分类时没有可关联的正式 topic；兼容旧调用传入的 topics
            topics = self._topics
        else:
            topics = self._topics_by_route.get(int(route_id), [])
        for t in topics:
            self.topic_combo.addItem(t.get("name") or f"Topic {t.get('id')}",
                                     t.get("id"))
        has = self.topic_combo.count() > 0
        self.topic_radio.setEnabled(has)
        self.topic_combo.setEnabled(has)
        if not has:
            self.topic_radio.setChecked(False)
            self.temp_radio.setChecked(True)
        elif not self.temp_radio.isChecked():
            self.topic_radio.setChecked(True)

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
            "route_id": self._selected_route_id(),
        }
        if is_knowledge and self.topic_radio.isChecked():
            payload["topic_id"] = self.topic_combo.currentData()
        return payload
