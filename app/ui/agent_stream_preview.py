"""流式文本仅存于内存；完成后由正式文档面板替换。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class AgentStreamPreview(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("AgentStreamingPreview")
        layout = QVBoxLayout(self)
        heading = QLabel("学习助手正在回复…")
        layout.addWidget(heading)
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.body)

    def set_text(self, text):
        self.body.setText(text)
