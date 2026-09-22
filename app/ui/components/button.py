"""SAButton / SAIconButton。

- ``SAButton`` 变体：primary / secondary / subtle / danger；尺寸 small / medium。
- secondary / primary / danger 的**文字 palette 兜底**在组件内部完成，调用方不再需要
  ``apply_secondary_button_text``。主题切换时自动重新应用。
- ``SAIconButton`` 为 icon-only 按钮；无文字时强制要求 tooltip + accessibleName。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QPushButton

from ..design import icons as _icons
from ..design import typography as _type
from ..design.theme_manager import theme_manager

VARIANTS: tuple[str, ...] = ("primary", "secondary", "subtle", "danger")
SIZES: tuple[str, ...] = ("small", "medium")

_OBJECT_NAMES = {
    "primary": "PrimaryButton",
    "secondary": "SecondaryButton",
    "danger": "DangerButton",
    "subtle": "",
}


def apply_button_text_palette(button: QPushButton, color: str) -> None:
    """把 ``ButtonText`` palette 显式设为指定颜色。

    Windows 原生 QPushButton style 可能忽略 QSS 的 ``color``，导致文字不可读。
    只影响文字颜色，不改尺寸 / 边框 / 布局。供 SAButton 内部与 legacy helper 复用。
    """
    qcolor = QColor(color)
    pal = button.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.ButtonText, qcolor)
    button.setPalette(pal)


class SAButton(QPushButton):
    """统一按钮组件。"""

    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        size: str = "medium",
        parent=None,
        icon_name=None,
        icon_color: str | None = None,
        filled: bool = False,
    ):
        super().__init__(text, parent)
        if variant not in VARIANTS:
            raise ValueError(f"unknown SAButton variant: {variant!r}")
        if size not in SIZES:
            raise ValueError(f"unknown SAButton size: {size!r}")
        self._variant = variant
        self._size = size
        self._icon_name = icon_name
        self._icon_color = icon_color
        self._filled = filled
        self.setProperty("saButton", True)

        if variant == "subtle":
            self.setObjectName("SAButton")
        else:
            self.setObjectName(_OBJECT_NAMES[variant])
        self.setProperty("saVariant", variant)
        self.setProperty("saSize", size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._apply_font()
        if icon_name is not None:
            self._apply_icon()
        self._apply_palette()

        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- public ----------
    def variant(self) -> str:
        return self._variant

    def size(self) -> str:
        return self._size

    def set_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown SAButton variant: {variant!r}")
        self._variant = variant
        self.setObjectName(
            "SAButton" if variant == "subtle" else _OBJECT_NAMES[variant]
        )
        self.setProperty("saVariant", variant)
        if self._variant == "subtle":
            self.setObjectName("SAButton")
        self._repolish()
        self._apply_palette()

    # ---------- internal ----------
    def _text_color(self) -> str:
        t = theme_manager().tokens()
        return {
            "primary": t["text_on_accent"],
            "secondary": t["accent"],
            "danger": t["danger"],
            "subtle": t["text_primary"],
        }[self._variant]

    def _apply_palette(self) -> None:
        apply_button_text_palette(self, self._text_color())

    def _apply_font(self) -> None:
        role = _type.BODY if self._size == "medium" else _type.BODY_SECONDARY
        self.setFont(_type.font_for(role))

    def _apply_icon(self) -> None:
        color = self._icon_color or self._text_color()
        self.setIcon(_icons.icon(self._icon_name, color=color, filled=self._filled))
        self.setIconSize(QSize(16, 16))

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_palette()
        if self._icon_name is not None:
            self._apply_icon()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


class SAIconButton(QPushButton):
    """icon-only 按钮（subtle / danger）。

    不固定死宽：通过 ``sizeHint`` / ``minimumSizeHint`` 与 ``iconSize`` 控制。
    没有文字时必须有 tooltip 与 accessibleName。
    """

    def __init__(
        self,
        icon_name,
        variant: str = "subtle",
        icon_size: int = 18,
        tooltip: str | None = None,
        accessible_name: str | None = None,
        color: str | None = None,
        filled: bool = False,
        parent=None,
    ):
        super().__init__("", parent)
        if variant not in ("subtle", "danger"):
            raise ValueError(f"unsupported SAIconButton variant: {variant!r}")
        self._icon_name = icon_name
        self._variant = variant
        self._icon_size = int(icon_size)
        self._explicit_color = color
        self._filled = filled

        name = accessible_name or tooltip or str(getattr(icon_name, "value", icon_name))
        self.setAccessibleName(name)
        self.setToolTip(tooltip or name)

        self.setObjectName("SAIconButton")
        self.setProperty("saButton", True)
        self.setProperty("saVariant", variant)
        self.setProperty("iconOnly", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_icon()
        self._apply_palette()
        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- sizing ----------
    def _pad(self) -> int:
        return 8

    def sizeHint(self) -> QSize:
        side = self._icon_size + self._pad() * 2
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._icon_size + 8, self._icon_size + 8)

    # ---------- internal ----------
    def _icon_color(self) -> str:
        t = theme_manager().tokens()
        if self._explicit_color:
            return self._explicit_color
        return t["danger"] if self._variant == "danger" else t["text_primary"]

    def _apply_icon(self) -> None:
        self.setIcon(
            _icons.icon(self._icon_name, color=self._icon_color(), filled=self._filled)
        )

    def _apply_palette(self) -> None:
        apply_button_text_palette(self, self._icon_color())

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_icon()
        self._apply_palette()
