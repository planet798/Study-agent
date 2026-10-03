"""Compact task metadata and optional expandable description."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .components.flow_layout import FlowWidget
from .components.tag import SATag
from .design import spacing


class AgentTaskContextCard(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentTaskContextControl")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing.XS)
        self.tags_widget = FlowWidget(self)
        self.route_tag = self.tags_widget.add_widget(SATag("", "neutral"))
        self.activity_tag = self.tags_widget.add_widget(SATag("", "neutral"))
        self.duration_tag = self.tags_widget.add_widget(SATag("", "neutral"))
        layout.addWidget(self.tags_widget)
        self.description_button = QPushButton("任务信息 ▾")
        self.description_button.setObjectName("AgentTaskInfoToggle")
        self.description_button.setCheckable(True)
        self.description_button.toggled.connect(self._toggle_description)
        layout.addWidget(self.description_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.description_label = QLabel("")
        self.description_label.setObjectName("AgentTaskDescription")
        self.description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.description_label.setWordWrap(True)
        self.description_label.hide()
        layout.addWidget(self.description_label)
        self.description_button.hide()

    def _toggle_description(self, checked: bool) -> None:
        self.description_label.setVisible(checked and bool(self.description_label.text()))
        self.description_button.setText("任务信息 ▴" if checked else "任务信息 ▾")

    def set_metadata(self, route_text: str, activity_text: str, duration_text: str,
                     *, route_is_fallback: bool = False) -> None:
        self.route_tag.setText(route_text or "未分类")
        self.route_tag.set_variant("neutral")
        self.activity_tag.setText(activity_text or "学习活动")
        self.duration_tag.setText(duration_text or "未设置")

    def set_description(self, description: str | None) -> None:
        text = (description or "").strip()
        self.description_label.setText(text)
        self.description_button.setVisible(bool(text))
        self.description_label.setVisible(bool(text) and self.description_button.isChecked())

    def tag_texts(self) -> tuple[str, str, str]:
        return (self.route_tag.text(), self.activity_tag.text(), self.duration_tag.text())
