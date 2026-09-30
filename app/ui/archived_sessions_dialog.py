"""Small visual archived-session list; MainWindow owns all service calls."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


class ArchivedSessionsDialog(QDialog):
    restore_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("已归档会话")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.body = QWidget(scroll)
        self.rows_layout = QVBoxLayout(self.body)
        scroll.setWidget(self.body)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.restore_buttons = {}
        self._busy = False

    def set_sessions(self, sessions):
        """Accept service-resolved visual titles, never raw domain/service objects."""
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.restore_buttons.clear()
        for session in sessions:
            row = QWidget(self.body)
            layout = QHBoxLayout(row)
            title = QLabel(session["visible_title"], row)
            title.setTextFormat(Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            layout.addWidget(title, 1)
            layout.addWidget(QLabel(session["archived_at"], row))
            restore = QPushButton("恢复", row)
            restore.setEnabled(not self._busy)
            restore.clicked.connect(lambda _checked=False, sid=session["id"]:
                                    self.restore_requested.emit(sid))
            layout.addWidget(restore)
            self.restore_buttons[session["id"]] = restore
            self.rows_layout.addWidget(row)
        if not sessions:
            self.rows_layout.addWidget(QLabel("暂无已归档会话", self.body))
        self.rows_layout.addStretch()

    def set_busy(self, busy):
        self._busy = bool(busy)
        for button in self.restore_buttons.values():
            button.setEnabled(not self._busy)
