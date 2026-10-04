"""设置内存草稿的持久化边界与编辑交互。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from app.database.personalization_repository import PersonalizationRepository
from app.services.personalization_service import PersonalizationService
from app.services.prompt_preview_service import PromptPreviewService
from app.ui.ai_settings_page import AISettingsPage


@pytest.fixture()
def personal(conn):
    return PersonalizationService(PersonalizationRepository(conn))


def _page(qtbot, personal, ai_config_service, prompt_registry):
    page = AISettingsPage(ai_config_service, prompt_registry,
                          PromptPreviewService(prompt_registry, today_provider=lambda: '2026-01-05'),
                          personalization_service=personal)
    qtbot.addWidget(page)
    return page


def _select(panel, key):
    for i in range(panel.tree.topLevelItemCount()):
        category = panel.tree.topLevelItem(i)
        for j in range(category.childCount()):
            item = category.child(j)
            if item.data(0, Qt.ItemDataRole.UserRole) == key:
                panel.tree.setCurrentItem(item)
                return
    raise AssertionError(f'missing Prompt {key}')


def test_personal_draft_survives_tabs_and_refresh_but_not_new_page(qtbot, personal, ai_config_service, prompt_registry):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.personalization_panel
    panel.editor.setPlainText('未保存的学习说明')
    for index in (1, 2, 0):
        page.tabs.setCurrentIndex(index)
    page.refresh()
    assert panel.editor.toPlainText() == '未保存的学习说明'
    assert panel.draft_label.text() == '未保存'
    assert personal.get_settings()['instructions'] == ''
    fresh = _page(qtbot, personal, ai_config_service, prompt_registry)
    assert fresh.personalization_panel.editor.toPlainText() == ''


def test_instructions_save_normalizes_and_discard_reads_latest_saved(qtbot, personal, ai_config_service, prompt_registry):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.personalization_panel
    panel.editor.setPlainText('  已保存的说明  ')
    panel.save_btn.click()
    assert panel.editor.toPlainText() == '已保存的说明'
    assert panel.draft_label.text() == '已保存'
    assert not panel.discard_btn.isEnabled()
    panel.editor.setPlainText('本地草稿')
    personal.set_instructions('其他操作保存的说明')
    panel.refresh()
    assert panel.editor.toPlainText() == '本地草稿'
    panel.discard_btn.click()
    assert panel.editor.toPlainText() == '其他操作保存的说明'


def test_instruction_failure_and_unavailable_preserve_draft(qtbot, personal, ai_config_service, prompt_registry, monkeypatch):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.personalization_panel
    panel.editor.setPlainText('保留草稿')
    def fail(*args):
        raise RuntimeError('PRIVATE PATH')
    monkeypatch.setattr(personal, 'set_instructions', fail)
    panel.save_btn.click()
    assert panel.editor.toPlainText() == '保留草稿'
    assert panel.feedback.property('feedbackState') == 'error'
    monkeypatch.setattr(personal, 'get_settings', fail)
    panel.refresh()
    assert panel.editor.toPlainText() == '保留草稿'
    assert not panel.editor.isEnabled()
    assert 'PRIVATE' not in panel.feedback.text()


def test_prompt_drafts_are_independent_across_selection_and_refresh(qtbot, personal, ai_config_service, prompt_registry):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.prompt_panel
    original_a = prompt_registry.effective_template('planner.user')
    original_b = prompt_registry.effective_template('planner.system')
    _select(panel, 'planner.user')
    panel.editor.setPlainText(original_a + '\n草稿 A')
    _select(panel, 'planner.system')
    panel.editor.setPlainText(original_b + '\n草稿 B')
    page.refresh()
    assert panel._current_key == 'planner.system'
    assert panel.editor.toPlainText().endswith('草稿 B')
    _select(panel, 'planner.user')
    assert panel.editor.toPlainText().endswith('草稿 A')
    assert not prompt_registry.is_customized('planner.user')
    assert not prompt_registry.is_customized('planner.system')
    panel.discard_btn.click()
    assert panel.editor.toPlainText() == original_a
    _select(panel, 'planner.system')
    assert panel.editor.toPlainText().endswith('草稿 B')


def test_prompt_validation_failure_then_save_and_confirmed_reset(qtbot, personal, ai_config_service, prompt_registry, monkeypatch):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.prompt_panel
    key = 'planner.user'
    original = prompt_registry.effective_template(key)
    _select(panel, key)
    panel.editor.setPlainText('缺少变量')
    panel.save_btn.click()
    assert panel.editor.toPlainText() == '缺少变量'
    assert '未保存' in panel.draft_label.text()
    assert not prompt_registry.is_customized(key)
    panel.editor.setPlainText(original + '\n有效修改')
    panel.save_btn.click()
    assert prompt_registry.effective_template(key).endswith('有效修改')
    assert '未保存' not in panel.draft_label.text()
    panel.editor.setPlainText(original + '\n未保存修改')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    panel.reset_btn.click()
    assert panel.editor.toPlainText().endswith('未保存修改')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    panel.reset_btn.click()
    assert not prompt_registry.is_customized(key)
    assert panel.editor.toPlainText() == original
    assert '未保存' not in panel.draft_label.text()


def test_preview_uses_saved_template_even_when_a_draft_exists(qtbot, personal, ai_config_service, prompt_registry, monkeypatch):
    from app.ui import ai_settings_page
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.prompt_panel
    _select(panel, 'planner.system')
    original = prompt_registry.effective_template('planner.system')
    panel.editor.setPlainText(original + '\nDRAFT-ONLY-MARKER')
    captured = []
    class Preview:
        def __init__(self, title, **kwargs):
            captured.append(kwargs)
        def exec(self):
            pass
    monkeypatch.setattr(ai_settings_page, 'FinalPromptPreviewDialog', Preview)
    panel.preview_btn.click()
    assert captured
    assert 'DRAFT-ONLY-MARKER' not in captured[0]['system_text']
    assert panel.editor.toPlainText().endswith('DRAFT-ONLY-MARKER')
    assert '已保存' in panel.preview_btn.text()


def test_memory_switches_do_not_save_instruction_drafts(qtbot, personal, ai_config_service, prompt_registry):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.personalization_panel
    panel.editor.setPlainText('临时说明')
    panel.memory_checkbox.click()
    panel.auto_memory_checkbox.click()
    panel.refresh()
    assert panel.editor.toPlainText() == '临时说明'
    assert personal.get_settings()['instructions'] == ''


def test_profile_commands_are_in_menu_and_key_is_not_in_detail(qtbot, personal, ai_config_service, prompt_registry):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    ai_config_service.create_profile(display_name='Profile', base_url='https://example.test',
                                     model='example', api_key='private-key-for-tests')
    panel = page.profiles_panel
    panel.refresh()
    assert panel.edit_key_btn.command in panel.more_btn.menu().actions()
    assert panel.rename_btn.command in panel.more_btn.menu().actions()
    assert panel.delete_btn.command in panel.more_btn.menu().actions()
    assert 'private-key-for-tests' not in panel.info_label.text()


def test_connection_test_is_serialized_and_stopped_results_are_ignored(qtbot, personal, ai_config_service, prompt_registry, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QObject, Signal
    from app.ui import ai_settings_page

    workers = []
    class Worker(QObject):
        succeeded = Signal(object)
        finished = Signal()
        def __init__(self, **kwargs):
            super().__init__(kwargs['parent'])
            workers.append(self)
        def start(self):
            pass
        def isRunning(self):
            return False
    monkeypatch.setattr(ai_settings_page, 'AIConnectionTestWorker', Worker)
    ai_config_service.create_profile(display_name='Profile', base_url='https://example.test',
                                     model='example', api_key='private-key-for-tests')
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.profiles_panel
    panel.test_btn.click()
    panel.refresh()
    assert not panel.test_btn.isEnabled()
    panel._on_test_connection()
    assert len(workers) == 1
    panel.stop_test_worker()
    workers[0].succeeded.emit(SimpleNamespace(ok=True, model='example', latency_ms=1,
                                             oauth_credential=None, message=''))
    assert '连接成功' not in panel.test_result_label.text()


@pytest.mark.parametrize('tab', [0, 1, 2])
def test_settings_panels_fit_narrow_window(qtbot, personal, ai_config_service, prompt_registry, tab):
    ai_config_service.create_profile(display_name='长配置名称' * 12, base_url='https://example.test/' + 'path/' * 20,
                                     model='example', api_key='private-key-for-tests')
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    page.tabs.setCurrentIndex(tab)
    page.resize(480, 650)
    page.show()
    qtbot.waitUntil(lambda: page.width() == 480)
    if tab in (1, 2):
        from app.ui.components.settings_controls import SettingsSplitter
        panel = page.profiles_panel if tab == 1 else page.prompt_panel
        splitter = panel.findChild(SettingsSplitter)
        assert splitter.orientation() == Qt.Orientation.Vertical


def test_stopped_oauth_failure_does_not_open_a_late_dialog(qtbot, personal, ai_config_service, prompt_registry, monkeypatch):
    page = _page(qtbot, personal, ai_config_service, prompt_registry)
    panel = page.profiles_panel
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    panel.stop_test_worker()
    panel._on_oauth_failed('late failure')
    assert not warnings
