"""P-1C: user preferences UI, manual memories and history/prompt separation."""

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from app.database.agent_memory_repository import AgentMemoryRepository
from app.database.agent_repository import AgentRepository
from app.database.personalization_repository import PersonalizationRepository
from app.database.repository import TaskRepository
from app.services.personalization_service import PersonalizationService
from app.ui.ai_settings_page import AISettingsPage, PromptManagerPanel
from app.ui.personalization_panel import PersonalizationPanel
from app.ui.personal_memory_dialog import PersonalMemoriesDialog, PersonalMemoryEditorDialog

pytestmark = pytest.mark.ui


@pytest.fixture
def service(conn):
    return PersonalizationService(PersonalizationRepository(conn))


def panel(qtbot, service):
    widget = PersonalizationPanel(service)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def click_action(card, text):
    next(b for b in card.findChildren(QPushButton) if b.text() == text).click()


def modal_action(qtbot, launch, action):
    # Exercise the actual modal dialogs instead of bypassing their save handlers.
    errors = []

    def run():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None
            action(dialog)
        except BaseException as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, run)
    launch()
    if errors:
        raise errors[0]


def test_structure_default_advanced_and_appearance(qtbot, ai_config_service, prompt_registry, service):
    page = AISettingsPage(ai_config_service, prompt_registry, personalization_service=service)
    qtbot.addWidget(page)
    assert [page.tabs.tabText(i) for i in range(3)] == ['个性化', '模型 / API', '高级']
    assert page.tabs.currentWidget() is page.personalization_panel
    assert page.tabs.widget(2).isAncestorOf(page.prompt_panel)
    assert isinstance(page.prompt_panel, PromptManagerPanel)
    assert page.theme_combo.isEnabled()
    normal_text = ' '.join(label.text() for label in page.personalization_panel.findChildren(QLabel))
    assert all(word not in normal_text for word in ['system prompt', 'user prompt', 'PromptRegistry', '{{variables}}'])
    assert (
        'Agent 会在新的学习对话中使用已保存的说明和已启用记忆；'
        '根据学习会话自动生成记忆将在后续提供。'
    ) in normal_text
    assert '尚未接入 Agent' not in normal_text
    from app.ui.app_shell import PAGE_SPECS_BY_KEY
    assert PAGE_SPECS_BY_KEY['settings'].subtitle == '个性化、模型与外观设置'


def test_unavailable_keeps_settings_usable(qtbot):
    page = AISettingsPage(None, None)
    qtbot.addWidget(page)
    p = page.personalization_panel
    page.refresh()
    assert '不可用' in p.feedback.text()
    for control in [p.save_btn, p.memory_checkbox, p.auto_memory_checkbox, p.manage_btn]:
        assert not control.isEnabled()
    assert page.theme_combo.isEnabled()
    assert page.tabs.isEnabled()


