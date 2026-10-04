"""内容重绘时的位置恢复；用户输入或关闭会使旧恢复请求失效。"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer


class ScrollPositionKeeper(QObject):
    def __init__(self, scroll):
        super().__init__(scroll)
        self.scroll = scroll
        self.bar = scroll.verticalScrollBar()
        self._epoch = 0
        self._handler = None
        for widget in (scroll, scroll.viewport(), self.bar):
            widget.installEventFilter(self)
        self.bar.sliderPressed.connect(self.cancel)

    def capture(self) -> int:
        value = self.bar.value()
        self.cancel()
        return value

    def cancel(self) -> None:
        self._epoch += 1
        if self._handler is not None:
            try:
                self.bar.rangeChanged.disconnect(self._handler)
            except (RuntimeError, TypeError):
                pass
            self._handler = None

    def restore(self, value: int) -> None:
        self.cancel()
        epoch = self._epoch
        target = max(0, value)

        def apply(*_args):
            if epoch != self._epoch:
                return
            # The layout must have a usable range before a nonzero restore.
            if target and self.bar.maximum() <= 0:
                return
            self.cancel()
            self.bar.setValue(min(target, self.bar.maximum()))

        self._handler = apply
        self.bar.rangeChanged.connect(apply)
        QTimer.singleShot(0, self, apply)

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Wheel, QEvent.Type.MouseButtonPress,
                    QEvent.Type.TouchBegin, QEvent.Type.Hide):
            self.cancel()
        elif kind == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
        ):
            self.cancel()
        return super().eventFilter(watched, event)
