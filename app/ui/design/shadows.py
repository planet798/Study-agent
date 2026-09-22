"""Shadow tokens。

Qt Widgets 的 ``QGraphicsDropShadowEffect`` 成本较高，UI-1 **默认不启用**：
卡片默认使用 ``surface`` + subtle border。这里只保留能力，供 future 弹层使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

NONE = "none"
LOW = "low"
MEDIUM = "medium"


@dataclass(frozen=True)
class ShadowSpec:
    blur: int
    offset_y: int
    color: str

    def rgba(self) -> QColor:
        c = QColor(self.color)
        return c


SPECS: dict[str, ShadowSpec | None] = {
    NONE: None,
    LOW: ShadowSpec(blur=8, offset_y=2, color="rgba(0, 0, 0, 0.14)"),
    MEDIUM: ShadowSpec(blur=18, offset_y=6, color="rgba(0, 0, 0, 0.20)"),
}


def apply_shadow(widget: QWidget, level: str = LOW) -> None:
    """给 widget 应用指定层级阴影；``NONE`` 时移除。"""
    spec = SPECS.get(level)
    if spec is None:
        widget.setGraphicsEffect(None)
        return
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(spec.blur)
    effect.setXOffset(0)
    effect.setYOffset(spec.offset_y)
    effect.setColor(spec.rgba())
    widget.setGraphicsEffect(effect)
