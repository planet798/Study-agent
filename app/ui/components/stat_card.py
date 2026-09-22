"""SAStatCard：低调的指标卡（label / value / optional helper / optional icon）。

用于 Today Summary 等。不做巨大数字 / 彩色 KPI / 渐变。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..design import icons as _icons
from ..design import spacing as _spacing
from ..design import typography as _type
from ..design.theme_manager import theme_manager


class SAStatCard(QFrame):
    def __init__(
        self,
        label: str = "",
        value: str = "",
        helper: str = "",
        icon_name=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("SAStatCard")
        self._icon_name = icon_name
        self._value_label = QLabel(value)
        self._value_label.setObjectName("SAStatValue")
        self._value_label.setFont(_type.font_for(_type.TITLE))
        self._label_label = QLabel(label)
        self._label_label.setObjectName("SAStatLabel")
        self._helper_label = QLabel(helper)
        self._helper_label.setObjectName("SAStatHelper")
        self._helper_label.setVisible(bool(helper))

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(20, 20)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setVisible(icon_name is not None)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(_spacing.SM)
        top.addWidget(self._icon_label)
        top.addWidget(self._value_label)
        top.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(
            _spacing.MD, _spacing.MD, _spacing.MD, _spacing.MD
        )
        root.setSpacing(_spacing.XS)
        root.addLayout(top)
        root.addWidget(self._label_label)
        root.addWidget(self._helper_label)

        theme_manager().theme_changed.connect(self._on_theme_changed)
        self._apply_icon()

    # ---------- public ----------
    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def value(self) -> str:
        return self._value_label.text()

    def set_label(self, label: str) -> None:
        self._label_label.setText(label)

    def label(self) -> str:
        return self._label_label.text()

    def set_helper(self, helper: str | None) -> None:
        self._helper_label.setText(helper or "")
        self._helper_label.setVisible(bool(helper))

    def helper(self) -> str:
        return self._helper_label.text()

    # ---------- internal ----------
    def _apply_icon(self) -> None:
        if self._icon_name is None:
            return
        self._icon_label.setPixmap(
            _icons.pixmap(
                self._icon_name,
                size=18,
                color=theme_manager().tokens()["text_secondary"],
            )
        )

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_icon()
