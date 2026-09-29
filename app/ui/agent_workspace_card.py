"""Task-scoped filesystem binding display; emits only user-initiated actions."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QWidget

from .components.button import SAButton
from .components.card import SACard
from .components.tag import SATag
from .design import spacing


class AgentWorkspaceCard(SACard):
    managed_requested = Signal(int)
    local_requested = Signal(int)
    open_requested = Signal(int)
    clear_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(variant="default", parent=parent, padding=spacing.LG, spacing=spacing.SM)
        self.setObjectName("AgentWorkspaceCard")
        self._task_id: int | None = None
        self._busy = False
        self._kind = "none"
        self._available = False

        title = QLabel("工作区")
        title.setObjectName("AgentWorkspaceTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        self.body_layout.addWidget(title)

        self.state_label = QLabel()
        self.state_label.setObjectName("AgentWorkspaceState")
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        self.state_label.setWordWrap(True)
        self.body_layout.addWidget(self.state_label)

        self.path_label = QLabel()
        self.path_label.setObjectName("AgentWorkspacePath")
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumWidth(0)
        self.body_layout.addWidget(self.path_label)

        self.badge = SATag("", "neutral")
        self.body_layout.addWidget(self.badge)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("AgentWorkspaceHint")
        self.hint_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hint_label.setWordWrap(True)
        self.body_layout.addWidget(self.hint_label)

        from PySide6.QtWidgets import QHBoxLayout
        row = QHBoxLayout()
        row.setSpacing(spacing.SM)
        self.managed_button = SAButton("使用托管工作区", variant="secondary", size="small")
        self.managed_button.setObjectName("AgentWorkspaceManaged")
        self.managed_button.clicked.connect(lambda: self._emit(self.managed_requested))
        self.local_button = SAButton("选择本地项目", variant="secondary", size="small")
        self.local_button.setObjectName("AgentWorkspaceLocal")
        self.local_button.clicked.connect(lambda: self._emit(self.local_requested))
        self.open_button = SAButton("打开文件夹", variant="secondary", size="small")
        self.open_button.setObjectName("AgentWorkspaceOpen")
        self.open_button.clicked.connect(lambda: self._emit(self.open_requested))
        self.replace_button = SAButton("更换", variant="secondary", size="small")
        self.replace_button.setObjectName("AgentWorkspaceReplace")
        menu = QMenu(self.replace_button)
        menu.addAction("使用托管工作区", lambda: self._emit(self.managed_requested))
        menu.addAction("选择本地项目", lambda: self._emit(self.local_requested))
        self.replace_button.setMenu(menu)
        self.reselect_button = SAButton("重新选择", variant="secondary", size="small")
        self.reselect_button.setObjectName("AgentWorkspaceReselect")
        self.reselect_button.clicked.connect(lambda: self._emit(self.local_requested))
        self.clear_button = SAButton("解除绑定", variant="subtle", size="small")
        self.clear_button.setObjectName("AgentWorkspaceClear")
        self.clear_button.clicked.connect(lambda: self._emit(self.clear_requested))
        self._buttons = (self.managed_button, self.local_button, self.open_button,
                         self.replace_button, self.reselect_button, self.clear_button)
        for button in self._buttons:
            row.addWidget(button)
        row.addStretch(1)
        self.body_layout.addLayout(row)
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
        self._task_id = view.task_id if view is not None else None
        self._kind = view.kind if view is not None else "none"
        self._available = bool(view.available) if view is not None else False
        kind = self._kind
        if kind == "none":
            self.state_label.setText("尚未为这个学习任务设置文件工作区。")
            self.hint_label.setText("托管工作区适合学习笔记、临时代码和实验输出；本地项目在当前版本中仅提供只读访问。")
        elif kind == "local" and not self._available:
            self.state_label.setText(view.label)
            self.hint_label.setText("工作区目录不存在，请重新选择。")
        else:
            self.state_label.setText(view.label)
            self.hint_label.setText("")
        self.path_label.setText(view.display_path if view is not None and kind != "none" else "")
        self.path_label.setVisible(kind != "none")
        self.badge.setText("可读写" if kind == "managed" else "本地项目 · 只读")
        self.badge.setVisible(kind != "none" and self._available)
        self.hint_label.setVisible(bool(self.hint_label.text()))
        self.managed_button.setVisible(kind == "none")
        self.local_button.setVisible(kind == "none")
        self.open_button.setVisible(kind == "managed" or (kind == "local" and self._available))
        self.replace_button.setVisible(kind != "none" and self._available)
        self.reselect_button.setVisible(kind == "local" and not self._available)
        self.clear_button.setVisible(kind != "none")
        self._update_enabled()

    def closeEvent(self, event):  # noqa: N802 - Qt API
        # A popup is a top-level Qt window even though its QObject parent is
        # the button. Never leave one open across a page/test teardown.
        menu = self.replace_button.menu()
        if menu is not None:
            menu.close()
        super().closeEvent(event)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_enabled()

    def _update_enabled(self) -> None:
        for button in self._buttons:
            button.setEnabled(self._task_id is not None and not self._busy)
