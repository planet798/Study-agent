"""实践详情分区、菜单、证据保护与可复制地址的交互回归。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from app.ui.components.workspace_sections import ActionsMenuButton
from app.ui.practice_detail_sections import PracticeDetailSection, PracticeMilestonesSection
from app.ui.practice_page import PracticeProjectDetailDialog
from tests.practice_capability_helpers import ready_lora, create_evidence


def _detail(env, qtbot, project_id, **kwargs):
    dialog = PracticeProjectDetailDialog(project_id, env.service, env.route_repo,
                                         env.skill_repo, env.plan_repo, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def _sections(dialog):
    return {s.key: s for s in dialog.findChildren(PracticeDetailSection)}


def _button(widget, text):
    return next(b for b in widget.findChildren(QPushButton) if b.text() == text)


def _action(widget, key):
    return next(a for menu in widget.findChildren(ActionsMenuButton)
                for a in menu.menu().actions() if a.data() == key)


def test_section_order_defaults_and_refresh_preserve_choices(practice_env, qtbot):
    project = practice_env.service.create_project('Project')
    practice_env.service.add_milestone(project['id'], 'Milestone')
    dialog = _detail(practice_env, qtbot, project['id'])
    keys = [dialog.body_layout.itemAt(i).widget().key
            for i in range(dialog.body_layout.count())
            if isinstance(dialog.body_layout.itemAt(i).widget(), PracticeDetailSection)]
    assert keys == ['info', 'milestones', 'outputs', 'readiness', 'evidence', 'scope']
    sections = _sections(dialog)
    assert sections['milestones'].toggle.isChecked()
    assert sections['outputs'].toggle.isChecked()
    assert all(not sections[k].toggle.isChecked() for k in ('info', 'readiness', 'evidence', 'scope'))
    dialog.show()
    sections['milestones'].toggle.click()
    sections['evidence'].toggle.click()
    dialog.refresh()
    qtbot.waitUntil(lambda: _sections(dialog)['evidence'].body.isVisible())
    assert not _sections(dialog)['milestones'].toggle.isChecked()


def test_milestone_start_complete_reset_uses_existing_transitions(practice_env, qtbot):
    project = practice_env.service.create_project('Project')
    milestone = practice_env.service.add_milestone(project['id'], 'Milestone')
    dialog = _detail(practice_env, qtbot, project['id'])
    dialog.show()
    _button(_sections(dialog)['milestones'], '开始').click()
    assert practice_env.service.milestones.get(milestone['id'])['status'] == 'in_progress'
    _button(_sections(dialog)['milestones'], '标记完成').click()
    assert practice_env.service.milestones.get(milestone['id'])['status'] == 'done'
    section = _sections(dialog)['milestones']
    assert not any(b.text() == '重置为待开始' for b in section.findChildren(QPushButton))
    _action(section, 'reset').trigger()
    assert practice_env.service.milestones.get(milestone['id'])['status'] == 'todo'
    assert practice_env.service.projects.get(project['id'])['status'] == 'planned'


def test_delete_and_edit_milestone_menus_target_the_selected_row(practice_env, qtbot, monkeypatch):
    project = practice_env.service.create_project('Project')
    milestone = practice_env.service.add_milestone(project['id'], 'Milestone')
    other = practice_env.service.add_milestone(project['id'], 'Other')
    dialog = _detail(practice_env, qtbot, project['id'])
    edited = []
    monkeypatch.setattr(dialog, '_edit_milestone', lambda m: edited.append(m['id']))
    _action(_sections(dialog)['milestones'], 'edit').trigger()
    assert edited == [milestone['id']]
    _action(_sections(dialog)['milestones'], 'delete').trigger()
    assert practice_env.service.milestones.get(milestone['id']) is None
    assert practice_env.service.milestones.get(other['id']) is not None


def test_new_milestone_and_output_buttons_remain_reachable(practice_env, qtbot, monkeypatch):
    created = []
    monkeypatch.setattr(PracticeProjectDetailDialog, '_add_milestone', lambda self: created.append('milestone'))
    monkeypatch.setattr(PracticeProjectDetailDialog, '_add_output', lambda self: created.append('output'))
    project = practice_env.service.create_project('Project')
    dialog = _detail(practice_env, qtbot, project['id'])
    _button(dialog, '新增里程碑').click()
    _button(dialog, '新增成果').click()
    assert created == ['milestone', 'output']


def test_uri_is_plain_selectable_text(practice_env, qtbot, qapp):
    project = practice_env.service.create_project('Project')
    uri = 'https://example.test/project?topic=<LLM>&branch=main'
    practice_env.service.add_output(project['id'], 'repository', 'Repo', uri=uri)
    dialog = _detail(practice_env, qtbot, project['id'])
    label = next(l for l in _sections(dialog)['outputs'].findChildren(QLabel) if l.text() == uri)
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert label.focusPolicy() == Qt.FocusPolicy.StrongFocus
    dialog.show()
    label.setFocus()
    label.setSelection(0, len(uri))
    qtbot.keyClick(label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert qapp.clipboard().text() == uri


def test_unreferenced_output_delete_menu_updates_summary(practice_env, qtbot):
    project = practice_env.service.create_project('Project')
    output = practice_env.service.add_output(project['id'], 'repository', 'Repo')
    dialog = _detail(practice_env, qtbot, project['id'])
    _action(_sections(dialog)['outputs'], 'delete').trigger()
    assert practice_env.service.outputs.get(output['id']) is None
    assert '成果：0' in [l.text() for l in dialog.findChildren(QLabel)]


@pytest.mark.parametrize('revoked', [False, True])
def test_protected_output_menu_reports_error_and_preserves_history(practice_capability_env, qtbot, monkeypatch, revoked):
    env = practice_capability_env
    ready = ready_lora(env)
    evidence = create_evidence(env, ready.project, ready.lora, [ready.repo['id']])
    if revoked:
        env.pc.revoke_project_topic_evidence(evidence['id'], '历史保留')
    dialog = _detail(env, qtbot, ready.project['id'], capability_service=env.pc)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    # The repository output is first in this fixture.
    _action(_sections(dialog)['outputs'], 'delete').trigger()
    assert warnings and env.service.outputs.get(ready.repo['id']) is not None
    assert env.pc.get_topic_evidence(evidence['id']) is not None


def test_folded_evidence_keeps_eligibility_and_confirmation_entry(practice_capability_env, qtbot, monkeypatch):
    env = practice_capability_env
    ready = ready_lora(env)
    dialog = _detail(env, qtbot, ready.project['id'], capability_service=env.pc)
    dialog.show()
    section = _sections(dialog)['evidence']
    section.toggle.click()
    confirm = _button(section, '确认项目使用')
    qtbot.waitUntil(confirm.isVisible)
    assert confirm.isEnabled()
    confirmed = []
    monkeypatch.setattr(dialog, '_confirm_topic_evidence', lambda c: confirmed.append(c['topic_id']))
    confirm.click()
    assert confirmed == [ready.lora.id]


def test_ineligible_evidence_keeps_reason_and_disabled_button(practice_capability_env, qtbot):
    env = practice_capability_env
    project = env.service.create_project('Project', route_ids=[env.r2.id])
    topic = env.plan_repo.find_topic_by_name_in_route(env.r2.id, 'LoRA / QLoRA')
    env.service.add_topic(project['id'], topic.id)
    dialog = _detail(env, qtbot, project['id'], capability_service=env.pc)
    section = _sections(dialog)['evidence']
    assert not _button(section, '确认项目使用').isEnabled()
    assert any('项目尚未标记为已完成' in l.text() for l in section.findChildren(QLabel))


def test_failed_project_read_can_retry_without_mutation(practice_env, qtbot, monkeypatch):
    project = practice_env.service.create_project('Project')
    dialog = _detail(practice_env, qtbot, project['id'])
    original = practice_env.service.get_project_detail
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(practice_env.service, 'get_project_detail', fail)
    dialog.refresh()
    assert '项目数据暂不可用' in [l.text() for l in dialog.findChildren(QLabel)]
    monkeypatch.setattr(practice_env.service, 'get_project_detail', original)
    _button(dialog, '重试').click()
    assert dialog.title_label.text() == 'Project'
    assert 'milestones' in _sections(dialog)


def test_keyboard_fold_cancels_pending_restore(practice_env, qtbot):
    project = practice_env.service.create_project('Project')
    for i in range(25):
        practice_env.service.add_milestone(project['id'], f'Milestone {i}')
    dialog = _detail(practice_env, qtbot, project['id'])
    dialog.show()
    qtbot.waitUntil(lambda: dialog._scroll_bar.maximum() > 300)
    dialog._restore_scroll(200)
    section = _sections(dialog)['milestones']
    qtbot.keyClick(section.toggle, Qt.Key.Key_Space)
    assert not section.toggle.isChecked()
    assert dialog._scroll_restore_handler is None


@pytest.mark.parametrize('width', [480, 880])
def test_long_project_fields_and_uri_do_not_expand_horizontally(practice_env, qtbot, width):
    project = practice_env.service.create_project('长项目名称' * 12, goal='长目标内容' * 30,
                                                 route_ids=[practice_env.r1.id, practice_env.r2.id, practice_env.r3.id])
    practice_env.service.add_milestone(project['id'], '长里程碑标题' * 30)
    practice_env.service.add_output(project['id'], 'repository', '长成果标题' * 30,
                                    uri='https://example.test/' + 'long-path/' * 50)
    dialog = _detail(practice_env, qtbot, project['id'])
    dialog.resize(width, 620)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.body.width() <= dialog.scroll.viewport().width())
    assert dialog.scroll.horizontalScrollBar().maximum() == 0


def test_output_edit_menu_saves_through_existing_editor(practice_env, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from app.ui import practice_page
    from app.ui.practice_dialogs import OutputEditorDialog

    class EditingDialog(OutputEditorDialog):
        def exec(self):
            self.title_edit.setText('更新后的成果标题')
            self._on_accept()
            return self.result()

    monkeypatch.setattr(practice_page, 'OutputEditorDialog', EditingDialog)
    project = practice_env.service.create_project('Project')
    output = practice_env.service.add_output(project['id'], 'repository', 'Repo')
    dialog = _detail(practice_env, qtbot, project['id'])
    _action(_sections(dialog)['outputs'], 'edit').trigger()
    assert practice_env.service.outputs.get(output['id'])['title'] == '更新后的成果标题'
    assert '更新后的成果标题' in [l.text() for l in _sections(dialog)['outputs'].findChildren(QLabel)]


def test_protected_output_edit_menu_retains_critical_field_lock(practice_capability_env, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from app.ui import practice_page
    from app.ui.practice_dialogs import OutputEditorDialog

    errors = []
    class EditingDialog(OutputEditorDialog):
        def exec(self):
            self.uri_edit.setText('https://example.test/changed')
            self._on_accept()
            errors.append(self.error_label.text())
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(practice_page, 'OutputEditorDialog', EditingDialog)
    env = practice_capability_env
    ready = ready_lora(env)
    create_evidence(env, ready.project, ready.lora, [ready.repo['id']])
    dialog = _detail(env, qtbot, ready.project['id'], capability_service=env.pc)
    _action(_sections(dialog)['outputs'], 'edit').trigger()
    assert errors and errors[0]
    assert env.service.outputs.get(ready.repo['id'])['uri'] == ready.repo['uri']
