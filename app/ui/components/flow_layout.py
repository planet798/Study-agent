"""FlowLayout：按可用宽度自动换行的布局（Qt 官方示例的 PySide6 版本）。

用于 metadata tags / capability chips：窄 viewport 下换行而不是横向溢出，
不依赖任何业务语义。
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 8, v_spacing: int = 6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(QMargins(margin, margin, margin, margin))
        self._h_spacing = int(h_spacing)
        self._v_spacing = int(v_spacing)
        self._items: list = []

    # ---------- QLayout ----------
    def addItem(self, item):  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    # ---------- internal ----------
    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class FlowWidget(QWidget):
    """QWidget 包装，使 FlowLayout 的 heightForWidth 能被父布局采纳。"""

    def __init__(self, parent=None, *, h_spacing: int = 8, v_spacing: int = 6):
        super().__init__(parent)
        self.setObjectName("SAFlowWidget")
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.flow = FlowLayout(self, margin=0, h_spacing=h_spacing, v_spacing=v_spacing)

    def add_widget(self, widget: QWidget) -> QWidget:
        self.flow.addWidget(widget)
        return widget

    def count(self) -> int:
        return self.flow.count()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self.flow.heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.flow.sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.flow.minimumSize()
