"""SACard：统一卡片容器（surface + border + radius + padding）。

默认**不加阴影**（shadows.MEDIUM 仅保留能力），避免 Qt Widgets 阴影开销。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ..design import spacing as _spacing

VARIANTS: tuple[str, ...] = ("default", "interactive", "selected")


class SACard(QFrame):
    """卡片容器。通过 :meth:`add_widget` / :meth:`add_layout` 填充内容。"""

    def __init__(
        self,
        variant: str = "default",
        parent: QWidget | None = None,
        padding: int = _spacing.MD,
        spacing: int = _spacing.SM,
    ):
        super().__init__(parent)
        if variant not in VARIANTS:
            raise ValueError(f"unknown SACard variant: {variant!r}")
        self.setObjectName("SACard")
        self._variant = variant
        self.setProperty("saCardVariant", variant)

        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(padding, padding, padding, padding)
        self.body_layout.setSpacing(spacing)

    # ---------- public ----------
    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown SACard variant: {variant!r}")
        self._variant = variant
        self.setProperty("saCardVariant", variant)
        self.style().unpolish(self)
        self.style().polish(self)

    def add_widget(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)

    def add_stretch(self) -> None:
        self.body_layout.addStretch()
