"""SAEmptyState：统一空状态（可选 icon + title + description + 可选 action）。

用于渐进替换 ``EmptyHint`` 与散落的 QLabel 文案；不绑定业务。
没有 icon 也可以使用。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..design import icons as _icons
from ..design import spacing as _spacing
from ..design.theme_manager import theme_manager


class SAEmptyState(QWidget):
    def __init__(
        self,
        title: str = "",
        description: str = "",
        icon_name=None,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("SAEmptyState")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            _spacing.XL, _spacing.XXL, _spacing.XL, _spacing.XXL
        )
        self._layout.setSpacing(_spacing.SM)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon_name
        self._icon_label.setVisible(icon_name is not None)
        self._layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("SAEmptyTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setWordWrap(True)
        self._layout.addWidget(self._title_label)

        self._desc_label = QLabel(description)
        self._desc_label.setObjectName("SAEmptyDescription")
        self._desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_label.setWordWrap(True)
        self._desc_label.setVisible(bool(description))
        self._layout.addWidget(self._desc_label)

        self._action_row = QHBoxLayout()
        self._action_row.setContentsMargins(0, _spacing.XS, 0, 0)
        self._action_row.addStretch()
        self._action: QWidget | None = None
        self._layout.addLayout(self._action_row)
        if action is not None:
            self.set_action(action)

        self._apply_icon()
        theme_manager().theme_changed.connect(self._on_theme_changed)

    # ---------- public ----------
    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def title(self) -> str:
        return self._title_label.text()

    def description(self) -> str:
        return self._desc_label.text()

    def set_description(self, description: str) -> None:
        self._desc_label.setText(description)
        self._desc_label.setVisible(bool(description))

    def set_action(self, action: QWidget | None) -> None:
        if self._action is not None:
            self._action_row.removeWidget(self._action)
            self._action.setParent(None)
        self._action = action
        if action is not None:
            self._action_row.insertWidget(self._action_row.count() - 1, action)

    def action(self) -> QWidget | None:
        return self._action

    # ---------- internal ----------
    def _apply_icon(self) -> None:
        if self._icon_name is None:
            return
        color = theme_manager().tokens()["text_tertiary"]
        self._icon_label.setPixmap(
            _icons.pixmap(self._icon_name, size=32, color=color)
        )

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_icon()
