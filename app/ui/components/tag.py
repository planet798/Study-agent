"""SATag：metadata 标签（Route / Activity / Source / Status 等）。

解决旧 ``ReviewTag`` 语义过载。克制使用：只用于 metadata，不做满页面 pill。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

VARIANTS: tuple[str, ...] = (
    "neutral",
    "accent",
    "success",
    "warning",
    "danger",
    "info",
)


class SATag(QLabel):
    def __init__(self, text: str = "", variant: str = "neutral", parent=None):
        super().__init__(text, parent)
        if variant not in VARIANTS:
            raise ValueError(f"unknown SATag variant: {variant!r}")
        self.setObjectName("SATag")
        self._variant = variant
        self.setProperty("saTagVariant", variant)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown SATag variant: {variant!r}")
        self._variant = variant
        self.setProperty("saTagVariant", variant)
        self.style().unpolish(self)
        self.style().polish(self)