def test_instructions_save_load_clear_counter(qtbot, service, conn, monkeypatch):
    p = panel(qtbot, service)
    assert p.editor.toPlainText() == ''
    assert p.editor.placeholderText().startswith('例如：')
    calls = []
    original = service.set_instructions

    def save(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(service, 'set_instructions', save)
    text = '  先讲直觉\n示例使用 C++\n{{literal}}  '
    p.editor.setPlainText(text)
    assert p.counter.text() == f'{len(text)} / 8000'
    assert service.get_settings()['instructions'] == ''  # explicit save only
    p.save_btn.click()
    assert calls == [text]
    assert service.get_settings()['instructions'] == text.strip()
    assert '已保存' in p.feedback.text()
    assert conn.execute('SELECT COUNT(*) FROM prompt_overrides').fetchone()[0] == 0
    fresh = panel(qtbot, PersonalizationService(PersonalizationRepository(conn)))
    assert fresh.editor.toPlainText() == text.strip()
    fresh.editor.setPlainText('unsaved')
    QApplication.processEvents()
    assert fresh.editor.toPlainText() == 'unsaved'
    fresh.refresh()
    assert fresh.editor.toPlainText() == text.strip()
    p.editor.clear()
    p.save_btn.click()
    assert service.get_settings()['instructions'] == ''


@pytest.mark.parametrize('text', ['x' * 8001, 'unsafe\x01'])
def test_invalid_instructions_controlled(qtbot, service, text):
    p = panel(qtbot, service)
    p.editor.setPlainText(text)
    p.save_btn.click()
    assert p.feedback.text() == '无法保存 Agent 说明，请检查内容长度或特殊字符。'
    assert service.get_settings()['instructions'] == ''


def test_memory_master_preserves_consent_and_items(qtbot, service, conn):
    memory = service.add_manual_memory('偏好')
    p = panel(qtbot, service)
    assert not p.memory_checkbox.isChecked()
    assert not p.auto_memory_checkbox.isChecked()
    assert not p.auto_memory_checkbox.isEnabled()
    assert p.manage_btn.isEnabled()
    p.memory_checkbox.click()
    p.auto_memory_checkbox.click()
    assert service.get_settings()['memory_enabled'] == 1
    assert service.get_settings()['auto_memory_enabled'] == 1
    p.memory_checkbox.click()
    assert not p.auto_memory_checkbox.isEnabled()
    assert p.auto_memory_checkbox.isChecked()
    assert service.get_settings()['auto_memory_enabled'] == 1
    assert service.list_memories() == [memory]
    fresh = panel(qtbot, PersonalizationService(PersonalizationRepository(conn)))
    assert not fresh.memory_checkbox.isChecked()
    assert fresh.auto_memory_checkbox.isChecked()
    assert not fresh.auto_memory_checkbox.isEnabled()
    fresh.memory_checkbox.click()
    assert fresh.auto_memory_checkbox.isEnabled() and fresh.auto_memory_checkbox.isChecked()
    fresh.auto_memory_checkbox.click()
    assert service.get_settings()['auto_memory_enabled'] == 0


def test_manager_empty_add_edit_toggle_delete(qtbot, service):
    dlg = PersonalMemoriesDialog(service)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.empty_label.isVisible()
    assert dlg.empty_label.text() == '还没有本地记忆。'

    def add(editor):
        assert isinstance(editor, PersonalMemoryEditorDialog)
        editor.editor.setPlainText('  手动\n长期偏好  ')
        assert editor.counter.text() == f'{len(editor.editor.toPlainText())} / 1000'
        editor.save_btn.click()

    modal_action(qtbot, dlg.add_btn.click, add)
    memory = service.list_memories()[0]
    assert memory['content'] == '手动\n长期偏好'
    assert memory['source_type'] == 'manual'
    assert not dlg.empty_label.isVisible()

    def cancel_edit(editor):
        assert editor.editor.toPlainText() == memory['content']
        editor.editor.setPlainText('Do not save')
        editor.cancel_btn.click()

    modal_action(qtbot, lambda: click_action(dlg.rows[0], '编辑'), cancel_edit)
    assert service.list_memories()[0] == memory

    def edit(editor):
        editor.editor.setPlainText('编辑后的偏好')
        editor.save_btn.click()

    modal_action(qtbot, lambda: click_action(dlg.rows[0], '编辑'), edit)
    edited = service.list_memories()[0]
    assert edited['content'] == '编辑后的偏好'
    assert edited['created_at'] == memory['created_at']
    click_action(dlg.rows[0], '停用')
    assert service.list_memories()[0]['enabled'] == 0
    assert len(dlg.rows) == 1
    assert any('已停用' in label.text() for label in dlg.rows[0].findChildren(QLabel))
    click_action(dlg.rows[0], '启用')
    assert service.list_memories()[0]['enabled'] == 1

    def cancel_delete(box):
        assert isinstance(box, QMessageBox)
        assert box.defaultButton().text() == '取消'
        assert box.escapeButton().text() == '取消'
        assert '聊天记录不会受到影响' in box.text()
        box.defaultButton().click()

    modal_action(qtbot, lambda: click_action(dlg.rows[0], '删除'), cancel_delete)
    assert len(service.list_memories()) == 1
    modal_action(qtbot, lambda: click_action(dlg.rows[0], '删除'),
                 lambda box: next(b for b in box.buttons() if b.text() == '删除').click())
    assert service.list_memories() == []
    assert dlg.empty_label.isVisible()


@pytest.mark.parametrize('text', ['', 'x' * 1001, '\x01'])
def test_memory_editor_invalid_feedback(qtbot, service, text):
    editor = PersonalMemoryEditorDialog(service)
    qtbot.addWidget(editor)
    editor.editor.setPlainText(text)
    editor.save_btn.click()
    assert editor.feedback.text() == '无法保存记忆，请检查内容长度或特殊字符后重试。'
    assert service.list_memories() == []


def test_ui_preserves_session_history_compaction_and_prompts(qtbot, service, conn, prompt_registry):
    task = TaskRepository(conn).create(title='Task', scheduled_date='2026-01-01')
    agent = AgentRepository(conn)
    session = agent.create_session(task.id, 'Session')
    message = agent.add_message(session['id'], 'user', 'Raw chat')
    AgentMemoryRepository(conn).upsert(session['id'], message['id'], 1, 'Compaction')
    original_prompt = prompt_registry.default_template('planner.system') + '\nOriginal override'
    prompt_registry.set_override('planner.system', original_prompt)
    memory = service.add_session_memory('Session preference', session['id'], message['id'])
    tables = ['agent_sessions', 'agent_messages', 'agent_session_memory', 'prompt_overrides', 'tasks']
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
    p = panel(qtbot, service)
    p.editor.setPlainText('{{variables}} should remain plain user text')
    p.save_btn.click()
    p.memory_checkbox.click()
    p.auto_memory_checkbox.click()
    p.memory_checkbox.click()

    def manage(dlg):
        assert isinstance(dlg, PersonalMemoriesDialog)
        labels = [label for label in dlg.rows[0].findChildren(QLabel)]
        assert any('学习会话' in label.text() for label in labels)
        assert all('source_session_id' not in label.text() and 'source_message_id' not in label.text()
                   for label in labels)
        assert [label.text() for label in labels] == ['Session preference', '学习会话 · 已启用']

        def edit(editor):
            editor.editor.setPlainText('Edited session preference')
            editor.save_btn.click()

        modal_action(qtbot, lambda: click_action(dlg.rows[0], '编辑'), edit)
        updated = service.list_memories()[0]
        for key in ['source_type', 'source_session_id', 'source_message_id', 'created_at']:
            assert updated[key] == memory[key]
        click_action(dlg.rows[0], '停用')
        click_action(dlg.rows[0], '启用')
        modal_action(qtbot, lambda: click_action(dlg.rows[0], '删除'),
                     lambda box: next(b for b in box.buttons() if b.text() == '删除').click())
        dlg.accept()

    assert p.manage_btn.isEnabled() and not p.memory_checkbox.isChecked()
    modal_action(qtbot, p.manage_btn.click, manage)
    assert service.list_memories() == []
    for table in tables:
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')] == before[table]
    assert prompt_registry.effective_template('planner.system') == original_prompt


def test_mainwindow_passes_service(qtbot, task_service, date_service, service):
    from app.ui.main_window import MainWindow
    window = MainWindow(task_service, date_service, personalization_service=service)
    qtbot.addWidget(window)
    assert window.personalization_service is service
    assert window.ai_settings_page.personalization_panel.service is service


def test_settings_explicit_refresh_all_panels(qtbot, ai_config_service, prompt_registry, service, monkeypatch):
    page = AISettingsPage(ai_config_service, prompt_registry, personalization_service=service)
    qtbot.addWidget(page)
    calls = []
    for name in ['profiles_panel', 'personalization_panel', 'prompt_panel']:
        monkeypatch.setattr(getattr(page, name), 'refresh', lambda n=name: calls.append(n))
    page.refresh()
    assert calls == ['profiles_panel', 'personalization_panel', 'prompt_panel']


def test_persistence_errors_never_expose_raw_exception(qtbot, service, monkeypatch):
    p = panel(qtbot, service)

    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE DB path / secret')

    monkeypatch.setattr(service, 'set_instructions', fail)
    p.save_btn.click()
    assert 'PRIVATE' not in p.feedback.text()
    monkeypatch.setattr(service, 'set_memory_enabled', fail)
    p.memory_checkbox.click()
    assert not p.memory_checkbox.isChecked()
    assert not p.auto_memory_checkbox.isEnabled()
    monkeypatch.setattr(service, 'get_settings', fail)
    p.refresh()
    assert not p.save_btn.isEnabled()
    assert 'PRIVATE' not in p.feedback.text()


def test_advanced_prompt_save_reset_preview_remains_independent(
    qtbot, ai_config_service, prompt_registry, service, monkeypatch,
):
    import app.ui.ai_settings_page as page_module
    from app.services.prompt_preview_service import PromptPreviewService
    preview = PromptPreviewService(prompt_registry, today_provider=lambda: '2026-01-05')
    page = AISettingsPage(ai_config_service, prompt_registry, preview,
                          personalization_service=service)
    qtbot.addWidget(page)
    page.tabs.setCurrentIndex(2)
    p = page.prompt_panel
    for i in range(p.tree.topLevelItemCount()):
        category = p.tree.topLevelItem(i)
        for j in range(category.childCount()):
            child = category.child(j)
            if child.data(0, Qt.ItemDataRole.UserRole) == 'planner.user':
                p.tree.setCurrentItem(child)
    assert p._current_key == 'planner.user'
    assert '必需变量' in p.var_label.text()
    captured = {}

    class PreviewDialog:
        def __init__(self, title, system_text='', user_text='', context_text='', parent=None):
            captured.update(system=system_text, user=user_text, context=context_text)

        def exec(self):
            return 1

    monkeypatch.setattr(page_module, 'FinalPromptPreviewDialog', PreviewDialog)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    before = service.get_settings()
    p.editor.setPlainText('Advanced {{context_json}} {{output_instruction}}')
    p.save_btn.click()
    assert prompt_registry.effective_template('planner.user') == 'Advanced {{context_json}} {{output_instruction}}'
    p.preview_btn.click()
    assert captured['user'] and captured['context']
    p.reset_btn.click()
    assert not prompt_registry.is_customized('planner.user')
    assert service.get_settings() == before
