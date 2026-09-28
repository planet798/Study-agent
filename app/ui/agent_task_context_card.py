"""AgentTaskContextCard：Workspace 的 Task Context 卡片（纯 UI，无业务依赖）。

Composition::

    SACard
        FlowWidget
            [Route tag] [Activity tag] [Duration tag]
        AgentTaskDescription (PlainText, optional)

Task title 不属于这里：它是 Global SAPageHeader 的 subtitle，避免重复表达。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from .components.card import SACard
from .components.flow_layout import FlowWidget
from .components.tag import SATag
from .design import spacing


class AgentTaskContextCard(SACard):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(variant="default", parent=parent, padding=spacing.LG, spacing=spacing.SM)

        self.tags_widget = FlowWidget(self)
        self.route_tag = self.tags_widget.add_widget(SATag("", "neutral"))
        self.activity_tag = self.tags_widget.add_widget(SATag("", "info"))
        self.duration_tag = self.tags_widget.add_widget(SATag("", "neutral"))
        self.body_layout.addWidget(self.tags_widget)

        self.description_label = QLabel("")
        self.description_label.setObjectName("AgentTaskDescription")
        self.description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(False)
        self.body_layout.addWidget(self.description_label)

    # ---------- public ----------
    def set_metadata(
        self,
        route_text: str,
        activity_text: str,
        duration_text: str,
        *,
        route_is_fallback: bool = False,
    ) -> None:
        self.route_tag.setText(route_text or "未分类")
        self.route_tag.set_variant("neutral" if route_is_fallback else "accent")
        self.activity_tag.setText(activity_text or "学习活动")
        self.duration_tag.setText(duration_text or "未设置")

    def set_description(self, description: str | None) -> None:
        text = (description or "").strip()
        self.description_label.setText(text)
        self.description_label.setVisible(bool(text))

    def tag_texts(self) -> tuple[str, str, str]:
        return (
            self.route_tag.text(),
            self.activity_tag.text(),
            self.duration_tag.text(),
        )
