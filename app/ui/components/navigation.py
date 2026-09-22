"""SANavigationItem / SANavigationSidebar（Fluent 2 左侧导航）。

- ``SANavigationItem`` 继承 ``QPushButton``，因此天然支持 click / text / isEnabled /
  checkable / keyboard（Tab + Enter/Space），同时由全局 QSS 提供 Fluent 视觉：
  accent indicator + surface_selected + Filled icon。
- ``SANavigationSidebar`` 管理 items、collapsed 状态、可用性降级与 page_requested 信号。
  不包含任何业务逻辑。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..design import icons as _icons
from ..design import spacing as _spacing
from ..design import typography as _type
from ..design.theme_manager import theme_manager
from .button import SAIconButton

EXPANDED_WIDTH = 228
COLLAPSED_WIDTH = 60


def key_value(key) -> str:
    """把 PageKey 枚举 / 字符串统一成稳定的字符串 key。

    ``class PageKey(str, Enum)`` 在 Python 3.11+ 下 ``str(member)`` 返回
    ``'PageKey.X'`` 而不是 value；统一用本 helper 归一化。
    """
    if isinstance(key, Enum):
        return str(key.value)
    return str(key)


class SANavigationItem(QPushButton):
    """单个导航项。selected 状态由 checked 表示。"""

    def __init__(
        self,
        key: str,
        text: str,
        icon_name,
        parent: QWidget | None = None,
    ):
        super().__init__(text, parent)
        self._key = key_value(key)
        self._label = text
        self._icon_name = icon_name
        self._collapsed = False
        self._icon_size = 20

        self.setObjectName("SANavigationItem")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(_type.font_for(_type.BODY))
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        self.setAccessibleName(text)

        self._refresh()
        self.toggled.connect(self._on_toggled)
        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- public ----------
    def key(self) -> str:
        return self._key

    def label(self) -> str:
        return self._label

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.setProperty("collapsed", "true" if self._collapsed else "false")
        # collapsed 只显示 icon；展开恢复文本。
        self.setText("" if self._collapsed else self._label)
        self.setToolTip(self._label)
        if self._collapsed:
            self.setAccessibleName(self._label)
        self._repolish()
        self._refresh_icon()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def refresh_appearance(self) -> None:
        self._refresh_icon()

    # ---------- internal ----------
    def _icon_color(self) -> str:
        t = theme_manager().tokens()
        if not self.isEnabled():
            return t["text_disabled"]
        return t["accent"] if self.isChecked() else t["text_secondary"]

    def _refresh_icon(self) -> None:
        self.setIcon(
            _icons.icon(
                self._icon_name,
                size=self._icon_size,
                color=self._icon_color(),
                filled=self.isChecked(),
            )
        )

    def _refresh(self) -> None:
        self._refresh_icon()
        if self._collapsed:
            self.setText("")
        self._repolish()

    def _on_toggled(self, _checked: bool) -> None:
        self._refresh_icon()
        self._repolish()

    def _on_theme_changed(self, _theme: str) -> None:
        self._refresh_icon()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_icon()
        super().changeEvent(event)


class SANavigationSidebar(QWidget):
    """左侧导航栏。"""

    page_requested = Signal(str)      # PageKey value
    collapsed_changed = Signal(bool)

    def __init__(self, specs, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SANavigationSidebar")
        # 纯 QWidget 需要 WA_StyledBackground 才会按 QSS 绘制 background。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._collapsed = False
        self._items: dict[str, SANavigationItem] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(_spacing.SM, _spacing.MD, _spacing.SM, _spacing.MD)
        root.setSpacing(_spacing.XS)

        # ---- brand ----
        brand = QHBoxLayout()
        brand.setContentsMargins(_spacing.XS, 0, _spacing.XS, _spacing.SM)
        brand.setSpacing(_spacing.SM)
        self.brand_icon = QLabel()
        self.brand_icon.setObjectName("SABrandIcon")
        self.brand_title = QLabel("Study Agent")
        self.brand_title.setObjectName("SABrandTitle")
        brand.addWidget(self.brand_icon)
        brand.addWidget(self.brand_title)
        brand.addStretch()
        root.addLayout(brand)

        # ---- collapse toggle ----
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, _spacing.XS)
        self.collapse_btn = SAIconButton(
            _icons.IconName.MENU,
            icon_size=16,
            tooltip="收起侧栏",
            accessible_name="收起侧栏",
        )
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        toggle_row.addWidget(self.collapse_btn)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # ---- main items ----
        self.items_layout = QVBoxLayout()
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(_spacing.XS)
        self.footer_layout = QVBoxLayout()
        self.footer_layout.setContentsMargins(0, _spacing.XS, 0, 0)
        self.footer_layout.setSpacing(_spacing.XS)
        for spec in specs:
            self._add_item(spec)
        root.addLayout(self.items_layout)

        root.addStretch()

        # ---- divider + settings ----
        divider = QFrame()
        divider.setObjectName("SADivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        root.addWidget(divider)
        root.addLayout(self.footer_layout)

        self.setFixedWidth(EXPANDED_WIDTH)
        self._apply_brand_icon()
        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- items ----------
    def _add_item(self, spec) -> SANavigationItem:
        item = SANavigationItem(spec.key, spec.title, spec.icon, self)
        self._group.addButton(item)
        self._items[key_value(spec.key)] = item
        if getattr(spec, "footer", False):
            self.footer_layout.addWidget(item)
        else:
            self.items_layout.addWidget(item)
        item.clicked.connect(
            lambda _checked=False, k=key_value(spec.key): self._on_item_clicked(k)
        )
        return item

    def add_footer_item(self, spec) -> SANavigationItem:
        item = self._add_item(spec)
        return item

    def item(self, key) -> SANavigationItem | None:
        return self._items.get(key_value(key))

    def items(self) -> list[SANavigationItem]:
        return list(self._items.values())

    def set_item_available(self, key, available: bool) -> None:
        item = self._items.get(key_value(key))
        if item is None:
            return
        item.setEnabled(bool(available))
        item.refresh_appearance()

    def set_current(self, key) -> None:
        item = self._items.get(key_value(key))
        if item is None:
            return
        blocked = self._group.blockSignals(True)
        for other in self._items.values():
            other.setChecked(other is item)
        self._group.blockSignals(blocked)
        item._refresh_icon()
        item._repolish()

    def current_key(self) -> str | None:
        for key, item in self._items.items():
            if item.isChecked():
                return key
        return None

    # ---------- collapse ----------
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        self.brand_title.setVisible(not collapsed)
        self.brand_icon.setVisible(True)
        for item in self._items.values():
            item.set_collapsed(collapsed)
        if collapsed:
            self.collapse_btn.setToolTip("展开侧栏")
            self.collapse_btn.setAccessibleName("展开侧栏")
        else:
            self.collapse_btn.setToolTip("收起侧栏")
            self.collapse_btn.setAccessibleName("收起侧栏")
        self.collapsed_changed.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    # ---------- internal ----------
    def _on_item_clicked(self, key: str) -> None:
        self.set_current(key)
        self.page_requested.emit(key)

    def _apply_brand_icon(self) -> None:
        color = theme_manager().tokens()["accent"]
        self.brand_icon.setPixmap(
            _icons.pixmap(_icons.IconName.BOOK, size=20, color=color, filled=True)
        )

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_brand_icon()
