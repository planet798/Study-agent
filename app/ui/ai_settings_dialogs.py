"""AI 设置中心对话框：API Profile 增删改 + 连接测试 + Prompt 预览。

安全要求：
- API Key 输入框默认 ``QLineEdit.Password``，默认绝不明文显示；
- 提供 👁 显示/隐藏切换；
- 任何提示 / 日志 / 异常都不回显完整 Key。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _password_row(edit: QLineEdit, parent: QWidget) -> QHBoxLayout:
    """QLineEdit.Password + 👁 显示/隐藏按钮。"""
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    row = QHBoxLayout()
    row.addWidget(edit, stretch=1)
    toggle = QPushButton("👁")
    toggle.setCheckable(True)
    toggle.setFixedWidth(40)
    toggle.setToolTip("显示 / 隐藏 API Key")

    def _on_toggle(checked: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    toggle.toggled.connect(_on_toggle)
    row.addWidget(toggle)
    return row


class AddAIProfileDialog(QDialog):
    """添加 API 配置。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("添加 API 配置")
        self.setModal(True)
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：USTC DeepSeek")
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("例如：https://api.llm.ustc.edu.cn/v1")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("例如：deepseek-v4-flash-ascend")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI 兼容 (openai_compatible)",
                                    "openai_compatible")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("API Key（写入系统凭据存储）")

        form.addRow("名称 *", self.name_edit)
        form.addRow("Base URL *", self.base_url_edit)
        form.addRow("Model *", self.model_edit)
        form.addRow("类型", self.provider_combo)
        form.addRow("API Key *", _password_row(self.api_key_edit, self))
        layout.addLayout(form)

        hint = QLabel(
            "API Key 不会写入 SQLite / 日志，而是保存到系统凭据存储"
            "（Windows 凭据管理器）。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认添加")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "缺少名称", "请填写配置名称。")
            return
        if not self.base_url_edit.text().strip():
            QMessageBox.warning(self, "缺少 Base URL", "请填写 Base URL。")
            return
        if not self.model_edit.text().strip():
            QMessageBox.warning(self, "缺少 Model", "请填写 Model。")
            return
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "缺少 API Key", "请填写 API Key。")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "display_name": self.name_edit.text().strip(),
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "provider_type": self.provider_combo.currentData(),
        }


class EditAPIKeyDialog(QDialog):
    """修改 API Key（默认隐藏，可切换显示）。"""

    def __init__(self, profile_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"修改 API Key · {profile_name}")
        self.setModal(True)
        self.resize(460, 160)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("输入新的 API Key")
        form.addRow("新 API Key", _password_row(self.api_key_edit, self))
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _on_accept(self) -> None:
        if not self.api_key_edit.text().strip():
            QMessageBox.warning(self, "缺少 API Key", "API Key 不能为空。")
            return
        self.accept()

    def api_key(self) -> str:
        return self.api_key_edit.text().strip()


class RenameProfileDialog(QDialog):
    """重命名配置（不改变 Key）。"""

    def __init__(self, current_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("重命名配置")
        self.setModal(True)
        self.resize(420, 140)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(current_name)
        form.addRow("配置名称", self.name_edit)
        layout.addLayout(form)

        hint = QLabel("重命名不会修改 API Key / Base URL / Model。")
        hint.setObjectName("TaskMeta")
        layout.addWidget(hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "缺少名称", "配置名称不能为空。")
            return
        self.accept()

    def display_name(self) -> str:
        return self.name_edit.text().strip()


class FinalPromptPreviewDialog(QDialog):
    """最终 Prompt 预览：按真实消息结构展示 System / User / Runtime Context。"""

    def __init__(
        self,
        title: str,
        *,
        system_text: str = "",
        user_text: str = "",
        context_text: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"最终 Prompt 预览 · {title}")
        self.setModal(True)
        self.resize(900, 720)

        layout = QVBoxLayout(self)
        if system_text:
            layout.addWidget(self._section_title("System Prompt（system 消息）"))
            layout.addWidget(self._readonly_box(system_text), stretch=2)
        if user_text:
            layout.addWidget(self._section_title("User Prompt / Instruction（user 消息）"))
            layout.addWidget(self._readonly_box(user_text), stretch=3)
        if context_text:
            layout.addWidget(self._section_title("Runtime Context（系统动态生成，只读）"))
            layout.addWidget(self._readonly_box(context_text), stretch=2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    @staticmethod
    def _readonly_box(text: str) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        return box


class PromptDefaultDialog(QDialog):
    """只读展示系统默认模板（用于与当前自定义对比）。"""

    def __init__(self, title: str, template: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"系统默认 Prompt · {title}")
        self.setModal(True)
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "以下为 Study Agent 内置默认 Prompt（代码 canonical default）。"
            "你的自定义版本只保存在本地数据库，不会被 git pull 覆盖。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(template)
        layout.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
