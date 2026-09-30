"""User-authored personalization preferences; persistence only, not runtime input."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.personalization_service import PersonalizationService
from .components.button import SAButton
from .components.card import SACard
from .components.section_header import SASectionHeader
from .personal_memory_dialog import PersonalMemoriesDialog


class PersonalizationPanel(QWidget):
    def __init__(self, personalization_service: PersonalizationService | None = None, parent=None):
        super().__init__(parent)
        self.service = personalization_service
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        root.addWidget(scroll)
        instructions = SACard()
        instructions.add_widget(SASectionHeader("Agent 说明"))
        instructions.add_widget(QLabel("告诉 Study Agent 你希望它如何帮助你学习。"))
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Agent 说明")
        self.editor.setPlaceholderText(
            "例如：\n我目前基础比较薄弱，讲解时先讲直觉，再解释原理。\n"
            "数据结构示例默认使用 C++。\n学习过程尽量分步骤推进……"
        )
        self.editor.setMinimumHeight(180)
        instructions.add_widget(self.editor)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.counter = QLabel("0 / 8000")
        self.counter.setObjectName("TaskMeta")
        row.addWidget(self.counter)
        row.addStretch()
        self.save_btn = SAButton("保存", variant="primary")
        row.addWidget(self.save_btn)
        instructions.add_widget(actions)
        layout.addWidget(instructions, stretch=1)

        memory = SACard()
        memory.add_widget(SASectionHeader("记忆"))
        description = QLabel("本地记忆用于在不同学习会话之间保留你的长期偏好和背景信息。")
        description.setWordWrap(True)
        memory.add_widget(description)
        self.memory_checkbox = QCheckBox("启用本地记忆")
        self.auto_memory_checkbox = QCheckBox("允许根据学习会话生成记忆")
        memory.add_widget(self.memory_checkbox)
        memory.add_widget(self.auto_memory_checkbox)
        note = QLabel(
            "Agent 会在新的学习对话中使用已保存的说明和已启用记忆；"
            "根据学习会话自动生成记忆将在后续提供。"
        )
        note.setObjectName("TaskMeta")
        note.setWordWrap(True)
        memory.add_widget(note)
        self.manage_btn = SAButton("管理记忆", variant="secondary")
        memory.add_widget(self.manage_btn)
        layout.addWidget(memory)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.editor.textChanged.connect(self._update_counter)
        self.save_btn.clicked.connect(self._save)
        self.memory_checkbox.toggled.connect(self._set_memory_enabled)
        self.auto_memory_checkbox.toggled.connect(self._set_auto_memory_enabled)
        self.manage_btn.clicked.connect(self._manage)
        self.refresh()

    def _update_counter(self):
        self.counter.setText(f"{len(self.editor.toPlainText())} / 8000")

    def _set_available(self, available):
        self.editor.setEnabled(available)
        self.save_btn.setEnabled(available)
        self.memory_checkbox.setEnabled(available)
        self.auto_memory_checkbox.setEnabled(available and self.memory_checkbox.isChecked())
        self.manage_btn.setEnabled(available)

    def refresh(self):
        if self.service is None:
            self._set_available(False)
            self.feedback.setText("个性化设置暂不可用。")
            return
        try:
            settings = self.service.get_settings()
        except Exception:
            self._set_available(False)
            self.feedback.setText("暂时无法加载个性化设置，请稍后重试。")
            return
        self.editor.setPlainText(settings["instructions"])
        with QSignalBlocker(self.memory_checkbox), QSignalBlocker(self.auto_memory_checkbox):
            self.memory_checkbox.setChecked(bool(settings["memory_enabled"]))
            self.auto_memory_checkbox.setChecked(bool(settings["auto_memory_enabled"]))
        self._set_available(True)
        self.feedback.clear()

    def _save(self):
        try:
            settings = self.service.set_instructions(self.editor.toPlainText())
        except Exception:
            self.feedback.setText("无法保存 Agent 说明，请检查内容长度或特殊字符。")
            return
        self.editor.setPlainText(settings["instructions"])
        self.feedback.setText("Agent 说明已保存。")

    def _set_memory_enabled(self, enabled):
        try:
            self.service.set_memory_enabled(enabled)
        except Exception:
            with QSignalBlocker(self.memory_checkbox):
                self.memory_checkbox.setChecked(not enabled)
            self.feedback.setText("无法保存记忆设置，请稍后重试。")
            return
        # Keep the stored auto-memory consent checked even while non-interactive.
        self.auto_memory_checkbox.setEnabled(enabled)
        self.feedback.setText("记忆设置已保存。")

    def _set_auto_memory_enabled(self, enabled):
        try:
            self.service.set_auto_memory_enabled(enabled)
        except Exception:
            with QSignalBlocker(self.auto_memory_checkbox):
                self.auto_memory_checkbox.setChecked(not enabled)
            self.feedback.setText("无法保存记忆设置，请稍后重试。")
            return
        self.feedback.setText("记忆设置已保存。")

    def _manage(self):
        dialog = PersonalMemoriesDialog(self.service, self)
        dialog.exec()
        dialog.deleteLater()
