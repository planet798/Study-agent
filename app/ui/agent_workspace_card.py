"""Compact task-scoped Workspace selector. Binding/security remain service-owned."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QWidget


class AgentWorkspaceCard(QWidget):
    managed_requested = Signal(int)
    local_requested = Signal(int)
    open_requested = Signal(int)
    clear_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AgentWorkspaceControl")
        self._task_id: int | None = None
        self._busy = False
        self._kind = "none"
        self._available = False
        self._path = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.selector = QPushButton("工作区 ▾")
        self.selector.setObjectName("AgentWorkspaceSelector")
        self.selector.setMinimumWidth(0)
        self.menu = QMenu(self.selector)
        self.selector.setMenu(self.menu)
        row.addWidget(self.selector)
        self.badge = QLabel("")
        self.badge.setObjectName("AgentWorkspaceBadge")
        row.addWidget(self.badge)
        row.addStretch(1)
        self.set_view(None)

    @property
    def workspace_kind(self) -> str:
        return self._kind

    @property
    def available(self) -> bool:
        return self._available

    def _emit(self, signal) -> None:
        if self._task_id is not None and not self._busy:
            signal.emit(self._task_id)

    def set_view(self, view) -> None:
        self.menu.close()
        self._task_id = view.task_id if view is not None else None
        self._kind = view.kind if view is not None else "none"
        self._available = bool(view.available) if view is not None else False
        self._path = view.display_path if view is not None and self._kind != "none" else ""
        label = (view.label if view is not None and self._kind != "none" else "选择工作区")
        # The button never claims the full path's size hint, even with large fonts.
        label = self.selector.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 180)
        self.selector.setText(f"{label} ▾")
        self.selector.setToolTip(self._path or "选择任务工作区")
        self.badge.setText(("可读写" if self._kind == "managed" else "只读")
                           if self._available else "不可用")
        self.badge.setToolTip("工作区绑定模式，不代表本轮工具权限")
        self.badge.setProperty("unavailable", not self._available)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        self.badge.setVisible(self._kind != "none")
        self.menu.clear()
        if self._path:
            detail = self.menu.addAction(self._path)
            detail.setEnabled(False)
            self.menu.addAction("复制路径", lambda: QApplication.clipboard().setText(self._path))
            self.menu.addSeparator()
        if self._kind != "none" and self._available:
            self.menu.addAction("打开文件夹", lambda: self._emit(self.open_requested))
        self.menu.addAction("使用托管工作区", lambda: self._emit(self.managed_requested))
        self.menu.addAction("选择本地项目" if self._kind != "local" else "重新选择本地项目",
                            lambda: self._emit(self.local_requested))
        if self._kind != "none":
            self.menu.addSeparator()
            self.menu.addAction("解除绑定", lambda: self._emit(self.clear_requested))
        self._update_enabled()

    def closeEvent(self, event):  # noqa: N802
        self.menu.close()
        super().closeEvent(event)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_enabled()

    def _update_enabled(self) -> None:
        self.selector.setEnabled(self._task_id is not None and not self._busy)
