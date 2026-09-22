"""SAProgressBar：连续度量进度条（curriculum completion / Mastery 百分比）。

不要用于离散 Capability 等级；Capability 用 SATag / SAStatusBadge + Lx 文本。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from ..design import spacing as _spacing


class SAProgressBar(QWidget):
    def __init__(
        self,
        value: int = 0,
        label: str = "",
        show_value: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._show_value = show_value
        self._bar = QProgressBar()
        self._bar.setObjectName("SAProgressBar")
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setAccessibleName(label or "进度")

        self._label = QLabel(label)
        self._label.setObjectName("SAProgressLabel")
        self._value = QLabel("")
        self._value.setObjectName("SAProgressValue")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(_spacing.SM)
        header.addWidget(self._label)
        header.addStretch()
        header.addWidget(self._value)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_spacing.XS)
        root.addLayout(header)
        root.addWidget(self._bar)

        self.set_value(value)
        self._update_value_label()

    # ---------- public ----------
    def set_value(self, value) -> None:
        try:
            pct = int(round(float(value)))
        except (TypeError, ValueError):
            pct = 0
        pct = max(0, min(100, pct))
        self._bar.setValue(pct)
        self._bar.setAccessibleDescription(f"{pct}%")
        self._update_value_label()

    def value(self) -> int:
        return self._bar.value()

    def set_label(self, label: str) -> None:
        self._label.setText(label)
        self._bar.setAccessibleName(label or "进度")

    def label(self) -> str:
        return self._label.text()

    def bar(self) -> QProgressBar:
        return self._bar

    # ---------- internal ----------
    def _update_value_label(self) -> None:
        if self._show_value:
            self._value.setText(f"{self._bar.value()}%")
            self._value.setVisible(True)
        else:
            self._value.setVisible(False)
