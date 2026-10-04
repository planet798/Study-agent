"""实践概览的空态、操作与位置回归。"""
from types import SimpleNamespace

import pytest

from PySide6.QtWidgets import QLabel, QMessageBox

from app.ui.components.workspace_sections import ActionsMenuButton
from app.ui.practice_overview_widgets import PracticeOverviewRow
from app.ui.practice_page import PracticeProjectsPage


def _page(env, qtbot, **kwargs):
    page = PracticeProjectsPage(env.service, env.route_repo, env.skill_repo,
                                env.plan_repo, **kwargs)
    qtbot.addWidget(page)
    return page


def _texts(page):
    return [label.text() for label in page.list_container.findChildren(QLabel)]


def _fail(*args, **kwargs):
    raise RuntimeError('unavailable')


def test_first_empty_has_one_visible_create_action(practice_env, qtbot):
    page = _page(practice_env, qtbot)
    page.show()
    assert page.empty_state.title() == '还没有实践项目'
    assert page.empty_action_btn.isVisible()
    assert not page.create_btn.isVisible()
    assert not page.scroll.isVisible()


def test_default_filter_excludes_archived_and_empty_filter_can_clear(practice_env, qtbot):
    active = practice_env.service.create_project('Active')
    archived = practice_env.service.create_project('Archived')
    practice_env.service.archive_project(archived['id'])
    page = _page(practice_env, qtbot)
    assert page.filter_combo.currentText() == '全部未归档'
    assert 'Active' in _texts(page) and 'Archived' not in _texts(page)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData('completed'))
    assert page.empty_state.title() == '当前筛选下没有项目'
    page.empty_action_btn.click()
    assert page.filter_combo.currentData() is None
    assert page.findChild(PracticeOverviewRow).property('projectId') == active['id']


@pytest.mark.parametrize('status', [None, 'planned', 'completed'])
def test_only_archived_empty_links_to_archived_filter(practice_env, qtbot, status):
    project = practice_env.service.create_project('Archive')
    practice_env.service.archive_project(project['id'])
    page = _page(practice_env, qtbot)
    if status is not None:
        page.filter_combo.setCurrentIndex(page.filter_combo.findData(status))
    assert page.empty_state.title() == '项目已全部归档'
    page.empty_action_btn.click()
    assert page.filter_combo.currentData() == 'archived'
    assert 'Archive' in _texts(page)


def test_list_failure_keeps_filter_and_retry_recovers(practice_env, qtbot, monkeypatch):
    practice_env.service.create_project('Project')
    page = _page(practice_env, qtbot)
    original = practice_env.service.list_projects
    monkeypatch.setattr(practice_env.service, 'list_projects', _fail)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData('planned'))
    assert page.empty_state.title() == '项目数据暂不可用'
    assert page.filter_combo.currentData() == 'planned'
    monkeypatch.setattr(practice_env.service, 'list_projects', original)
    page.empty_action_btn.click()
    assert page.filter_combo.currentData() == 'planned'
    assert 'Project' in _texts(page)


def test_metric_failures_do_not_claim_zero(practice_env, qtbot, monkeypatch):
    practice_env.service.create_project('Project')
    monkeypatch.setattr(practice_env.service, 'get_project_progress', _fail)
    page = _page(practice_env, qtbot,
                 capability_service=SimpleNamespace(count_active_by_project=_fail),
                 readiness_service=SimpleNamespace(get_project_readiness=_fail))
    texts = _texts(page)
    for label in ('里程碑', '成果', '项目能力证据', '学习准备度'):
        assert f'{label}：暂不可用' in texts
    assert '成果：0' not in texts and '项目能力证据：0' not in texts


def test_four_metrics_remain_independent(practice_env, qtbot):
    project = practice_env.service.create_project('Project')
    milestone = practice_env.service.add_milestone(project['id'], 'One')
    practice_env.service.set_milestone_status(milestone['id'], 'done')
    practice_env.service.add_output(project['id'], 'repository', 'Repo')
    page = _page(practice_env, qtbot,
                 capability_service=SimpleNamespace(count_active_by_project=lambda _: 2),
                 readiness_service=SimpleNamespace(get_project_readiness=lambda _: {
                     'satisfied_count': 3, 'total_count': 5}))
    texts = _texts(page)
    assert '里程碑：1 / 1' in texts and '成果：1' in texts
    assert '项目能力证据：2' in texts and '学习准备度：3 / 5 已满足' in texts
    assert not any('%' in text for text in texts)


def test_archive_restore_menu_preserves_milestones_and_outputs(practice_env, qtbot, monkeypatch):
    project = practice_env.service.create_project('Project', status='in_progress')
    practice_env.service.add_milestone(project['id'], 'One')
    practice_env.service.add_output(project['id'], 'repository', 'Repo')
    page = _page(practice_env, qtbot)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    menu = page.findChild(PracticeOverviewRow).findChild(ActionsMenuButton).menu()
    next(a for a in menu.actions() if a.data() == 'archive').trigger()
    assert practice_env.service.projects.get(project['id'])['status'] == 'archived'
    page.empty_action_btn.click()
    menu = page.findChild(PracticeOverviewRow).findChild(ActionsMenuButton).menu()
    next(a for a in menu.actions() if a.data() == 'restore').trigger()
    assert practice_env.service.projects.get(project['id'])['status'] == 'in_progress'
    progress = practice_env.service.get_project_progress(project['id'])
    assert progress['milestones_total'] == 1 and progress['output_count'] == 1


def test_refresh_preserves_position_filter_change_resets(practice_env, qtbot):
    for i in range(20):
        practice_env.service.create_project(f'Project {i}')
    page = _page(practice_env, qtbot)
    page.resize(680, 420)
    page.show()
    bar = page.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 180)
    bar.setValue(160)
    page.refresh()
    qtbot.waitUntil(lambda: bar.value() == 160)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData('planned'))
    qtbot.waitUntil(lambda: bar.value() == 0)
