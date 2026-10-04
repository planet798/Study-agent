"""高频表单的滚动、固定操作区、校验和输入兼容。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QLabel

from app.ui.ai_settings_dialogs import (
    AddAIProfileDialog, EditAPIKeyDialog, RenameProfileDialog,
    FinalPromptPreviewDialog, PromptDefaultDialog,
)
from app.ui.components.button import SAIconButton
from app.ui.dialogs import NotDoneDialog
from app.ui.manual_task_dialog import AddLearningTaskDialog
from app.ui.assessment_dialog import AssessmentDialog


@pytest.mark.parametrize('factory', [AddAIProfileDialog, lambda: EditAPIKeyDialog('Profile'),
                                    lambda: RenameProfileDialog('Profile'),
                                    lambda: NotDoneDialog('长任务标题' * 40), AddLearningTaskDialog])
def test_form_footer_stays_outside_scrolling_body(qtbot, factory):
    dialog = factory()
    qtbot.addWidget(dialog)
    dialog.resize(440, 320)
    dialog.show()
    scroll = dialog.form_scroll
    assert scroll.isVisible()
    footer = dialog.layout().itemAt(dialog.layout().count() - 1)
    if footer.widget():
        assert not scroll.isAncestorOf(footer.widget())
    if hasattr(dialog, 'buttons'):
        confirm = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert confirm.isVisible() and cancel.isVisible()
        assert cancel.mapTo(dialog, cancel.rect().center()).x() < confirm.mapTo(dialog, confirm.rect().center()).x()
    assert dialog.width() == 440


def test_required_profile_fields_use_inline_feedback_and_keep_input(qtbot):
    dialog = AddAIProfileDialog()
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('保留配置名称')
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert 'Base URL' in dialog.error_label.text()
    assert dialog.error_label.property('feedbackState') == 'error'
    assert dialog.name_edit.text() == '保留配置名称'
    assert not dialog.form_scroll.isAncestorOf(dialog.error_label)
    dialog.base_url_edit.setText('https://example.test')
    dialog.model_edit.setText('example')
    dialog.api_key_edit.setText('private-test-key')
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values()['api_key'] == 'private-test-key'


def test_password_toggle_keeps_default_mask_and_updates_accessibility(qtbot):
    dialog = EditAPIKeyDialog('Profile')
    qtbot.addWidget(dialog)
    toggle = dialog.findChild(SAIconButton)
    assert dialog.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    toggle.click()
    assert dialog.api_key_edit.echoMode() == QLineEdit.EchoMode.Normal
    assert '隐藏' in toggle.accessibleName()
    toggle.click()
    assert dialog.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert '显示' in toggle.accessibleName()


def test_not_done_reason_validation_and_trim_are_unchanged(qtbot):
    dialog = NotDoneDialog('Task')
    qtbot.addWidget(dialog)
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dialog.error_label.text()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.reason_edit.setPlainText('  保留完整原因\n第二行  ')
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.reason() == '保留完整原因\n第二行'


@pytest.mark.parametrize('factory', [lambda: PromptDefaultDialog('Example', '完整原文\n' * 100),
                                    lambda: FinalPromptPreviewDialog('Example', system_text='系统\n' * 100,
                                                                     user_text='用户\n' * 100, context_text='上下文\n' * 100)])
def test_readonly_prompt_dialogs_keep_all_content_and_close_reachable(qtbot, factory):
    from PySide6.QtWidgets import QPlainTextEdit
    dialog = factory()
    qtbot.addWidget(dialog)
    dialog.resize(480, 420)
    dialog.show()
    editors = dialog.findChildren(QPlainTextEdit)
    assert all(e.isReadOnly() and len(e.toPlainText().splitlines()) == 100 for e in editors)
    box = dialog.findChild(QDialogButtonBox)
    assert box.button(QDialogButtonBox.StandardButton.Close).isVisible()
    assert not dialog.form_scroll.isAncestorOf(box)


def test_assessment_questions_results_share_viewport_and_footer_remains_visible(qtbot, qapp):
    from PySide6.QtWidgets import QPushButton
    attempt = {'id': 1, 'questions': [{'type': 'concept', 'question': '长问题' * 40} for _ in range(12)]}
    dialog = AssessmentDialog(None, attempt, '2026-10-04')
    qtbot.addWidget(dialog)
    dialog.resize(480, 420)
    dialog.show()
    assert dialog.questions_area.isAncestorOf(dialog.questions_container)
    assert dialog.questions_area.isAncestorOf(dialog.result_container)
    assert not dialog.questions_area.isAncestorOf(dialog.submit_btn)
    assert dialog.submit_btn.isVisible()
    dialog._show_result({'mastery_estimate': 0.8, 'result_level': 'good'})
    qapp.processEvents()
    assert dialog.result_container.isVisible()
    assert dialog.questions_area.verticalScrollBar().value() > 0
    assert len(dialog._answer_edits) == 12


def test_enter_in_rename_field_uses_confirm_not_cancel(qtbot):
    dialog = RenameProfileDialog('Profile')
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText('Updated profile')
    dialog.name_edit.setFocus()
    qtbot.keyClick(dialog.name_edit, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.display_name() == 'Updated profile'
