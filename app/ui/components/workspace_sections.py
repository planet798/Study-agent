"""工作区列表与详情的小型呈现组件；不查询数据或执行写操作。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget
from .button import SAButton
from ..design import spacing

def plain_label(text: str, role: str = "TaskMeta") -> QLabel:
    label = QLabel(text)
    label.setObjectName(role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ActionsMenuButton(SAButton):
    """带语义操作 key 的菜单；菜单不执行业务写入。"""

    requested = Signal(str)

    def __init__(self, actions: list[tuple[str, str]], parent=None):
        super().__init__("更多", variant="subtle", size="small", parent=parent)
        self.setAccessibleName("更多操作")
        menu = QMenu(self)
        menu.setObjectName("ActionsMenu")
        for key, text in actions:
            action = menu.addAction(text)
            action.setData(key)
            action.triggered.connect(
                lambda _checked=False, k=key: self.requested.emit(k)
            )
        self.setMenu(menu)


class CollapsibleSection(QFrame):
    expanded_changed = Signal(str, bool)

    def __init__(self, key: str, title: str, summary: str = "", *, expanded=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("CollapsibleSection")
        root = QVBoxLayout(self)
        root.setContentsMargins(spacing.XS, spacing.MD, spacing.XS, spacing.MD)
        root.setSpacing(spacing.SM)
        self.header_layout = QHBoxLayout()
        self.header_layout.addWidget(plain_label(title, "SectionTitle"), 1)
        if summary:
            self.header_layout.addWidget(plain_label(summary))
        self.toggle = SAButton("", variant="subtle", size="small")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(f"展开或收起：{title}")
        self.header_layout.addWidget(self.toggle)
        root.addLayout(self.header_layout)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, spacing.XS, 0, 0)
        self.body_layout.setSpacing(spacing.SM)
        root.addWidget(self.body)
        self.toggle.setChecked(expanded)
        self._apply_expanded(expanded)
        self.toggle.toggled.connect(self._on_toggle)

    def _apply_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setText("收起" if expanded else "展开")

    def _on_toggle(self, expanded):
        self._apply_expanded(expanded)
        self.expanded_changed.emit(self.key, expanded)


