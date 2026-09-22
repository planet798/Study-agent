"""SASectionHeader：统一 section 标题（title + 可选 subtitle + 可选 trailing）。

不绑定任何业务语义，供 Today / Routes / Practice / Settings sections 复用。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..design import spacing as _spacing


class SASectionHeader(QWidget):
    def __init__(
        self,
        title: str = "",
        subtitle: str | None = None,
        trailing: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("SASectionTitle")
        self._subtitle_label = QLabel(subtitle or "")
        self._subtitle_label.setObjectName("SASectionSubtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setVisible(bool(subtitle))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(_spacing.XS)
        text.addWidget(self._title_label)
        text.addWidget(self._subtitle_label)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(_spacing.SM)
        self._row.addLayout(text)
        self._row.addStretch()

        self._trailing: QWidget | None = None
        if trailing is not None:
            self.set_trailing(trailing)

    # ---------- public ----------
    def title(self) -> str:
        return self._title_label.text()

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_subtitle(self, subtitle: str | None) -> None:
        self._subtitle_label.setText(subtitle or "")
        self._subtitle_label.setVisible(bool(subtitle))

    def trailing(self) -> QWidget | None:
        return self._trailing

    def set_trailing(self, widget: QWidget | None) -> None:
        if self._trailing is not None:
            self._row.removeWidget(self._trailing)
            self._trailing.setParent(None)
        self._trailing = widget
        if widget is not None:
            self._row.addWidget(widget)
