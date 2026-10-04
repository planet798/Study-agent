"""设置面板的控件构造；无服务或数据库调用。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QMenu, QPlainTextEdit, QScrollArea,
    QTreeWidget, QVBoxLayout, QWidget,
)
from .components.button import SAButton
from .components.flow_layout import FlowWidget
from .components.info_banner import SAInfoBanner
from .components.tag import SATag
from .components.section_header import SASectionHeader
from .components.settings_controls import MenuCommandButton, SettingsSplitter
from .components.workspace_sections import plain_label
from .design import icons as _icons, typography as _type

class SettingsSection(QWidget):
    def __init__(self):
        super().__init__()
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(4, 12, 4, 12)
        self.body_layout.setSpacing(8)

    def add_widget(self, widget):
        self.body_layout.addWidget(widget)


def build_profiles_view(owner):
    layout = QVBoxLayout(owner)
    layout.setSpacing(8)

    top_widget = FlowWidget()
    top = top_widget.flow
    owner.add_btn = SAButton(
        "添加 API Key 配置", variant="primary",
        icon_name=_icons.IconName.ADD,
    )
    owner.add_btn.clicked.connect(owner._on_add)
    top.addWidget(owner.add_btn)

    owner.oauth_btn = SAButton("订阅账号登录", variant="secondary")
    owner.oauth_btn.clicked.connect(owner._on_oauth_login)
    top.addWidget(owner.oauth_btn)

    owner.legacy_btn = MenuCommandButton("保存为配置", owner)
    owner.legacy_btn.clicked.connect(owner._on_import_legacy)

    layout.addWidget(top_widget)

    owner.source_banner = SAInfoBanner("", "", variant="info")
    owner.source_label = owner.source_banner.description_label()
    layout.addWidget(owner.source_banner)

    splitter = SettingsSplitter()

    owner.list_widget = QListWidget()
    owner.list_widget.currentItemChanged.connect(
        lambda *_: owner._load_selected()
    )
    owner.list_widget.setMinimumWidth(0)
    owner.list_widget.setWordWrap(True)
    splitter.addWidget(owner.list_widget)

    detail = QWidget()
    form = QVBoxLayout(detail)
    form.setContentsMargins(12, 0, 0, 0)
    owner.detail_title = QLabel("未选择配置")
    owner.detail_title.setObjectName("SectionTitle")
    owner.detail_title.setWordWrap(True)
    owner.detail_title.setTextFormat(Qt.TextFormat.PlainText)
    form.addWidget(owner.detail_title)

    owner.info_label = QLabel("")
    owner.info_label.setWordWrap(True)
    owner.info_label.setTextFormat(Qt.TextFormat.PlainText)
    owner.info_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    form.addWidget(owner.info_label)

    btn_row1 = QHBoxLayout()
    owner.set_active_btn = SAButton("设为当前", variant="primary")
    owner.set_active_btn.clicked.connect(owner._on_set_active)
    owner.test_btn = SAButton("测试连接", variant="secondary")
    owner.test_btn.clicked.connect(owner._on_test_connection)
    btn_row1.addWidget(owner.set_active_btn)
    btn_row1.addWidget(owner.test_btn)
    btn_row1.addStretch()
    form.addLayout(btn_row1)

    owner.edit_key_btn = MenuCommandButton("修改 Key", owner)
    owner.edit_key_btn.clicked.connect(owner._on_edit_key)
    owner.rename_btn = MenuCommandButton("重命名", owner)
    owner.rename_btn.clicked.connect(owner._on_rename)
    owner.delete_btn = MenuCommandButton("删除", owner)
    owner.delete_btn.clicked.connect(owner._on_delete)
    owner.more_btn = SAButton("更多操作", variant="subtle")
    menu = QMenu(owner.more_btn)
    for button in (owner.edit_key_btn, owner.rename_btn, owner.delete_btn, owner.legacy_btn):
        menu.addAction(button.command)
    owner.more_btn.setMenu(menu)
    btn_row1.insertWidget(btn_row1.count() - 1, owner.more_btn)

    owner.test_result_label = QLabel("")
    owner.test_result_label.setWordWrap(True)
    form.addWidget(owner.test_result_label)
    form.addStretch()

    splitter.addWidget(detail)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 2)
    layout.addWidget(splitter, stretch=1)

def build_prompt_view(owner):
    layout = QVBoxLayout(owner)
    layout.setSpacing(8)

    splitter = SettingsSplitter()
    owner.tree = QTreeWidget()
    owner.tree.setHeaderHidden(True)
    owner.tree.itemSelectionChanged.connect(owner._on_select)
    owner.tree.setMinimumWidth(0)
    splitter.addWidget(owner.tree)

    right = QWidget()
    form = QVBoxLayout(right)
    form.setContentsMargins(12, 0, 0, 0)

    owner.name_label = QLabel("请选择 Prompt")
    owner.name_label.setObjectName("SectionTitle")
    owner.name_label.setWordWrap(True)
    form.addWidget(owner.name_label)

    owner.desc_label = QLabel("")
    owner.desc_label.setWordWrap(True)
    owner.desc_label.setObjectName("TaskMeta")
    form.addWidget(owner.desc_label)

    owner.status_label = plain_label("")
    form.addWidget(owner.status_label)
    owner.status_tag = SATag("", "neutral")
    owner.status_tag.setVisible(False)
    form.addWidget(owner.status_tag, alignment=Qt.AlignmentFlag.AlignLeft)

    owner.var_label = QLabel("")
    owner.var_label.setWordWrap(True)
    owner.var_label.setObjectName("TaskMeta")
    owner.var_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    form.addWidget(owner.var_label)

    owner.editor = QPlainTextEdit()
    owner.editor.setObjectName("SAPromptEditor")
    owner.editor.setFont(_type.font_for(_type.MONOSPACE))
    owner.editor.setAccessibleName("Prompt 编辑器")
    owner.editor.setPlaceholderText("选择左侧 Prompt 后可在此编辑…")
    form.addWidget(owner.editor, stretch=1)

    owner.save_btn = SAButton("保存修改", variant="primary")
    owner.save_btn.clicked.connect(owner._on_save)
    owner.preview_btn = SAButton("预览已保存版本", variant="secondary")
    owner.preview_btn.clicked.connect(owner._on_preview)
    owner.reset_btn = MenuCommandButton("恢复默认", owner)
    owner.reset_btn.clicked.connect(owner._on_reset)
    owner.default_btn = MenuCommandButton("查看系统默认", owner)
    owner.default_btn.clicked.connect(owner._on_view_default)
    owner.draft_label = plain_label("")
    form.addWidget(owner.draft_label)
    actions = FlowWidget()
    actions.add_widget(owner.save_btn)
    actions.add_widget(owner.preview_btn)
    owner.discard_btn = SAButton("放弃修改", variant="subtle")
    owner.discard_btn.clicked.connect(owner._discard)
    actions.add_widget(owner.discard_btn)
    more = SAButton("更多操作", variant="subtle")
    menu = QMenu(more)
    menu.addAction(owner.default_btn.command)
    menu.addAction(owner.reset_btn.command)
    more.setMenu(menu)
    actions.add_widget(more)
    form.addWidget(actions)
    owner.feedback = plain_label("")
    form.addWidget(owner.feedback)
    owner.editor.textChanged.connect(owner._on_editor_changed)

    route_row = QHBoxLayout()
    route_row.addWidget(QLabel("预览路线"))
    owner.route_combo = QComboBox()
    owner.route_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    owner.route_combo.setMinimumContentsLength(12)
    owner.route_combo.setMinimumWidth(0)
    route_row.addWidget(owner.route_combo)
    route_row.addStretch()
    form.addLayout(route_row)

    splitter.addWidget(right)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 3)
    layout.addWidget(splitter, stretch=1)

    owner._set_editor_enabled(False)


def build_personalization_view(owner):
    root = QVBoxLayout(owner)
    root.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    root.addWidget(scroll)
    instructions = SettingsSection()
    instructions.add_widget(SASectionHeader("Agent 说明"))
    instructions.add_widget(QLabel("告诉 Study Agent 你希望它如何帮助你学习。"))
    owner.editor = QPlainTextEdit()
    owner.editor.setAccessibleName("Agent 说明")
    owner.editor.setPlaceholderText(
        "例如：\n我目前基础比较薄弱，讲解时先讲直觉，再解释原理。\n"
        "数据结构示例默认使用 C++。\n学习过程尽量分步骤推进……"
    )
    owner.editor.setMinimumHeight(180)
    instructions.add_widget(owner.editor)
    actions = FlowWidget()
    owner.counter = plain_label("0 / 8000")
    actions.add_widget(owner.counter)
    owner.draft_label = plain_label("")
    actions.add_widget(owner.draft_label)
    owner.save_btn = SAButton("保存", variant="primary")
    actions.add_widget(owner.save_btn)
    owner.discard_btn = SAButton("放弃修改", variant="subtle")
    owner.discard_btn.clicked.connect(owner._discard)
    actions.add_widget(owner.discard_btn)
    instructions.add_widget(actions)
    layout.addWidget(instructions, stretch=1)

    memory = SettingsSection()
    memory.add_widget(SASectionHeader("记忆"))
    description = QLabel("本地记忆用于在不同学习会话之间保留你的长期偏好和背景信息。")
    description.setWordWrap(True)
    memory.add_widget(description)
    owner.memory_checkbox = QCheckBox("启用本地记忆")
    owner.auto_memory_checkbox = QCheckBox("允许根据学习会话生成记忆")
    memory.add_widget(owner.memory_checkbox)
    memory.add_widget(owner.auto_memory_checkbox)
    note = QLabel(
        "Agent 会在新的学习对话中使用已保存的说明和已启用记忆。"
        "记忆开关不会自动保存尚未确认的内容。"
    )
    note.setObjectName("TaskMeta")
    note.setWordWrap(True)
    memory.add_widget(note)
    owner.manage_btn = SAButton("管理记忆", variant="secondary")
    memory.add_widget(owner.manage_btn)
    layout.addWidget(memory)
    owner.feedback = QLabel()
    owner.feedback.setWordWrap(True)
    layout.addWidget(owner.feedback)
    owner.editor.textChanged.connect(owner._update_counter)
    owner.save_btn.clicked.connect(owner._save)
    owner.memory_checkbox.toggled.connect(owner._set_memory_enabled)
    owner.auto_memory_checkbox.toggled.connect(owner._set_auto_memory_enabled)
    owner.manage_btn.clicked.connect(owner._manage)
