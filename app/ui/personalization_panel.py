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
from .settings_drafts import SettingsDrafts
from .settings_views import build_personalization_view
from .components.settings_controls import feedback


class PersonalizationPanel(QWidget):
    def __init__(self, personalization_service: PersonalizationService | None = None, parent=None):
        super().__init__(parent)
        self.service = personalization_service
        self._drafts = SettingsDrafts()
        self._available = False
        build_personalization_view(self)
        self.refresh()

    def _update_counter(self):
        text = self.editor.toPlainText()
        self.counter.setText(f"{len(text)} / 8000")
        self._drafts.edit("instructions", text)
        self._update_draft_status()

    def _set_available(self, available):
        self._available = available
        self.discard_btn.setEnabled(available and self._drafts.dirty("instructions"))
        self.editor.setEnabled(available)
        self.save_btn.setEnabled(available)
        self.memory_checkbox.setEnabled(available)
        self.auto_memory_checkbox.setEnabled(available and self.memory_checkbox.isChecked())
        self.manage_btn.setEnabled(available)

    def refresh(self):
        if self.service is None:
            self._set_available(False)
            feedback(self.feedback, "个性化设置暂不可用。", "error")
            return
        try:
            settings = self.service.get_settings()
        except Exception:
            self._set_available(False)
            feedback(self.feedback, "暂时无法加载个性化设置，请稍后重试。", "error")
            return
        text = self._drafts.load("instructions", settings["instructions"])
        self._load_editor(text)
        with QSignalBlocker(self.memory_checkbox), QSignalBlocker(self.auto_memory_checkbox):
            self.memory_checkbox.setChecked(bool(settings["memory_enabled"]))
            self.auto_memory_checkbox.setChecked(bool(settings["auto_memory_enabled"]))
        self._set_available(True)
        feedback(self.feedback, "")

    def _save(self):
        try:
            settings = self.service.set_instructions(self.editor.toPlainText())
        except Exception:
            feedback(self.feedback, "无法保存 Agent 说明，请检查内容长度或特殊字符。", "error")
            return
        self._drafts.accept("instructions", settings["instructions"])
        self._load_editor(settings["instructions"])
        feedback(self.feedback, "Agent 说明已保存。", "success")

    def _load_editor(self, text):
        if self.editor.toPlainText() != text:
            with QSignalBlocker(self.editor):
                self.editor.setPlainText(text)
        self.counter.setText(f"{len(text)} / 8000")
        self._update_draft_status()

    def _update_draft_status(self):
        dirty = self._drafts.dirty("instructions")
        self.draft_label.setText("未保存" if dirty else "已保存")
        self.discard_btn.setEnabled(self._available and dirty)

    def _discard(self):
        try:
            saved = self.service.get_settings()["instructions"]
        except Exception:
            feedback(self.feedback, "暂时无法重新加载说明，草稿已保留。", "error")
            return
        self._drafts.accept("instructions", saved)
        self._load_editor(saved)
        feedback(self.feedback, "已放弃未保存修改。")

    def _set_memory_enabled(self, enabled):
        try:
            self.service.set_memory_enabled(enabled)
        except Exception:
            with QSignalBlocker(self.memory_checkbox):
                self.memory_checkbox.setChecked(not enabled)
            feedback(self.feedback, "无法保存记忆设置，请稍后重试。", "error")
            return
        # Keep the stored auto-memory consent checked even while non-interactive.
        self.auto_memory_checkbox.setEnabled(enabled)
        feedback(self.feedback, "记忆设置已保存。", "success")

    def _set_auto_memory_enabled(self, enabled):
        try:
            self.service.set_auto_memory_enabled(enabled)
        except Exception:
            with QSignalBlocker(self.auto_memory_checkbox):
                self.auto_memory_checkbox.setChecked(not enabled)
            feedback(self.feedback, "无法保存记忆设置，请稍后重试。", "error")
            return
        feedback(self.feedback, "记忆设置已保存。", "success")

    def _manage(self):
        dialog = PersonalMemoriesDialog(self.service, self)
        dialog.exec()
        dialog.deleteLater()
