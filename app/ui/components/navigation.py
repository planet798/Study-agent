"""SANavigationItem / SANavigationSidebar（暖中性色左侧导航）。

- ``SANavigationItem`` 继承 ``QPushButton``，支持 click / text / isEnabled /
  checkable / keyboard（Tab + Enter/Space）；全局 QSS 负责中性选中背景和 Filled icon。
- ``SANavigationSidebar`` 管理 items、collapsed 状态、可用性降级与 page_requested 信号。
  不包含任何业务逻辑。
"""

from __future__ import annotations

from enum import Enum
from weakref import ref

import shiboken6

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
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


def _safe_theme_disconnect(manager, callback) -> None:
    try:
        manager.theme_changed.disconnect(callback)
    except (RuntimeError, TypeError, SystemError):
        pass


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
        manager = theme_manager()
        weak_item = ref(self)

        def on_theme_changed(theme):
            item = weak_item()
            if item is not None and shiboken6.isValid(item):
                item._on_theme_changed(theme)

        manager.theme_changed.connect(on_theme_changed)
        self.destroyed.connect(lambda *_: _safe_theme_disconnect(manager, on_theme_changed))

    # ---------- public ----------
    def key(self) -> str:
        return self._key

    def label(self) -> str:
        return self._label

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.setProperty("collapsed", "true" if self._collapsed else "false")
        # collapsed 只显示 icon；展开恢复文本。
        label = (self.fontMetrics().elidedText(self._label, Qt.TextElideMode.ElideRight,
                                               EXPANDED_WIDTH - 126)
                 if self._key.startswith("session:") else self._label)
        self.setText("" if self._collapsed else label)
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
        return t["text_primary"] if self.isChecked() else t["text_secondary"]

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
        if shiboken6.isValid(self):
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
    session_requested = Signal(int)
    session_rename_requested = Signal(int)
    session_reset_title_requested = Signal(int)
    session_pin_requested = Signal(int)
    session_unpin_requested = Signal(int)
    session_archive_requested = Signal(int)
    archived_sessions_requested = Signal()
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

        # Pins are unbounded: keep the dynamic section scrollable while static
        # navigation and the Settings footer remain in place.
        sessions_scroll = QScrollArea(self)
        sessions_scroll.setObjectName("SASessionsScroll")
        sessions_scroll.setWidgetResizable(True)
        sessions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sessions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sessions_body = QWidget(sessions_scroll)
        sessions_body.setObjectName("SASessionsBody")
        sessions_body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sessions_root = QVBoxLayout(sessions_body)
        sessions_root.setContentsMargins(0, 0, 0, 0)
        sessions_root.setSpacing(_spacing.XS)
        sessions_scroll.setWidget(sessions_body)
        root.addWidget(sessions_scroll, 1)
        self.sessions_heading = QLabel("学习会话")
        self.sessions_heading.setObjectName("SASessionsHeading")
        self.sessions_heading.setContentsMargins(_spacing.SM, _spacing.MD, 0, _spacing.XS)
        sessions_root.addWidget(self.sessions_heading)
        self.sessions_layout = QVBoxLayout()
        self.sessions_layout.setContentsMargins(0, 0, 0, 0)
        self.sessions_layout.setSpacing(_spacing.XS)
        sessions_root.addLayout(self.sessions_layout)
        self._session_items: dict[int, SANavigationItem] = {}
        self._session_rows: dict[int, QWidget] = {}
        self.archived_sessions_btn = QPushButton("已归档会话", self)
        self.archived_sessions_btn.setToolTip("已归档会话")
        self.archived_sessions_btn.setAccessibleName("已归档会话")
        self.archived_sessions_btn.setIcon(_icons.icon(_icons.IconName.BOOK, size=20))
        self.archived_sessions_btn.clicked.connect(self.archived_sessions_requested)
        sessions_root.addWidget(self.archived_sessions_btn)
        self.archived_sessions_btn.hide()
        self.sessions_heading.hide()

        sessions_root.addStretch()

        # ---- divider + settings ----
        divider = QFrame()
        divider.setObjectName("SADivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)
        divider.setFixedHeight(1)
        root.addWidget(divider)
        root.addLayout(self.footer_layout)

        self.setFixedWidth(EXPANDED_WIDTH)
        self._apply_brand_icon()
        manager = theme_manager()
        weak_sidebar = ref(self)

        def on_theme_changed(theme):
            sidebar = weak_sidebar()
            if sidebar is not None and shiboken6.isValid(sidebar):
                sidebar._on_theme_changed(theme)

        manager.theme_changed.connect(on_theme_changed)
        self.destroyed.connect(lambda *_: _safe_theme_disconnect(manager, on_theme_changed))

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

    def set_sessions(self, sessions: list[dict], current_id: int | None = None,
                     busy: bool = False, has_archived: bool = False) -> None:
        """Render visual records with service-resolved visible_title, without a cap."""
        previous_key = self.current_key()
        for item in self._session_items.values():
            self._group.removeButton(item)
            self._items.pop(item.key(), None)
            row = self._session_rows[int(item.key().split(":")[1])]
            self.sessions_layout.removeWidget(row)
            row.hide()
            item.management_menu.close()
            row.deleteLater()
        self._session_items.clear()
        self._session_rows.clear()
        self.archived_sessions_btn.setVisible(has_archived)
        self.archived_sessions_btn.setEnabled(not busy)
        self.sessions_heading.setVisible(bool(sessions or has_archived) and not self._collapsed)
        for session in sessions:
            sid = int(session["id"])
            title = session["visible_title"]
            item = SANavigationItem(f"session:{sid}", title, _icons.IconName.BOOK, self)
            # Reuse Fluent navigation selected/hover/disabled QSS.
            item.setMinimumWidth(0)
            item.setToolTip(title)
            item.setText(item.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                                       EXPANDED_WIDTH - 126))
            item.setEnabled(not busy or sid == current_id)
            item.set_collapsed(self._collapsed)
            self._group.addButton(item)
            self._items[item.key()] = item
            self._session_items[sid] = item
            row = QWidget(self)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(item, 1)
            more = SAIconButton(_icons.IconName.MORE, icon_size=16,
                                tooltip="会话操作", accessible_name=f"{title}：会话操作")
            more.setFixedSize(28, 32)
            more.setEnabled(not busy)
            more.setVisible(not self._collapsed)
            layout.addWidget(more)
            menu = QMenu(more)
            actions = [("重命名", self.session_rename_requested)]
            if session.get("has_title_override"):
                actions.append(("恢复原始名称", self.session_reset_title_requested))
            actions.append(("取消固定" if session.get("pinned") else "固定",
                            self.session_unpin_requested if session.get("pinned")
                            else self.session_pin_requested))
            actions.append(("归档", self.session_archive_requested))
            for text, signal in actions:
                action = menu.addAction(text)
                action.setEnabled(not busy)
                action.triggered.connect(lambda _checked=False, id=sid, sig=signal: sig.emit(id))
            more.clicked.connect(lambda _checked=False, m=menu, b=more:
                                 m.popup(b.mapToGlobal(b.rect().bottomLeft())))
            item.management_button = more
            item.management_menu = menu
            if session.get("pinned"):
                # No PIN asset exists; a subtle visible marker plus menu state.
                marker = QLabel("固定", row)
                marker.setObjectName("SASessionPinMarker")
                marker.setToolTip("已固定")
                marker.setVisible(not self._collapsed)
                layout.insertWidget(1, marker)
                item.pin_marker = marker
            self._session_rows[sid] = row
            self.sessions_layout.addWidget(row)
            item.clicked.connect(lambda _checked=False, id=sid: self._on_session_clicked(id))
        if current_id in self._session_items:
            self.set_current(f"session:{current_id}")
        elif previous_key in self._items and not previous_key.startswith("session:"):
            self.set_current(previous_key)

    def session_item(self, session_id: int) -> SANavigationItem | None:
        return self._session_items.get(int(session_id))

    def _on_session_clicked(self, session_id: int) -> None:
        self.set_current(f"session:{session_id}")
        self.session_requested.emit(session_id)

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
        for item in self._session_items.values():
            item.management_button.setVisible(not collapsed)
            if hasattr(item, "pin_marker"):
                item.pin_marker.setVisible(not collapsed)
        self.archived_sessions_btn.setText("" if collapsed else "已归档会话")
        self.sessions_heading.setVisible(
            bool(self._session_items or not self.archived_sessions_btn.isHidden()) and not collapsed
        )
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
        color = theme_manager().tokens()["text_secondary"]
        self.brand_icon.setPixmap(
            _icons.pixmap(_icons.IconName.BOOK, size=20, color=color, filled=True)
        )

    def _on_theme_changed(self, _theme: str) -> None:
        if shiboken6.isValid(self) and shiboken6.isValid(self.brand_icon):
            self._apply_brand_icon()
