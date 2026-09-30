"""Manual local memory management through PersonalizationService only."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..services.personalization_service import PersonalizationService
from .components.button import SAButton
from .components.card import SACard


class PersonalMemoryEditorDialog(QDialog):
    def __init__(self, service: PersonalizationService, memory: dict | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.memory = memory
        self.setWindowTitle("编辑记忆" if memory else "添加记忆")
        self.setModal(True)
        self.resize(480, 320)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("记忆内容")
        self.editor.setPlaceholderText("记录你的长期学习偏好或背景信息。")
        self.editor.setPlainText(memory["content"] if memory else "")
        layout.addWidget(self.editor)
        self.counter = QLabel()
        self.counter.setObjectName("TaskMeta")
        layout.addWidget(self.counter)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_btn = SAButton("取消", variant="secondary")
        self.save_btn = SAButton("保存", variant="primary")
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.save_btn)
        layout.addLayout(actions)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._save)
        self.editor.textChanged.connect(self._update_counter)
        self._update_counter()

    def _update_counter(self):
        self.counter.setText(f"{len(self.editor.toPlainText())} / 1000")

    def _save(self):
        try:
            if self.memory is None:
                self.service.add_manual_memory(self.editor.toPlainText())
            else:
                self.service.edit_memory(self.memory["id"], self.editor.toPlainText())
        except Exception:
            self.feedback.setText("无法保存记忆，请检查内容长度或特殊字符后重试。")
            return
        self.accept()


class PersonalMemoriesDialog(QDialog):
    def __init__(self, service: PersonalizationService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("本地记忆")
        self.setModal(True)
        self.resize(600, 480)
        layout = QVBoxLayout(self)
        self.add_btn = SAButton("+ 添加记忆", variant="primary")
        self.add_btn.clicked.connect(self._add)
        layout.addWidget(self.add_btn)
        self.empty_label = QLabel("还没有本地记忆。")
        layout.addWidget(self.empty_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.rows_layout = QVBoxLayout(container)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        close = SAButton("关闭", variant="secondary")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.rows = []
        self.refresh()

    def refresh(self):
        try:
            memories = self.service.list_memories(include_disabled=True)
        except Exception:
            self.feedback.setText("暂时无法加载本地记忆，请稍后重试。")
            return
        while self.rows_layout.count():
            widget = self.rows_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.rows = []
        self.empty_label.setVisible(not memories)
        self.feedback.clear()
        for memory in memories:
            card = SACard()
            content = QLabel(memory["content"])
            content.setTextFormat(Qt.TextFormat.PlainText)
            content.setWordWrap(True)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if not memory["enabled"]:
                content.setObjectName("TaskMeta")
            card.add_widget(content)
            source = "手动添加" if memory["source_type"] == "manual" else "学习会话"
            meta = QLabel(f"{source} · {'已启用' if memory['enabled'] else '已停用'}")
            meta.setObjectName("TaskMeta")
            card.add_widget(meta)
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(0, 0, 0, 0)
            edit = SAButton("编辑", variant="secondary")
            toggle = SAButton("停用" if memory["enabled"] else "启用", variant="secondary")
            delete = SAButton("删除", variant="secondary")
            edit.clicked.connect(lambda checked=False, m=memory: self._edit(m))
            toggle.clicked.connect(lambda checked=False, m=memory: self._toggle(m))
            delete.clicked.connect(lambda checked=False, m=memory: self._delete(m))
            for button in (edit, toggle, delete):
                buttons.addWidget(button)
            buttons.addStretch()
            card.add_widget(actions)
            self.rows_layout.addWidget(card)
            self.rows.append(card)

    def _add(self):
        dialog = PersonalMemoryEditorDialog(self.service, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
        dialog.deleteLater()

    def _edit(self, memory):
        dialog = PersonalMemoryEditorDialog(self.service, memory, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
        dialog.deleteLater()

    def _toggle(self, memory):
        try:
            if memory["enabled"]:
                self.service.disable_memory(memory["id"])
            else:
                self.service.enable_memory(memory["id"])
        except Exception:
            self.feedback.setText("无法更新记忆，请稍后重试。")
            return
        self.refresh()

    def _confirm_delete(self):
        box = QMessageBox(self)
        box.setWindowTitle("删除记忆")
        box.setText("删除后，这条本地记忆将永久移除。聊天记录不会受到影响。")
        delete = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        confirmed = box.clickedButton() == delete
        box.deleteLater()
        return confirmed

    def _delete(self, memory):
        if not self._confirm_delete():
            return
        try:
            self.service.delete_memory(memory["id"])
        except Exception:
            self.feedback.setText("无法删除记忆，请稍后重试。")
            return
        self.refresh()
