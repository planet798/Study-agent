"""AI 设置中心对话框：API Profile 增删改 + 连接测试 + Prompt 预览。

安全要求：
- API Key 输入框默认 ``QLineEdit.Password``，默认绝不明文显示；
- 提供 👁 显示/隐藏切换；
- 任何提示 / 日志 / 异常都不回显完整 Key。
"""

from __future__ import annotations

from .components.form_dialog import finish_form
from .components.button import SAIconButton
from .components.settings_controls import feedback
from .design.icons import IconName

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
    toggle = SAIconButton(IconName.EYE, tooltip="显示 API Key", parent=parent)
    toggle.setCheckable(True)

    def _on_toggle(checked: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        text = "隐藏 API Key" if checked else "显示 API Key"
        toggle.setToolTip(text)
        toggle.setAccessibleName(text)

    toggle.toggled.connect(_on_toggle)
    row.addWidget(toggle)
    return row


class AddAIProfileDialog(QDialog):
    """添加 API 配置。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("添加 API 配置")
        self.setModal(True)
        self.resize(580, 520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：DeepSeek 订阅")
        self.vendor_combo = QComboBox()
        self.vendor_combo.addItem("自定义兼容接口", None)
        for label, preset in [
            ("OpenAI", {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"}),
            ("DeepSeek", {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}),
            ("OpenRouter", {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"}),
            ("硅基流动 SiliconFlow", {"name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-8B"}),
            ("月之暗面 Moonshot", {"name": "Moonshot", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2-0905-preview"}),
            ("阿里云百炼 DashScope", {"name": "DashScope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"}),
        ]:
            self.vendor_combo.addItem(label, preset)
        self.vendor_combo.currentIndexChanged.connect(self._apply_vendor_preset)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("例如：https://api.llm.ustc.edu.cn/v1")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("例如：deepseek-v4-flash-ascend")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI 兼容接口",
                                    "openai_compatible")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("API Key（写入系统凭据存储）")

        form.addRow("模型服务", self.vendor_combo)
        form.addRow("名称 *", self.name_edit)
        form.addRow("Base URL *", self.base_url_edit)
        form.addRow("Model *", self.model_edit)
        form.addRow("类型", self.provider_combo)
        form.addRow("API Key *", _password_row(self.api_key_edit, self))
        layout.addLayout(form)

        hint = QLabel(
            "选择已有模型服务的订阅/API 套餐后，填入该服务控制台生成的 API Key；"
            "本应用不会代购或创建订阅。API Key 不会写入 SQLite / 日志，"
            "而是保存到系统凭据存储。预设服务均通过 OpenAI 兼容接口接入。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认添加")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        finish_form(self)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            feedback(self.error_label, "请填写配置名称。", "error")
            self.error_label.show()
            return
        if not self.base_url_edit.text().strip():
            feedback(self.error_label, "请填写 Base URL。", "error")
            self.error_label.show()
            return
        if not self.model_edit.text().strip():
            feedback(self.error_label, "请填写 Model。", "error")
            self.error_label.show()
            return
        if not self.api_key_edit.text().strip():
            feedback(self.error_label, "请填写 API Key。", "error")
            self.error_label.show()
            return
        self.accept()

    def _apply_vendor_preset(self, _index: int) -> None:
        preset = self.vendor_combo.currentData()
        if not preset:
            return
        self.base_url_edit.setText(preset["base_url"])
        self.model_edit.setText(preset["model"])
        if not self.name_edit.text().strip():
            self.name_edit.setText(preset["name"])

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
        self.resize(480, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("输入新的 API Key")
        form.addRow("新 API Key", _password_row(self.api_key_edit, self))
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        finish_form(self)

    def _on_accept(self) -> None:
        if not self.api_key_edit.text().strip():
            feedback(self.error_label, "API Key 不能为空。", "error")
            self.error_label.show()
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
        self.resize(460, 240)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(current_name)
        form.addRow("配置名称", self.name_edit)
        layout.addLayout(form)

        hint = QLabel("重命名不会修改 API Key / Base URL / Model。")
        hint.setObjectName("TaskMeta")
        layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        finish_form(self)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            feedback(self.error_label, "配置名称不能为空。", "error")
            self.error_label.show()
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
        finish_form(self)

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
            "以下为 Study Agent 内置默认 Prompt。"
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
        finish_form(self)
