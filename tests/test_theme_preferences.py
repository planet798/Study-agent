"""Theme preference / switching tests (UI-2)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from app.ui.design import theme_preferences as prefs
from app.ui.design.theme_manager import ThemeManager, ThemeMode


@pytest.fixture(autouse=True)
def _clean_theme(qapp):
    ThemeManager.reset_instance()
    yield
    ThemeManager.reset_instance()


@pytest.fixture()
def settings(tmp_path):
    """隔离的 QSettings（临时 ini），绝不碰真实用户配置。"""
    return QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)


# ---------------- persistence ----------------

def test_default_is_system(settings):
    assert prefs.load_theme_mode(settings) is ThemeMode.SYSTEM


def test_save_and_load_roundtrip(settings):
    prefs.save_theme_mode(ThemeMode.DARK, settings)
    assert prefs.load_theme_mode(settings) is ThemeMode.DARK
    settings.sync()
    # 重新打开同一文件仍读得到
    again = QSettings(settings.fileName(), QSettings.Format.IniFormat)
    assert prefs.load_theme_mode(again) is ThemeMode.DARK


def test_invalid_value_falls_back_to_system(settings):
    settings.setValue(prefs.THEME_SETTING_KEY, "sepia")
    assert prefs.load_theme_mode(settings) is ThemeMode.SYSTEM


def test_setting_key_is_stable(settings):
    prefs.save_theme_mode(ThemeMode.LIGHT, settings)
    assert settings.value(prefs.THEME_SETTING_KEY) == "light"


# ---------------- apply ----------------

def test_apply_saved_theme(qapp, settings):
    prefs.save_theme_mode(ThemeMode.DARK, settings)
    effective = prefs.apply_saved_theme(qapp, settings)
    assert effective == "dark"
    assert ThemeManager.instance().current_mode is ThemeMode.DARK


def test_system_fallback_is_light_or_dark():
    assert ThemeManager.resolve_theme(ThemeMode.SYSTEM) in ("light", "dark")
    assert ThemeManager._system_theme() in ("light", "dark")


# ---------------- repeated apply (regression) ----------------

def test_repeated_apply_emits_once(qapp):
    tm = ThemeManager()
    tm.apply(qapp)  # 首次：light（会 emit）
    seen: list[str] = []
    tm.theme_changed.connect(seen.append)
    tm.apply(qapp)
    tm.apply(qapp)
    assert seen == []  # 同一 effective theme 不重复广播


def test_first_apply_emits_once(qapp):
    tm = ThemeManager()
    seen: list[str] = []
    tm.theme_changed.connect(seen.append)
    tm.apply(qapp)
    assert seen == ["light"]


def test_mode_change_to_same_effective_theme_no_emit(qapp):
    tm = ThemeManager()
    tm.apply(qapp)  # light
    seen: list[str] = []
    tm.theme_changed.connect(seen.append)
    tm.set_theme(ThemeMode.SYSTEM)  # offset 环境解析为 light
    if ThemeManager.resolve_theme(ThemeMode.SYSTEM) == "light":
        assert seen == []


# ---------------- UI entry (AISettingsPage 外观) ----------------

def test_settings_page_theme_combo_switches(qapp, ai_config_service, prompt_registry, settings):
    from app.ui.ai_settings_page import AISettingsPage

    ThemeManager.instance().apply(qapp)
    page = AISettingsPage(
        ai_config_service, prompt_registry, theme_settings=settings
    )
    dark_index = page.theme_combo.findData(ThemeMode.DARK.value)
    page.theme_combo.setCurrentIndex(dark_index)

    assert ThemeManager.instance().current_mode is ThemeMode.DARK
    assert prefs.load_theme_mode(settings) is ThemeMode.DARK
    assert ThemeManager.instance().effective_theme == "dark"


def test_settings_page_combo_initial_state(qapp, ai_config_service, prompt_registry, settings):
    from app.ui.ai_settings_page import AISettingsPage

    prefs.save_theme_mode(ThemeMode.DARK, settings)
    ThemeManager.instance().set_theme(prefs.load_theme_mode(settings))
    page = AISettingsPage(
        ai_config_service, prompt_registry, theme_settings=settings
    )
    assert page.theme_combo.currentData() == ThemeMode.DARK.value


# ---------------- MainWindow startup (no flash) ----------------

def test_mainwindow_reads_saved_theme_before_apply(
    qapp, repo, task_service, date_service, settings
):
    from app.ui.main_window import MainWindow

    prefs.save_theme_mode(ThemeMode.DARK, settings)
    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        theme_settings=settings,
    )
    try:
        assert ThemeManager.instance().current_mode is ThemeMode.DARK
        assert ThemeManager.instance().effective_theme == "dark"
    finally:
        w.close()


def test_mainwindow_does_not_touch_real_settings(
    qapp, repo, task_service, date_service, settings
):
    from app.ui.main_window import MainWindow

    w = MainWindow(
        task_service=task_service,
        date_service=date_service,
        theme_settings=settings,
    )
    try:
        # 隔离 settings 未被显式写主题；真实 settings 也未被读取成非默认
        assert not settings.contains(prefs.THEME_SETTING_KEY)
    finally:
        w.close()
