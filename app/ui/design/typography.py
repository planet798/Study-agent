"""Typography tokens：定义 role，不按页面写具体字号。

业务组件通过 ``font_for(role)`` 取得 ``QFont``（Qt-native），
全局 QSS 只负责 legacy 文本的字体族 / 基础字号。
"""

from __future__ import annotations

from PySide6.QtGui import QFont

# ---- roles ----
DISPLAY = "display"
TITLE_LARGE = "title_large"
TITLE = "title"
SUBTITLE = "subtitle"
BODY = "body"
BODY_SECONDARY = "body_secondary"
CAPTION = "caption"
MONOSPACE = "monospace"

ROLES: tuple[str, ...] = (
    DISPLAY,
    TITLE_LARGE,
    TITLE,
    SUBTITLE,
    BODY,
    BODY_SECONDARY,
    CAPTION,
    MONOSPACE,
)

# 字号（px）。UI-1 保持与旧 QSS 接近，避免页面跳动。
_SIZES: dict[str, int] = {
    DISPLAY: 28,
    TITLE_LARGE: 22,
    TITLE: 17,
    SUBTITLE: 15,
    BODY: 14,
    BODY_SECONDARY: 13,
    CAPTION: 12,
    MONOSPACE: 13,
}

_WEIGHTS: dict[str, QFont.Weight] = {
    DISPLAY: QFont.Weight.DemiBold,
    TITLE_LARGE: QFont.Weight.DemiBold,
    TITLE: QFont.Weight.DemiBold,
    SUBTITLE: QFont.Weight.Medium,
    BODY: QFont.Weight.Normal,
    BODY_SECONDARY: QFont.Weight.Normal,
    CAPTION: QFont.Weight.Normal,
    MONOSPACE: QFont.Weight.Normal,
}

FONT_FAMILY = (
    '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", '
    '"Microsoft YaHei", "Noto Sans CJK SC", sans-serif'
)

FONT_FAMILY_MONO = (
    '"Cascadia Mono", "Consolas", "Noto Sans Mono", monospace'
)


def size_for(role: str) -> int:
    if role not in _SIZES:
        raise ValueError(f"unknown typography role: {role!r}")
    return _SIZES[role]


def font_for(role: str) -> QFont:
    """返回该 role 的 ``QFont``（族 / 字号 / 字重）。"""
    size = size_for(role)
    font = QFont()
    if role == MONOSPACE:
        font.setFamily("Cascadia Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
    else:
        font.setFamily("Segoe UI Variable")
        font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(size)
    font.setWeight(_WEIGHTS[role])
    return font
