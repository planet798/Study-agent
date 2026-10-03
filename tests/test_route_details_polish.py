"""路线精修的交互回归：折叠、操作、空态和滚动恢复。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from app.ui.route_detail_sections import RouteDetailSection, RoutePhaseSection
from app.ui.route_overview_widgets import RouteActionsButton, RouteOverviewRow
from app.ui.routes_page import RouteDetailDialog
from tests.test_routes_ui3 import routes_env, _clean_theme, _detail, _page


def _sections(dialog):
    return {section.key: section for section in dialog.findChildren(RouteDetailSection)}


def _manual_detail(qtbot, routes_env, *, planned=True):
    route = routes_env['route_service'].create_learning_route('交互测试路线', goal='明确学习目标')
    phases, topics = [], []
    if planned:
        plan_service = routes_env['route_plan']
        plan_service.ensure_manual_plan(route.id, route.name)
        for order in (1, 2):
            phase = plan_service.add_phase(route.id, f'阶段 {order}', order_index=order)
            topic = plan_service.add_topic(route.id, phase.id, f'知识点 {order}')
            phases.append(phase)
            topics.append(topic)
    dialog = RouteDetailDialog(route, routes_env['route_service'], routes_env['route_plan'],
                               progress_service=routes_env['progress'])
    qtbot.addWidget(dialog)
    return dialog, phases, topics


def test_default_expands_current_phase_and_refresh_keeps_choices(qtbot, routes_env):
    dialog, phases, _ = _manual_detail(qtbot, routes_env)
    dialog.show()
    sections = _sections(dialog)
    first, second = (f'phase:{p.id}' for p in phases)
    assert sections[first].body.isVisible()
    assert not sections[second].body.isVisible()
    qtbot.mouseClick(sections[first].toggle, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(sections[second].toggle, Qt.MouseButton.LeftButton)
    dialog.refresh()
    sections = _sections(dialog)
    assert not sections[first].body.isVisible()
    assert sections[second].toggle.isChecked()
    qtbot.waitUntil(lambda: sections[second].body.isVisible())
    assert not sections['evidence'].body.isVisible()


def test_all_complete_does_not_claim_mastery_and_all_phases_start_closed(qtbot, routes_env):
    dialog, phases, topics = _manual_detail(qtbot, routes_env)
    env = routes_env['env']
    for topic in topics:
        task = env.repo.create('学习知识点', scheduled_date='2026-10-03',
                               topic_id=topic.id, route_id=dialog.route.id)
        env.repo.mark_done(task.id)
    # Reopen: default expansion comes from the persisted curriculum state.
    reopened = RouteDetailDialog(dialog.route, dialog.route_service, dialog.route_plan_service,
                                 progress_service=routes_env['progress'])
    qtbot.addWidget(reopened)
    assert all(not s.toggle.isChecked() for s in reopened.findChildren(RoutePhaseSection))
    texts = [l.text() for l in reopened.findChildren(QLabel)]
    assert '当前阶段：课程学习已完成' in texts
    assert '掌握度：暂无验收数据' in texts
    assert '课程覆盖 2/2' in texts


def test_empty_plan_has_one_creation_area_and_keeps_route_metadata(qtbot, routes_env):
    dialog, _, _ = _manual_detail(qtbot, routes_env, planned=False)
    for _ in range(3):
        dialog.refresh()
    texts = [l.text() for l in dialog.findChildren(QLabel)]
    assert '明确学习目标' in texts
    assert '路线信息' in texts
    buttons = [b.text() for b in dialog.findChildren(QPushButton)]
    assert buttons.count('创建手动学习计划') == 1
    assert buttons.count('AI 生成学习计划') == 1
    assert '添加阶段' not in buttons
    top_menu = dialog.action_row.itemAt(0).widget().menu()
    assert not any(a.data() == 'ai' for a in top_menu.actions())


def test_delete_from_reopened_phase_uses_existing_service(qtbot, routes_env, monkeypatch):
    dialog, phases, topics = _manual_detail(qtbot, routes_env)
    monkeypatch.setattr(dialog, '_confirm_delete_dialog', lambda text: True)
    second = _sections(dialog)[f'phase:{phases[1].id}']
    second.toggle.click()
    menu = next(b.menu() for b in second.body.findChildren(RouteActionsButton)
                if any(a.data() == 'delete_topic' for a in b.menu().actions()))
    next(a for a in menu.actions() if a.data() == 'delete_topic').trigger()
    assert routes_env['env'].plan_repo.get_topic(topics[1].id) is None
    assert routes_env['env'].plan_repo.get_topic(topics[0].id) is not None
    assert _sections(dialog)[f'phase:{phases[1].id}'].toggle.isChecked()


def test_topic_history_protection_still_applies_through_menu(qtbot, routes_env, monkeypatch):
    from app.ui import routes_page
    dialog, phases, topics = _manual_detail(qtbot, routes_env)
    env = routes_env['env']
    env.repo.create('已有学习记录', scheduled_date='2026-10-03', topic_id=topics[0].id,
                    route_id=dialog.route.id)
    warnings = []
    monkeypatch.setattr(routes_page, 'show_warning', lambda *args: warnings.append(args))
    phase = _sections(dialog)[f'phase:{phases[0].id}']
    menu = phase.body.findChild(RouteActionsButton).menu()
    next(a for a in menu.actions() if a.data() == 'delete_topic').trigger()
    assert warnings and env.plan_repo.get_topic(topics[0].id) is not None


def test_profile_editor_remains_reachable(qtbot, routes_env, monkeypatch):
    dialog = _detail(qtbot, routes_env)
    clicked = []
    monkeypatch.setattr(dialog, '_on_edit_profile', lambda topic: clicked.append(topic.id))
    phase = dialog.findChildren(RoutePhaseSection)[0]
    menu = phase.body.findChild(RouteActionsButton).menu()
    next(a for a in menu.actions() if a.data() == 'edit_profile').trigger()
    assert len(clicked) == 1


def test_structure_failure_does_not_offer_creation(qtbot, routes_env, monkeypatch):
    dialog = _detail(qtbot, routes_env)
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(dialog.route_plan_service, 'get_structure', fail)
    dialog.refresh()
    texts = [l.text() for l in dialog.findChildren(QLabel)]
    assert '课程数据暂不可用' in texts
    assert '当前阶段：数据暂不可用' in texts
    assert not any(b.text() == '创建手动学习计划' for b in dialog.findChildren(QPushButton))


def test_detail_refresh_preserves_scroll(qtbot, routes_env):
    dialog = _detail(qtbot, routes_env)
    dialog.resize(680, 480)
    dialog.show()
    bar = dialog.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 140)
    bar.setValue(120)
    dialog.refresh()
    qtbot.waitUntil(lambda: bar.value() == 120)


def test_new_restore_overrides_old_and_manual_scroll_cancels_it(qtbot, routes_env, qapp):
    dialog = _detail(qtbot, routes_env)
    dialog.resize(680, 420)
    dialog.show()
    bar = dialog.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 220)
    keeper = dialog._scroll_keeper
    keeper.restore(100)
    keeper.restore(200)
    qapp.processEvents()
    assert bar.value() == 200
    keeper.restore(100)
    bar.sliderPressed.emit()
    bar.setValue(20)
    qapp.processEvents()
    assert bar.value() == 20


def test_close_cancels_pending_scroll_restore(qtbot, routes_env, qapp):
    dialog = _detail(qtbot, routes_env)
    dialog.resize(680, 420)
    dialog.show()
    bar = dialog.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 200)
    bar.setValue(20)
    dialog._scroll_keeper.restore(200)
    dialog.close()
    qapp.processEvents()
    assert bar.value() == 20


@pytest.mark.parametrize('width', [480, 880])
def test_long_fields_do_not_expand_detail_horizontally(qtbot, routes_env, width):
    dialog, phases, _ = _manual_detail(qtbot, routes_env)
    dialog.route_service.update_route_info(dialog.route.id, name='长路线名称' * 16,
                                           goal='长目标描述' * 40)
    dialog.refresh()
    dialog.resize(width, 620)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.scroll.widget().width() <= dialog.scroll.viewport().width())
    assert dialog.scroll.horizontalScrollBar().maximum() == 0


def test_archive_and_restore_menus_preserve_history(qtbot, routes_env, monkeypatch):
    page = _page(qtbot, routes_env)
    route = routes_env['env'].route_repo.get_by_key('R2_LLM_POST_TRAINING')
    before = len(routes_env['env'].plan_repo.list_topics_by_route(route.id))
    monkeypatch.setattr(page, '_confirm_archive_dialog', lambda: True)
    row = next(r for r in page.findChildren(RouteOverviewRow)
               if r.findChildren(QLabel)[0].text() == route.name)
    next(a for a in row.findChild(RouteActionsButton).menu().actions()
         if a.data() == 'archive').trigger()
    assert routes_env['env'].route_repo.get(route.id).is_archived
    page._on_toggle_archived()
    row = next(r for r in page.findChildren(RouteOverviewRow)
               if r.findChildren(QLabel)[0].text() == route.name)
    next(a for a in row.findChild(RouteActionsButton).menu().actions()
         if a.data() == 'restore').trigger()
    assert not routes_env['env'].route_repo.get(route.id).is_archived
    assert len(routes_env['env'].plan_repo.list_topics_by_route(route.id)) == before


def test_scroll_restore_waits_for_late_content(qtbot, qapp):
    from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
    from app.ui.route_scroll import RouteScrollKeeper

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    scroll.resize(320, 200)
    qtbot.addWidget(scroll)
    scroll.show()
    qapp.processEvents()
    keeper = RouteScrollKeeper(scroll)
    keeper.restore(150)
    qapp.processEvents()
    assert scroll.verticalScrollBar().maximum() == 0
    for index in range(30):
        label = QLabel(f'知识点 {index}')
        label.setMinimumHeight(24)
        layout.addWidget(label)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().value() == 150)


def test_section_toggle_is_keyboard_accessible(qtbot, routes_env):
    dialog, phases, _ = _manual_detail(qtbot, routes_env)
    dialog.show()
    section = _sections(dialog)[f'phase:{phases[0].id}']
    section.toggle.setFocus()
    qtbot.keyClick(section.toggle, Qt.Key.Key_Space)
    assert not section.toggle.isChecked()
    qtbot.keyClick(section.toggle, Qt.Key.Key_Space)
    assert section.toggle.isChecked()


def test_finishing_only_theory_does_not_complete_detail_curriculum(qtbot, routes_env):
    from app.ui.components.progress_bar import SAProgressBar
    env = routes_env['env']
    route = env.route_repo.get_by_key('R2_LLM_POST_TRAINING')
    topic = env.plan_repo.list_topics_by_route(route.id)[0]
    statuses = env.tl.get_component_status(topic.id)
    theory = next(s for s in statuses if s['activity_kind'] == 'theory')
    assert sum(s['required'] for s in statuses) > 1
    task = env.repo.create('完成理论学习', topic_id=topic.id, route_id=route.id,
                           scheduled_date='2026-10-03', component_id=theory['component_id'],
                           learning_activity_kind='theory')
    env.repo.mark_done(task.id)
    dialog = _detail(qtbot, routes_env)
    covered = next(b for b in dialog.findChildren(SAProgressBar)
                   if b.label().startswith('课程覆盖'))
    assert covered.value() == 0
    assert next(s for s in dialog.findChildren(RoutePhaseSection)).toggle.isChecked()
    assert '当前阶段：课程学习已完成' not in [l.text() for l in dialog.findChildren(QLabel)]
