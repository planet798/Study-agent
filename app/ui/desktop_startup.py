"""安装版首次导入和显式数据库升级，耗时工作不阻塞 GUI。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout

from app.runtime_paths import data_dir
from app.services.desktop_data_service import DesktopDataError, import_legacy_data, upgrade_database


class _Operation(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation())
        except DesktopDataError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            # 第三方异常可能含配置/凭据正文，仅展示异常类型。
            self.failed.emit(f"操作失败（{type(exc).__name__}）。原数据已保留，请关闭旧实例后重试。")


class _Progress(QDialog):
    def __init__(self, operation, label):
        super().__init__()
        self.setWindowTitle("Study Agent · 数据准备")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.error = None
        self.result = None
        self.worker = _Operation(operation, self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.accept)

    def _completed(self, result):
        self.result = result

    def _failed(self, message):
        self.error = message

    def reject(self):
        if not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def _run(operation, label) -> bool:
    dialog = _Progress(operation, label)
    dialog.worker.start()
    dialog.exec()
    dialog.worker.wait()
    if dialog.error:
        QMessageBox.critical(None, "数据准备失败", dialog.error)
        return False
    return True


def prepare_desktop_data(db_path: Path, gate_status) -> bool:
    if not db_path.exists():
        welcome = QMessageBox()
        welcome.setWindowTitle("欢迎使用 Study Agent")
        welcome.setText("请选择全新开始，或导入源码版已有的学习数据。")
        welcome.setInformativeText("导入前请退出旧版程序。旧数据保留；同一 Windows 用户的登录凭据继续可用。")
        fresh = welcome.addButton("全新开始", QMessageBox.ButtonRole.AcceptRole)
        legacy = welcome.addButton("导入旧数据", QMessageBox.ButtonRole.ActionRole)
        welcome.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        welcome.exec()
        if welcome.clickedButton() == legacy:
            source = QFileDialog.getExistingDirectory(None, "选择旧版 data 目录")
            if not source:
                return False
            if not _run(lambda: import_legacy_data(Path(source), data_dir()), "正在导入和验证学习数据，请勿关闭程序…"):
                return False
        elif welcome.clickedButton() != fresh:
            return False
    gate = gate_status(db_path)
    if not gate.get("blocked"):
        return True
    if gate.get("reason") != "old_schema":
        QMessageBox.critical(None, "数据库无法启动", "数据库不可读或来自更高版本。请使用匹配的程序版本或恢复备份。")
        return False
    choice = QMessageBox.question(
        None, "升级学习数据库",
        "数据库需要升级。将先备份，再在副本中预演、迁移并校验历史记录。\n"
        "成功后才替换原库，失败保留原数据。备份位于数据目录的 backups 子目录。\n\n是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return choice == QMessageBox.StandardButton.Yes and _run(
        lambda: upgrade_database(db_path), "正在备份和校验数据库升级，请勿关闭程序…",
    )
