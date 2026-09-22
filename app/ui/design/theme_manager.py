"""ThemeManager：Light / Dark / System 主题运行时基础设施。

流程::

    Semantic Tokens  →  ThemeManager  →  QSS template render
                                              ↓
                                   QApplication.setStyleSheet()

- QSS 文件使用 ``{{token}}`` placeholder，由 :func:`render_qss` 做**安全的**
  字符串替换（不使用 ``str.format``，避免与 QSS 的 ``{}`` 冲突）。
- UI-1 不做 Settings Appearance 页面，不做 DB 持久化；只提供 runtime API。
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

from . import tokens as _tokens

_STYLES_DIR = Path(__file__).resolve().parent / "styles"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


def _coerce_mode(mode) -> ThemeMode:
    if isinstance(mode, ThemeMode):
        return mode
    return ThemeMode(str(mode))


def render_qss(template: str, mapping: dict[str, str]) -> str:
    """安全替换 ``{{token}}`` placeholders。

    未知 placeholder 抛 ``KeyError``；替换后若仍残留 ``{{`` 抛 ``ValueError``。
    """
    missing: list[str] = []

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in mapping:
            missing.append(key)
            return match.group(0)
        return str(mapping[key])

    rendered = _PLACEHOLDER_RE.sub(_sub, template)
    if missing:
        raise KeyError(
            "QSS template references unknown tokens: "
            + ", ".join(sorted(set(missing)))
        )
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("QSS render left unresolved placeholders")
    return rendered


def load_template(theme: str) -> str:
    path = _STYLES_DIR / f"{theme}.qss"
    return path.read_text(encoding="utf-8")


def render_theme(theme: str) -> str:
    """渲染指定主题的完整 QSS。"""
    return render_qss(load_template(theme), _tokens.render_map(theme))


def _palette_for(theme: str) -> QPalette:
    c = _tokens.render_map(theme)
    pal = QPalette()

    def set_color(group, role, value):
        pal.setColor(group, role, QColor(value))

    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Window, c["background"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.WindowText, c["text_primary"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Base, c["surface"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.AlternateBase, c["surface_alt"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Text, c["text_primary"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Button, c["surface_alt"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.ButtonText, c["text_primary"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Highlight, c["accent"])
    set_color(
        QPalette.ColorGroup.All, QPalette.ColorRole.HighlightedText, c["text_on_accent"]
    )
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.ToolTipBase, c["surface"])
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.ToolTipText, c["text_primary"])
    set_color(
        QPalette.ColorGroup.All, QPalette.ColorRole.PlaceholderText, c["text_tertiary"]
    )
    set_color(QPalette.ColorGroup.All, QPalette.ColorRole.Link, c["accent"])
    set_color(
        QPalette.ColorGroup.All, QPalette.ColorRole.BrightText, c["text_on_accent"]
    )

    # disabled 组：明确使用 text_disabled，避免系统默认低对比
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        set_color(QPalette.ColorGroup.Disabled, role, c["text_disabled"])
    set_color(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, c["surface_alt"]
    )
    return pal


class ThemeManager(QObject):
    """全局主题管理器（单例通过 :meth:`instance` 获取）。"""

    theme_changed = Signal(str)  # effective theme ("light" / "dark")

    _instance: "ThemeManager | None" = None

    def __init__(self, mode: ThemeMode = ThemeMode.LIGHT, parent: QObject | None = None):
        super().__init__(parent)
        self._mode = _coerce_mode(mode)
        self._app = None
        self._last_emitted: str | None = None

    # ---------- singleton ----------
    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """仅供测试：清除单例。"""
        cls._instance = None

    # ---------- state ----------
    @property
    def current_mode(self) -> ThemeMode:
        return self._mode

    @property
    def effective_theme(self) -> str:
        return self.resolve_theme(self._mode)

    @staticmethod
    def resolve_theme(mode) -> str:
        """把 ThemeMode 解析成 "light" / "dark"；SYSTEM 读取失败则 fallback Light。"""
        mode = _coerce_mode(mode)
        if mode is ThemeMode.LIGHT:
            return "light"
        if mode is ThemeMode.DARK:
            return "dark"
        return ThemeManager._system_theme()

    @staticmethod
    def _system_theme() -> str:
        try:
            app = QGuiApplication.instance()
            if app is None:
                return "light"
            hints = app.styleHints()
            scheme = hints.colorScheme()
            if scheme is not None and str(scheme).endswith("Dark"):
                return "dark"
        except Exception:  # noqa: BLE001 - 平台/版本差异，fallback Light
            return "light"
        return "light"

    # ---------- api ----------
    def tokens(self) -> dict[str, str]:
        """当前 effective theme 的完整 token 映射（colors + typography/radius）。"""
        return _tokens.render_map(self.effective_theme)

    def set_theme(self, mode) -> None:
        """切换主题；若已 apply 过，则立即重新应用。"""
        self._mode = _coerce_mode(mode)
        if self._app is not None:
            self.apply(self._app)

    def apply(self, app) -> str:
        """渲染并应用当前主题 QSS + palette，返回 effective theme。"""
        if app is None:
            return self.effective_theme
        self._app = app
        theme = self.effective_theme
        qss = render_theme(theme)
        app.setStyleSheet(qss)
        app.setPalette(_palette_for(theme))
        if theme != self._last_emitted:
            self._last_emitted = theme
        self.theme_changed.emit(theme)
        return theme


def theme_manager() -> ThemeManager:
    """便捷访问全局 ThemeManager。"""
    return ThemeManager.instance()
