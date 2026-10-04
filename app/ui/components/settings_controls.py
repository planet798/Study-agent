"""设置页的自适应布局、菜单命令及语义反馈。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QSplitter

from .button import SAButton


class MenuCommandButton(SAButton):
    """保留按钮调用入口，实际操作在菜单内呈现。"""
    def __init__(self, text, parent=None):
        super().__init__(text, variant="subtle", parent=parent)
        self.command = QAction(text, self)
        self.command.triggered.connect(self.click)
        super().setVisible(False)

    def setEnabled(self, enabled):  # noqa: N802
        super().setEnabled(enabled)
        if hasattr(self, "command"):
            self.command.setEnabled(enabled)

    def setVisible(self, visible):  # noqa: N802
        if hasattr(self, "command"):
            self.command.setVisible(visible)
            super().setVisible(False)
        else:
            super().setVisible(visible)


class SettingsSplitter(QSplitter):
    """窄窗口纵向排布，宽窗口保留列表与详情的左右结构。"""
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setChildrenCollapsible(False)

    def resizeEvent(self, event):  # noqa: N802
        orientation = Qt.Orientation.Vertical if self.width() < 650 else Qt.Orientation.Horizontal
        if orientation != self.orientation():
            self.setOrientation(orientation)
        super().resizeEvent(event)


def feedback(label, text: str, state="info"):
    label.setObjectName("SettingsFeedback")
    label.setProperty("feedbackState", state)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)
