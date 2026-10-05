"""紧凑更新界面；模型、凭据、数据库不可用时仍可检查版本。"""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from app.updates.jobs import supported
from app.updates.models import RELEASES_URL, UpdateState
from app.version import VERSION
from .app_update_controller import AppUpdateController
from .components.button import SAButton
from .components.section_header import SASectionHeader


class AppUpdatePanel(QWidget):
    install_requested = Signal()

    def __init__(self, parent=None, *, controller=None, can_install=None):
        super().__init__(parent)
        self.controller = controller or AppUpdateController(parent=self)
        self.can_install = supported() if can_install is None else can_install
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(SASectionHeader("应用更新"))
        row.addWidget(QLabel(f"当前版本 {VERSION}"))
        row.addStretch()
        self.check_btn = SAButton("检查更新", variant="secondary")
        self.check_btn.clicked.connect(self.controller.check)
        row.addWidget(self.check_btn)
        self.download_btn = SAButton("下载更新", variant="secondary")
        self.download_btn.clicked.connect(self.controller.download)
        row.addWidget(self.download_btn)
        self.install_btn = SAButton("安装并重启", variant="primary")
        self.install_btn.clicked.connect(self.install_requested.emit)
        row.addWidget(self.install_btn)
        self.cancel_btn = SAButton("取消下载", variant="subtle")
        self.cancel_btn.clicked.connect(self.controller.cancel)
        row.addWidget(self.cancel_btn)
        self.release_btn = SAButton("打开发布页", variant="subtle")
        self.release_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        row.addWidget(self.release_btn)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.source_hint = QLabel("源码运行仅检查版本；请通过发布页下载安装版。")
        self.source_hint.setVisible(not self.can_install)
        layout.addWidget(self.source_hint)
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        self.notes_btn = SAButton("查看更新说明", variant="subtle")
        self.notes_btn.setCheckable(True)
        layout.addWidget(self.notes_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(100)
        self.notes.setAccessibleName("更新说明")
        self.notes.hide()
        layout.addWidget(self.notes)
        self.notes_btn.toggled.connect(self.notes.setVisible)
        self.destroyed.connect(self.controller.stop)
        self.controller.changed.connect(self.refresh)
        self.controller.progress.connect(self._progress)
        self.refresh()

    def _progress(self, current, total):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(current * 100 / total) if total else 0)
        self.progress_bar.setFormat(f"%p% · {current / 1048576:.1f} / {total / 1048576:.1f} MB")

    def refresh(self):
        controller = self.controller
        busy = controller.state in {UpdateState.CHECKING, UpdateState.DOWNLOADING,
                                    UpdateState.VERIFYING, UpdateState.PREPARING}
        downloading = controller.state in {UpdateState.DOWNLOADING, UpdateState.VERIFYING}
        self.status.setText(controller.message)
        self.check_btn.setEnabled(not busy)
        self.check_btn.setText("重新检查" if controller.state == UpdateState.FAILED else "检查更新")
        self.download_btn.setVisible(controller.release is not None and controller.downloaded is None)
        self.download_btn.setEnabled(self.can_install and not busy)
        self.download_btn.setText("重新下载" if controller.state == UpdateState.FAILED else "下载更新")
        self.install_btn.setVisible(controller.downloaded is not None)
        self.install_btn.setEnabled(self.can_install and not busy)
        self.cancel_btn.setVisible(downloading)
        self.progress_bar.setVisible(downloading)
        if controller.state == UpdateState.VERIFYING:
            self.progress_bar.setRange(0, 0)
        elif controller.state == UpdateState.DOWNLOADING:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        self.notes_btn.setVisible(controller.release is not None)
        if controller.release:
            release = controller.release
            self.notes.setPlainText(
                f"版本 {release.version} · {release.published_at[:10]} · {release.size / 1048576:.1f} MB\n\n"
                + (release.notes or "此版本暂无更新说明。"))
        else:
            self.notes_btn.setChecked(False)
            self.notes.hide()
