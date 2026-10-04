"""工作区列表与详情的小型呈现组件；不查询数据或执行写操作。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu
from .button import SAButton

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


