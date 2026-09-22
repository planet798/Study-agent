"""SAPageHeader：Workspace 统一页面标题区。

title + 可选 subtitle + 可选 leading icon + 可选 trailing actions。
不绑定任何业务语义；由 AppShell / MainWindow 在页面切换时更新。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..design import icons as _icons
from ..design import spacing as _spacing
from ..design.theme_manager import theme_manager


class SAPageHeader(QWidget):
    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("SAPageHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._leading = QLabel()
        self._leading.setObjectName("SAPageHeaderIcon")
        self._leading.setFixedSize(28, 28)
        self._leading.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("SAPageTitle")
        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setObjectName("SAPageSubtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setVisible(bool(subtitle))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(self._title_label)
        text.addWidget(self._subtitle_label)

        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(_spacing.MD)
        self._row.addWidget(self._leading)
        self._row.addLayout(text)
        self._row.addStretch()

        self._trailing_row = QHBoxLayout()
        self._trailing_row.setContentsMargins(0, 0, 0, 0)
        self._trailing_row.setSpacing(_spacing.SM)
        self._row.addLayout(self._trailing_row)

        divider = QFrame()
        divider.setObjectName("SAPageHeaderDivider")
        divider.setFixedHeight(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            _spacing.XXL, _spacing.LG, _spacing.XXL, 0
        )
        root.setSpacing(_spacing.MD)
        root.addLayout(self._row)
        root.addWidget(divider)

        self._icon_name = None
        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- public ----------
    def title(self) -> str:
        return self._title_label.text()

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def subtitle(self) -> str:
        return self._subtitle_label.text()

    def set_subtitle(self, subtitle: str | None) -> None:
        self._subtitle_label.setText(subtitle or "")
        self._subtitle_label.setVisible(bool(subtitle))

    def subtitle_label(self) -> QLabel:
        """暴露 subtitle QLabel（Today 的日期仍写入这个 label）。"""
        return self._subtitle_label

    def leading_label(self) -> QLabel:
        return self._leading

    def set_icon(self, icon_name) -> None:
        self._icon_name = icon_name
        self._apply_icon()

    def add_trailing(self, widget: QWidget) -> QWidget:
        self._trailing_row.addWidget(widget)
        return widget

    # ---------- internal ----------
    def _apply_icon(self) -> None:
        if self._icon_name is None:
            self._leading.clear()
            self._leading.setVisible(False)
            return
        self._leading.setVisible(True)
        color = theme_manager().tokens()["accent"]
        self._leading.setPixmap(
            _icons.pixmap(self._icon_name, size=24, color=color)
        )

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_icon()
