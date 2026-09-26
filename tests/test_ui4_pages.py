"""UI-4 targeted tests: Practice / Settings redesign."""

from __future__ import annotations

import pytest

from app.ui.design.theme_manager import ThemeManager


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    ThemeManager.instance().apply(qapp)
    yield
    ThemeManager.reset_instance()


# ============================================================
# Settings
# ============================================================

def test_settings_usable_without_ai_service(qapp):
    from app.ui.ai_settings_page import AISettingsPage, AIProfilesPanel, PromptManagerPanel

    page = AISettingsPage(None, None)
    assert page.theme_combo is not None
    assert isinstance(page.profiles_panel, AIProfilesPanel)
    assert isinstance(page.prompt_panel, PromptManagerPanel)
    # appearance still works
    idx = page.theme_combo.findData("dark")
    page.theme_combo.setCurrentIndex(idx)
    assert ThemeManager.instance().current_mode is not None


def test_settings_tabs_unchanged(qapp, ai_config_service, prompt_registry):
    from PySide6.QtWidgets import QTabWidget

    from app.ui.ai_settings_page import AISettingsPage

    page = AISettingsPage(ai_config_service, prompt_registry)
    tab = page.findChild(QTabWidget)
    labels = [tab.tabText(i) for i in range(tab.count())]
    assert labels == ["模型 / API", "Prompt 管理"]


def test_profile_list_no_unicode_markers(qapp, ai_config_service):
    from app.ui.ai_settings_page import AIProfilesPanel

    ai_config_service.create_profile(
        "P1", "https://x/v1", "m", api_key="sk-1"
    )
    panel = AIProfilesPanel(ai_config_service)
    text = panel.list_widget.item(0).text()
    assert "●" not in text and "○" not in text
    assert "P1" in text


def test_profile_source_is_banner(qapp, ai_config_service):
    from app.ui.ai_settings_page import AIProfilesPanel

    panel = AIProfilesPanel(ai_config_service)
    assert panel.source_banner is not None
    assert panel.source_label is panel.source_banner.description_label()


def test_profile_detail_never_shows_full_key(qapp, ai_config_service):
    from app.ui.ai_settings_page import AIProfilesPanel

    ai_config_service.create_profile(
        "P1", "https://x/v1", "m", api_key="sk-super-secret"
    )
    panel = AIProfilesPanel(ai_config_service)
    panel.refresh()
    panel.list_widget.setCurrentRow(0)
    assert "sk-super-secret" not in panel.info_label.text()
    assert "已配置" in panel.info_label.text()


def test_prompt_editor_monospace_and_state_tag(qapp, prompt_registry):
    from app.ui.ai_settings_page import PromptManagerPanel

    panel = PromptManagerPanel(prompt_registry)
    assert panel.editor.objectName() == "SAPromptEditor"
    assert panel.editor.accessibleName() == "Prompt 编辑器"
    panel.refresh()
    assert panel.status_tag is not None


# ============================================================
# Practice
# ============================================================

def _practice_page(env, qtbot):
    from app.ui.practice_page import PracticeProjectsPage

    page = PracticeProjectsPage(
        env.service, env.route_repo, env.skill_repo, env.plan_repo
    )
    qtbot.addWidget(page)
    return page


def test_practice_card_uses_sacard_and_badge(practice_env, qtbot):
    from app.ui.components.card import SACard
    from app.ui.components.status_badge import SAStatusBadge

    env = practice_env
    env.service.create_project("P", "llm_training", route_ids=[env.r1.id])
    page = _practice_page(env, qtbot)
    assert page.list_container.findChildren(SACard)
    badges = page.list_container.findChildren(SAStatusBadge)
    assert badges and badges[0].text() == "计划中"


def test_practice_card_separates_metrics(practice_env, qtbot):
    from PySide6.QtWidgets import QLabel

    env = practice_env
    p = env.service.create_project("P", "other", route_ids=[env.r1.id])
    env.service.add_milestone(p["id"], "m1")
    env.service.add_output(p["id"], "repository", "repo")
    page = _practice_page(env, qtbot)
    texts = [l.text() for l in page.list_container.findChildren(QLabel)]
    assert any("里程碑：" in t for t in texts)
    assert any("成果：" in t for t in texts)
    assert any("项目能力证据：" in t for t in texts)
    # 不存在统一 project progress %
    assert not any("进度" in t and "%" in t for t in texts)


def test_practice_empty_state(practice_env, qtbot):
    env = practice_env
    page = _practice_page(env, qtbot)
    assert page.empty_state.title() == "还没有实践项目"
    assert page.empty_state.isHidden() is False


def test_practice_detail_sections(practice_env, qtbot):
    from PySide6.QtWidgets import QLabel

    from app.ui.practice_page import PracticeProjectDetailDialog

    env = practice_env
    p = env.service.create_project("P", "other", route_ids=[env.r1.id])
    dlg = PracticeProjectDetailDialog(
        p["id"], env.service, env.route_repo, env.skill_repo, env.plan_repo
    )
    qtbot.addWidget(dlg)
    joined = "\n".join(l.text() for l in dlg.findChildren(QLabel))
    for section in ("概览", "关联学习路线", "关联技能", "关联 Topic",
                    "里程碑", "项目成果"):
        assert section in joined, section
    assert "【" not in joined
