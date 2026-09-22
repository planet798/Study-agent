"""SAInfoBanner：通用行内提示 / contextual banner。

简单 variant（info / warning / danger / success），带可选 icon / title / description /
action。不做 Notification framework。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..design import icons as _icons
from ..design import spacing as _spacing
from ..design.theme_manager import theme_manager

VARIANTS: tuple[str, ...] = ("info", "warning", "danger", "success")
_VARIANT_ICON = {
    "info": _icons.IconName.INFO,
    "warning": _icons.IconName.WARNING,
    "danger": _icons.IconName.WARNING,
    "success": _icons.IconName.CHECK,
}


class SAInfoBanner(QFrame):
    def __init__(
        self,
        title: str = "",
        description: str = "",
        variant: str = "info",
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        if variant not in VARIANTS:
            raise ValueError(f"unknown SAInfoBanner variant: {variant!r}")
        self.setObjectName("SAInfoBanner")
        self._variant = variant
        self.setProperty("saBannerVariant", variant)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(18, 18)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("SAInfoBannerTitle")
        self._title_label.setVisible(bool(title))
        self._desc_label = QLabel(description)
        self._desc_label.setObjectName("SAInfoBannerDescription")
        self._desc_label.setWordWrap(True)
        self._desc_label.setVisible(bool(description))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(self._title_label)
        text.addWidget(self._desc_label)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(
            _spacing.MD, _spacing.SM, _spacing.MD, _spacing.SM
        )
        self._row.setSpacing(_spacing.SM)
        self._row.addWidget(self._icon_label)
        self._row.addLayout(text, stretch=1)
        self._action: QWidget | None = None
        if action is not None:
            self.set_action(action)

        theme_manager().theme_changed.connect(self._on_theme_changed)
        self._apply_icon()

    # ---------- public ----------
    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown SAInfoBanner variant: {variant!r}")
        self._variant = variant
        self.setProperty("saBannerVariant", variant)
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_icon()

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)
        self._title_label.setVisible(bool(title))

    def title(self) -> str:
        return self._title_label.text()

    def set_description(self, description: str) -> None:
        self._desc_label.setText(description)
        self._desc_label.setVisible(bool(description))

    def description(self) -> str:
        return self._desc_label.text()

    def title_label(self) -> QLabel:
        """兼容：把 banner 标题 label 暴露给外部（如 planner_status_label）。"""
        return self._title_label

    def description_label(self) -> QLabel:
        return self._desc_label

    def set_action(self, action: QWidget | None) -> None:
        if self._action is not None:
            self._row.removeWidget(self._action)
            self._action.setParent(None)
        self._action = action
        if action is not None:
            self._row.addWidget(action)

    def action(self) -> QWidget | None:
        return self._action

    # ---------- internal ----------
    def _apply_icon(self) -> None:
        color = theme_manager().tokens()[self._variant]
        self._icon_label.setPixmap(
            _icons.pixmap(_VARIANT_ICON[self._variant], size=16, color=color)
        )

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_icon()
