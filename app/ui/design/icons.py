"""Icon system（Microsoft Fluent System Icons，最小集合）。

规则：
- Regular = 默认 / 未选中；Filled = 选中 / 激活。
- 图标是**单色可着色**的：同一个 SVG 资产通过 semantic foreground 着色，
  不存在 ``light_home.svg`` / ``dark_home.svg`` 两套重复资产。
- 不 vendoring 整个 Fluent Icons 仓库；只保留项目当前需要的最小 SVG 集。
  资产许可见 ``icons/LICENSE``。

API::

    icon(IconName.SETTINGS)                      # 默认色（当前主题 text_primary 由调用方传）
    icon(IconName.HOME, filled=True, color="#fff")
    pixmap(IconName.ADD, size=16, color=accent)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_ICON_DIR = Path(__file__).resolve().parent / "icons"


class IconName(str, Enum):
    HOME = "home"
    BOOK = "book"
    PROJECT = "clipboard_task"
    CALENDAR = "calendar"
    SETTINGS = "settings"
    MENU = "menu"
    NAVIGATION = "navigation"
    CHEVRON_LEFT = "chevron_left"
    CHEVRON_RIGHT = "chevron_right"
    MORE = "more_horizontal"
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    PLAY = "play"
    CHECK = "checkmark"
    WARNING = "warning"
    INFO = "info"
    SEARCH = "search"
    EYE = "eye"
    EYE_OFF = "eye_off"


ICON_NAMES: tuple[str, ...] = tuple(i.value for i in IconName)

# 有官方 filled 变体的图标；其余 filled 请求回退到 regular。
_FILLED_AVAILABLE: frozenset[str] = frozenset(
    {"home", "book", "calendar", "settings"}
)

_pixmap_cache: dict[tuple, QPixmap] = {}


def _coerce(name) -> str:
    if isinstance(name, IconName):
        return name.value
    name = str(name)
    if name not in ICON_NAMES:
        raise ValueError(f"unknown icon: {name!r}")
    return name


def icon_path(name, filled: bool = False) -> Path:
    """返回图标 SVG 路径（filled 不存在时回退 regular）。"""
    value = _coerce(name)
    if filled and value in _FILLED_AVAILABLE:
        candidate = _ICON_DIR / f"{value}_filled.svg"
        if candidate.exists():
            return candidate
    path = _ICON_DIR / f"{value}.svg"
    if not path.exists():
        raise FileNotFoundError(f"icon asset missing: {path}")
    return path


def pixmap(
    name,
    size: int = 24,
    color: str | QColor | None = None,
    filled: bool = False,
) -> QPixmap:
    """把图标渲染为指定大小 / 颜色的 ``QPixmap``（带缓存）。"""
    qcolor = QColor(color) if color is not None else QColor("#000000")
    key = (_coerce(name), int(size), qcolor.name(QColor.NameFormat.HexArgb), bool(filled))
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    pm = QPixmap(int(size), int(size))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(icon_path(name, filled=filled)))
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        # 用目标色 source-in 覆盖，实现单色着色
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pm.rect(), qcolor)
    finally:
        painter.end()
    _pixmap_cache[key] = pm
    return pm


def icon(
    name,
    size: int = 24,
    color: str | QColor | None = None,
    filled: bool = False,
) -> QIcon:
    """返回一个 ``QIcon``（可随语义色着色）。"""
    return QIcon(pixmap(name, size=size, color=color, filled=filled))


def clear_cache() -> None:
    """仅供测试 / 主题切换后清理。"""
    _pixmap_cache.clear()


__all__ = [
    "IconName",
    "ICON_NAMES",
    "icon",
    "icon_path",
    "pixmap",
    "clear_cache",
]
