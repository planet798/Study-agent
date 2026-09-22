"""Theme 偏好持久化（QSettings，非 DB）。

- key: ``appearance/theme``，值：``system`` / ``light`` / ``dark``。
- 默认 ``system``。
- 测试通过传入隔离的 ``QSettings`` 实例（临时 ini 文件），不写真实用户配置。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from .theme_manager import ThemeMode

ORGANIZATION = "StudyAgent"
APPLICATION = "StudyAgent"
THEME_SETTING_KEY = "appearance/theme"

_DEFAULT = ThemeMode.SYSTEM


def default_settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def load_theme_mode(settings: QSettings | None = None) -> ThemeMode:
    s = settings if settings is not None else default_settings()
    raw = s.value(THEME_SETTING_KEY, _DEFAULT.value)
    try:
        return ThemeMode(str(raw))
    except ValueError:
        return _DEFAULT


def save_theme_mode(mode, settings: QSettings | None = None) -> ThemeMode:
    resolved = ThemeMode(str(mode)) if not isinstance(mode, ThemeMode) else mode
    s = settings if settings is not None else default_settings()
    s.setValue(THEME_SETTING_KEY, resolved.value)
    s.sync()
    return resolved


def apply_saved_theme(app, settings: QSettings | None = None) -> str:
    """读取偏好并应用（供启动早期调用，避免先 Light 再闪 Dark）。"""
    from .theme_manager import ThemeManager

    tm = ThemeManager.instance()
    tm.set_theme(load_theme_mode(settings))
    return tm.apply(app)
