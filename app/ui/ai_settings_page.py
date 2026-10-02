"""AI 设置中心页面（一级导航）。

Tab 1【个性化】: Agent 说明与本地记忆偏好（仅持久化）。
Tab 2【模型 / API】: AI Provider Profile 管理。
Tab 3【高级】: 现有内部 Prompt 管理与预览。

设计约束：
- API Key 只写系统 keyring；UI 默认遮挡，绝不回显完整 Key；
- 网络测试在 QThread 后台执行，只传普通 base_url / model / api_key，不传 DB；
- Prompt 修改立即写 override，下一次 AI 调用即生效（无需重启）。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai.prompt_registry import PromptRegistry, PromptValidationError
from ..services.prompt_preview_service import PromptPreviewService
from .components.button import SAButton, SAIconButton
from .components.card import SACard
from .components.info_banner import SAInfoBanner
from .components.section_header import SASectionHeader
from .components.status_badge import SAStatusBadge
from .components.tag import SATag
from .design import icons as _icons
from .design import typography as _type
from .design import theme_preferences as _theme_prefs
from .design.theme_manager import ThemeMode, theme_manager
from .ai_settings_dialogs import (
    AddAIProfileDialog,
    EditAPIKeyDialog,
    FinalPromptPreviewDialog,
    PromptDefaultDialog,
    RenameProfileDialog,
)
from .ai_worker import AIConnectionTestWorker, OAuthLoginWorker
from .styles import apply_secondary_button_text
from .personalization_panel import PersonalizationPanel


# ============================================================
# Tab 1：模型 / API
# ============================================================


class AIProfilesPanel(QWidget):
    """API Profile 管理面板。"""

    def __init__(self, ai_config_service, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = ai_config_service
        self._test_worker: AIConnectionTestWorker | None = None
        self._oauth_worker: OAuthLoginWorker | None = None
        self._oauth_provider_catalog: dict[str, dict] = {}
        self._build_ui()
        self.refresh()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.add_btn = SAButton(
            "添加 API Key 配置", variant="primary",
            icon_name=_icons.IconName.ADD,
        )
        self.add_btn.clicked.connect(self._on_add)
        top.addWidget(self.add_btn)

        self.oauth_btn = SAButton("订阅账号登录", variant="secondary")
        self.oauth_btn.clicked.connect(self._on_oauth_login)
        top.addWidget(self.oauth_btn)

        self.legacy_btn = SAButton("保存为配置", variant="secondary")
        self.legacy_btn.clicked.connect(self._on_import_legacy)
        top.addWidget(self.legacy_btn)

        top.addStretch()
        layout.addLayout(top)

        self.source_banner = SAInfoBanner("", "", variant="info")
        self.source_label = self.source_banner.description_label()
        layout.addWidget(self.source_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(
            lambda *_: self._load_selected()
        )
        splitter.addWidget(self.list_widget)

        detail = QWidget()
        form = QVBoxLayout(detail)
        form.setContentsMargins(12, 0, 0, 0)
        self.detail_title = QLabel("未选择配置")
        self.detail_title.setObjectName("SectionTitle")
        form.addWidget(self.detail_title)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addWidget(self.info_label)

        btn_row1 = QHBoxLayout()
        self.set_active_btn = SAButton("设为当前", variant="primary")
        self.set_active_btn.clicked.connect(self._on_set_active)
        self.test_btn = SAButton("测试连接", variant="secondary")
        self.test_btn.clicked.connect(self._on_test_connection)
        btn_row1.addWidget(self.set_active_btn)
        btn_row1.addWidget(self.test_btn)
        btn_row1.addStretch()
        form.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.edit_key_btn = SAButton("修改 Key", variant="secondary")
        self.edit_key_btn.clicked.connect(self._on_edit_key)
        self.rename_btn = SAButton("重命名", variant="subtle")
        self.rename_btn.clicked.connect(self._on_rename)
        self.delete_btn = SAButton("删除", variant="danger")
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row2.addWidget(self.edit_key_btn)
        btn_row2.addWidget(self.rename_btn)
        btn_row2.addWidget(self.delete_btn)
        btn_row2.addStretch()
        form.addLayout(btn_row2)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        form.addWidget(self.test_result_label)
        form.addStretch()

        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        if self.service is None:
            self._set_unavailable("AI 配置不可用（未注入 AIConfigService）。")
            return
        current_id = self._selected_id()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        profiles = self.service.list_profiles()
        select_row = 0
        for i, p in enumerate(profiles):
            suffix = "  当前" if p.is_active else ""
            is_oauth = p.provider_type.startswith("pi_oauth:")
            endpoint = f"订阅账号 · {p.model}" if is_oauth else (p.base_url or "（无 Base URL）")
            item = QListWidgetItem(
                f"{p.display_name}{suffix}\n    {endpoint}"
            )
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            if p.is_active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip("当前配置")
            self.list_widget.addItem(item)
            if current_id is not None and p.id == current_id:
                select_row = i
            if current_id is None and p.is_active:
                select_row = i
        self.list_widget.blockSignals(False)
        if profiles:
            self.list_widget.setCurrentRow(select_row)
        self._update_source_banner()
        self._load_selected()

    def _set_unavailable(self, message: str) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.blockSignals(False)
        self.detail_title.setText("不可用")
        self.info_label.setText("")
        self.test_result_label.setText("")
        for b in (self.add_btn, self.oauth_btn, self.legacy_btn, self.set_active_btn,
                  self.test_btn, self.edit_key_btn, self.rename_btn,
                  self.delete_btn):
            b.setEnabled(False)
        self.source_banner.set_variant("warning")
        self.source_label.setText(message)

    def _selected_id(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_source_banner(self) -> None:
        if self.service is None:
            return
        count = self.service.profile_count()
        has_selection = count > 0 and self.service.get_active_profile() is None
        cfg = self.service.get_runtime_config()
        if count == 0:
            legacy_ok = self.service.is_legacy_configured()
            self.legacy_btn.setVisible(True)
            self.legacy_btn.setEnabled(legacy_ok)
            self.source_banner.set_variant("info" if legacy_ok else "warning")
            if legacy_ok:
                self.source_label.setText(
                    "当前正在使用环境变量配置（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / "
                    "DEEPSEEK_MODEL）。可点击【保存为配置】把非密信息迁入数据库，"
                    "API Key 写入系统凭据存储。"
                )
            else:
                self.source_label.setText(
                    "当前无可用 AI 配置。可添加 API Key，或点击【订阅账号登录】使用支持的订阅服务。"
                )
        else:
            self.legacy_btn.setVisible(False)
            self.source_banner.set_variant("info")
            if cfg.profile_name:
                self.source_label.setText(
                    f"当前使用的配置：{cfg.profile_name}（source={cfg.source}）"
                )
            elif has_selection:
                self.source_label.setText(
                    "有可用配置，但尚未选择“当前配置”。请选择一条并点击【设为当前】。"
                )
            else:
                self.source_label.setText("")

    def _load_selected(self) -> None:
        pid = self._selected_id()
        enabled = pid is not None
        for b in (self.set_active_btn, self.test_btn, self.edit_key_btn,
                  self.rename_btn, self.delete_btn):
            b.setEnabled(enabled)
        self.test_result_label.setText("")
        if pid is None:
            self.detail_title.setText("未选择配置")
            self.info_label.setText("")
            return
        p = self.service.get_profile(pid)
        if p is None:
            self.refresh()
            return
        self.detail_title.setText(p.display_name + ("（当前）" if p.is_active else ""))
        is_oauth = p.provider_type.startswith("pi_oauth:")
        credential_state = "已登录（订阅 OAuth）" if self.service.has_credentials(pid) else "未配置"
        if is_oauth:
            base_url_text = f"pi-ai OAuth · {p.provider_type.split(':', 1)[1]}"
            credential_label = "账号："
        else:
            base_url_text = p.base_url or "（未填写）"
            credential_label = "API Key："
        self.edit_key_btn.setEnabled(not is_oauth)
        self.info_label.setText(
            f"配置名称：{p.display_name}\n"
            f"服务：{base_url_text}\n"
            f"Model：{p.model or '（未填写）'}\n"
            f"{credential_label}{credential_state if is_oauth else ('已配置（••••••••）' if self.service.has_api_key(pid) else '未配置')}\n"
            f"类型：{'订阅账号登录' if is_oauth else p.provider_type}"
        )
        self.set_active_btn.setEnabled(not p.is_active)

    # ---------- 操作 ----------

    def _on_add(self) -> None:
        dlg = AddAIProfileDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        values = dlg.values()
        try:
            self.service.create_profile(
                display_name=values["display_name"],
                base_url=values["base_url"],
                model=values["model"],
                api_key=values["api_key"],
                provider_type=values["provider_type"],
            )
        except Exception as e:  # noqa: BLE001 - 含 SecretStoreError
            QMessageBox.critical(self, "添加失败", str(e))
            return
        self.refresh()

    def _on_oauth_login(self) -> None:
        if self._oauth_worker is not None:
            return
        try:
            from ..ai.pi_ai_bridge import PiAIBridge
            providers = [p for p in PiAIBridge().catalog() if p.get("isSubscription") and p.get("models")]
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "订阅登录不可用", str(error))
            return
        if not providers:
            QMessageBox.warning(self, "无可用服务", "pi-ai 没有返回支持订阅登录的服务。")
            return
        labels = [f"{p['name']} — {p['loginLabel']}" for p in providers]
        label, accepted = QInputDialog.getItem(
            self, "订阅账号登录", "选择要登录的模型服务：", labels, 0, False
        )
        if not accepted:
            return
        provider = providers[labels.index(label)]
        worker = OAuthLoginWorker(provider["id"], self)
        self._oauth_worker = worker
        self.oauth_btn.setEnabled(False)
        worker.auth_event.connect(self._on_oauth_event)
        worker.prompt_requested.connect(self._on_oauth_prompt)
        worker.succeeded.connect(lambda credential: self._on_oauth_login_succeeded(provider, credential))
        worker.failed.connect(lambda message: QMessageBox.warning(self, "登录失败", message))
        worker.finished.connect(self._on_oauth_worker_finished)
        worker.start()

    def _on_oauth_event(self, event: dict) -> None:
        auth_event = event.get("authEvent", {})
        kind = auth_event.get("type")
        if kind == "auth_url":
            url = auth_event.get("url", "")
            if url:
                QDesktopServices.openUrl(QUrl(url))
            text = auth_event.get("instructions") or "已在浏览器打开授权页面。请完成登录并返回 Study Agent。"
            QMessageBox.information(self, "等待账号授权", text)
        elif kind == "device_code":
            url = auth_event.get("verificationUri", "")
            if url:
                QDesktopServices.openUrl(QUrl(url))
            QMessageBox.information(
                self, "设备授权",
                f"请在浏览器打开：\n{url}\n\n并输入授权码：\n{auth_event.get('userCode', '')}",
            )
        elif kind == "info":
            text = auth_event.get("message", "")
            for link in auth_event.get("links", []):
                text += f"\n{link.get('label') or '帮助'}：{link.get('url')}"
            if text:
                QMessageBox.information(self, "订阅登录", text)

    def _on_oauth_prompt(self, event: dict) -> None:
        worker = self._oauth_worker
        if worker is None:
            return
        prompt = event.get("prompt", {})
        prompt_type = prompt.get("type")
        if prompt_type == "select":
            options = prompt.get("options", [])
            labels = [item.get("label", item.get("id", "")) for item in options]
            label, accepted = QInputDialog.getItem(
                self, "订阅登录", prompt.get("message", "请选择"), labels, 0, False
            )
            worker.answer_prompt(options[labels.index(label)]["id"] if accepted else None)
        else:
            from PySide6.QtWidgets import QLineEdit
            mode = QLineEdit.EchoMode.Password if prompt_type == "secret" else QLineEdit.EchoMode.Normal
            value, accepted = QInputDialog.getText(
                self, "订阅登录", prompt.get("message", "请输入"), mode,
                prompt.get("placeholder", ""),
            )
            worker.answer_prompt(value if accepted else None)

    def _on_oauth_login_succeeded(self, provider: dict, credential: dict) -> None:
        models = provider.get("models", [])
        labels = [f"{model['name']} ({model['id']})" for model in models]
        label, accepted = QInputDialog.getItem(
            self, "选择模型", f"{provider['name']} 登录成功，请选择要使用的模型：",
            labels, 0, False,
        )
        if not accepted:
            QMessageBox.information(
                self, "登录完成", "账号授权已完成；关闭窗口前请先选择模型以保存配置。"
            )
            return
        model = models[labels.index(label)]
        try:
            self.service.create_oauth_profile(
                provider_id=provider["id"],
                display_name=f"{provider['name']} 订阅",
                model=model["id"],
                credential=credential,
                make_active=True,
            )
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "保存订阅失败", str(error))
            return
        QMessageBox.information(self, "已连接", f"已保存 {provider['name']} 订阅，并选择模型 {model['id']}。")
        self.refresh()

    def _on_oauth_worker_finished(self) -> None:
        worker = self._oauth_worker
        self._oauth_worker = None
        self.oauth_btn.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _on_import_legacy(self) -> None:
        try:
            profile = self.service.import_legacy_profile()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        QMessageBox.information(
            self, "已保存",
            f"已把环境变量配置保存为「{profile.display_name}」并设为当前。",
        )
        self.refresh()

    def _on_set_active(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        self.service.set_active(pid)
        self.refresh()

    def _on_edit_key(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        p = self.service.get_profile(pid)
        dlg = EditAPIKeyDialog(p.display_name if p else "", self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        try:
            self.service.set_api_key(pid, dlg.api_key())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        QMessageBox.information(self, "已保存", "API Key 已写入系统凭据存储。")
        self._load_selected()

    def _on_rename(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        p = self.service.get_profile(pid)
        dlg = RenameProfileDialog(p.display_name if p else "", self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        try:
            self.service.rename_profile(pid, dlg.display_name())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        self.refresh()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        p = self.service.get_profile(pid)
        if p is None:
            return
        profiles = self.service.list_profiles()
        others = [x for x in profiles if x.id != pid]
        reassign_to = None
        msg = f"确定删除配置「{p.display_name}」吗？\n同时会删除系统凭据存储中的登录凭据。"
        if p.is_active and others:
            names = [x.display_name for x in others]
            choice, ok = QInputDialog.getItem(
                self, "删除当前配置",
                "该配置是当前配置。请选择删除后要使用的配置：",
                names, 0, False,
            )
            if not ok:
                return
            reassign_to = others[names.index(choice)].id
            msg += f"\n删除后将使用：「{choice}」。"
        elif p.is_active:
            msg += "\n删除后当前将没有可用的 AI 配置。"
        if QMessageBox.question(
            self, "确认删除", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_profile(pid, reassign_to=reassign_to)
        self.refresh()

    def _on_test_connection(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        p = self.service.get_profile(pid)
        if p is None:
            return
        # 凭据仅在后台请求内存中使用；Worker 不接收 DB connection/repository。
        is_oauth = p.provider_type.startswith("pi_oauth:")
        cfg = self.service.resolve_profile_config(pid) if is_oauth else None
        api_key = "" if is_oauth else (self.service.secrets.get(p.secret_ref) or "")
        self.test_btn.setEnabled(False)
        self.test_result_label.setText("测试中…")
        worker = AIConnectionTestWorker(
            base_url=p.base_url, model=p.model, api_key=api_key,
            timeout=10.0, parent=self,
            oauth_provider=cfg.oauth_provider_id if cfg else "",
            oauth_credential=cfg.oauth_credential if cfg else None,
        )
        self._test_worker = worker

        def _done(result) -> None:
            self.test_btn.setEnabled(True)
            self._test_worker = None
            if result.oauth_credential and is_oauth:
                try:
                    self.service.save_oauth_credential(pid, result.oauth_credential)
                except Exception as error:  # noqa: BLE001
                    self.test_result_label.setText(f"连接成功，但凭据更新失败：{error}")
                    return
            if result.ok:
                self.test_result_label.setText(
                    f"连接成功 · Model: {result.model} · {result.latency_ms} ms"
                )
            else:
                self.test_result_label.setText(f"连接失败：{result.message}")

        worker.succeeded.connect(_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def stop_test_worker(self) -> None:
        """退出前停止连接测试线程，避免 QThread 仍在运行时被销毁。"""
        worker = self._test_worker
        self._test_worker = None
        if worker is not None and worker.isRunning():
            try:
                worker.requestInterruption()
                worker.wait()
            except Exception:  # noqa: BLE001
                pass
        oauth_worker = self._oauth_worker
        self._oauth_worker = None
        if oauth_worker is not None and oauth_worker.isRunning():
            try:
                oauth_worker.cancel()
                oauth_worker.wait(3000)
            except Exception:  # noqa: BLE001
                pass


# ============================================================
# Tab 2：Prompt 管理
# ============================================================


class PromptManagerPanel(QWidget):
    """Prompt 列表 + 编辑 + 保存 + 恢复默认 + 最终预览。"""

    def __init__(
        self,
        prompt_registry: PromptRegistry,
        preview_service: PromptPreviewService | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.registry = prompt_registry
        self.preview_service = preview_service
        self._current_key: str | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_select)
        splitter.addWidget(self.tree)

        right = QWidget()
        form = QVBoxLayout(right)
        form.setContentsMargins(12, 0, 0, 0)

        self.name_label = QLabel("请选择 Prompt")
        self.name_label.setObjectName("SectionTitle")
        form.addWidget(self.name_label)

        self.desc_label = QLabel("")
        self.desc_label.setWordWrap(True)
        self.desc_label.setObjectName("TaskMeta")
        form.addWidget(self.desc_label)

        self.status_label = QLabel("")
        form.addWidget(self.status_label)
        self.status_tag = SATag("", "neutral")
        self.status_tag.setVisible(False)
        form.addWidget(self.status_tag, alignment=Qt.AlignmentFlag.AlignLeft)

        self.var_label = QLabel("")
        self.var_label.setWordWrap(True)
        self.var_label.setObjectName("TaskMeta")
        self.var_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addWidget(self.var_label)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("SAPromptEditor")
        self.editor.setFont(_type.font_for(_type.MONOSPACE))
        self.editor.setAccessibleName("Prompt 编辑器")
        self.editor.setPlaceholderText("选择左侧 Prompt 后可在此编辑…")
        form.addWidget(self.editor, stretch=1)

        row = QHBoxLayout()
        self.save_btn = SAButton("保存修改", variant="primary")
        self.save_btn.clicked.connect(self._on_save)
        self.reset_btn = SAButton("恢复默认", variant="subtle")
        self.reset_btn.clicked.connect(self._on_reset)
        self.default_btn = SAButton("查看系统默认", variant="subtle")
        self.default_btn.clicked.connect(self._on_view_default)
        self.preview_btn = SAButton("最终 Prompt 预览", variant="secondary")
        self.preview_btn.clicked.connect(self._on_preview)
        for b in (self.save_btn, self.reset_btn, self.default_btn, self.preview_btn):
            row.addWidget(b)
        row.addStretch()
        form.addLayout(row)

        route_row = QHBoxLayout()
        route_row.addWidget(QLabel("预览路线"))
        self.route_combo = QComboBox()
        route_row.addWidget(self.route_combo)
        route_row.addStretch()
        form.addLayout(route_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, stretch=1)

        self._set_editor_enabled(False)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        if self.registry is None:
            self.tree.clear()
            self.name_label.setText("Prompt 管理不可用")
            self.desc_label.setText("未注入 PromptRegistry。")
            self.status_label.setText("")
            self.status_tag.setVisible(False)
            self.var_label.setText("")
            self.editor.setPlainText("")
            self.editor.setEnabled(False)
            for b in (self.save_btn, self.reset_btn, self.default_btn,
                      self.preview_btn):
                b.setEnabled(False)
            self.route_combo.clear()
            self.route_combo.setEnabled(False)
            return
        self.tree.clear()
        by_cat = self.registry.by_category()
        for category, defs in by_cat.items():
            cat_item = QTreeWidgetItem([category])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(cat_item)
            for d in defs:
                status = "已自定义" if self.registry.is_customized(d.key) else "系统默认"
                child = QTreeWidgetItem([f"{d.display_name}  [{status}]"])
                child.setData(0, Qt.ItemDataRole.UserRole, d.key)
                cat_item.addChild(child)
            cat_item.setExpanded(True)
        self._reload_routes()
        self._set_editor_enabled(False)
        self._current_key = None
        self.name_label.setText("请选择 Prompt")
        self.desc_label.setText("")
        self.status_label.setText("")
        self.status_tag.setVisible(False)
        self.var_label.setText("")
        self.editor.setPlainText("")

    def _reload_routes(self) -> None:
        self.route_combo.clear()
        self.route_combo.addItem("（当前路线）", None)
        if self.preview_service is None:
            self.route_combo.setEnabled(False)
            return
        for r in self.preview_service.available_routes():
            self.route_combo.addItem(r["name"], r["id"])
        self.route_combo.setEnabled(True)

    def _selected_key(self) -> str | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        key = items[0].data(0, Qt.ItemDataRole.UserRole)
        return key

    def _on_select(self) -> None:
        key = self._selected_key()
        if not key:
            return
        self._current_key = key
        definition = self.registry.definition(key)
        self.name_label.setText(definition.display_name)
        self.desc_label.setText(
            f"用途：{definition.description}\n"
            f"消息角色：{definition.role} · 分类：{definition.category}"
        )
        customized = self.registry.is_customized(key)
        self.status_label.setText(
            "当前状态：用户自定义（可在下方修改，或恢复默认）"
            if customized else "当前状态：系统默认"
        )
        self.status_tag.setText("已自定义" if customized else "系统默认")
        self.status_tag.set_variant("warning" if customized else "neutral")
        self.status_tag.setVisible(True)
        required = ", ".join(f"{{{{{v}}}}}" for v in definition.required_variables) or "（无）"
        optional = ", ".join(f"{{{{{v}}}}}" for v in definition.optional_variables) or "（无）"
        self.var_label.setText(f"必需变量：{required}\n可选变量：{optional}")
        self.editor.setPlainText(self.registry.effective_template(key))
        self._set_editor_enabled(True)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.editor.setEnabled(enabled)
        for b in (self.save_btn, self.reset_btn, self.default_btn, self.preview_btn):
            b.setEnabled(enabled)

    # ---------- 操作 ----------

    def _on_save(self) -> None:
        key = self._current_key
        if not key:
            return
        try:
            self.registry.set_override(key, self.editor.toPlainText())
        except PromptValidationError as e:
            QMessageBox.warning(
                self, "Prompt 变量校验失败",
                f"{e}\n\n请修正后再保存（不会写入数据库）。",
            )
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        QMessageBox.information(
            self, "已保存",
            "Prompt 已保存，下一次 AI 调用立即生效（无需重启）。",
        )
        self.refresh()
        self._reselect(key)

    def _on_reset(self) -> None:
        key = self._current_key
        if not key:
            return
        if not self.registry.is_customized(key):
            QMessageBox.information(self, "无需恢复", "该 Prompt 当前就是系统默认。")
            return
        if QMessageBox.question(
            self, "恢复默认",
            "将删除当前自定义版本并恢复 Study Agent 内置 Prompt。\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.registry.reset(key)
        self.refresh()
        self._reselect(key)

    def _on_view_default(self) -> None:
        key = self._current_key
        if not key:
            return
        d = self.registry.definition(key)
        PromptDefaultDialog(d.display_name, d.default_template, self).exec()

    def _on_preview(self) -> None:
        key = self._current_key
        if not key:
            return
        if self.preview_service is None:
            QMessageBox.information(self, "不可用", "当前没有可用的预览数据源。")
            return
        route_id = self.route_combo.currentData()
        try:
            conv = self.preview_service.preview_conversation(key, route_id=route_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "预览失败", str(e))
            return
        # 若编辑器有未保存修改，预览使用“已保存”的 effective template，
        # 避免误导（保存后才真正生效）。
        title = (conv.system or conv.user).definition.display_name
        system_text = conv.system.rendered if conv.system else ""
        user_text = conv.user.rendered if conv.user else ""
        context_text = self._format_context(conv.context)
        FinalPromptPreviewDialog(
            title,
            system_text=system_text,
            user_text=user_text,
            context_text=context_text,
            parent=self,
        ).exec()

    @staticmethod
    def _format_context(context: dict) -> str:
        if not context:
            return ""
        lines = []
        for name, value in context.items():
            text = "" if value is None else str(value)
            if len(text) > 4000:
                text = text[:4000] + "\n…（已截断）"
            lines.append(f"--- {{{{{name}}}}} ---\n{text}")
        return "\n\n".join(lines)

    def _reselect(self, key: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(i)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == key:
                    self.tree.setCurrentItem(child)
                    return


# ============================================================
# 页面
# ============================================================


class AISettingsPage(QWidget):
    """AI 设置一级页面。"""

    def __init__(
        self,
        ai_config_service,
        prompt_registry: PromptRegistry,
        preview_service: PromptPreviewService | None = None,
        parent: QWidget | None = None,
        theme_settings=None,
        personalization_service=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)

        # ---- 外观（独立于 AI service，始终可用） ----
        self.theme_settings = theme_settings
        appearance = QWidget()
        appearance_row = QHBoxLayout(appearance)
        appearance_row.setContentsMargins(0, 0, 0, 0)
        appearance_row.addWidget(QLabel("主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("跟随系统", ThemeMode.SYSTEM.value)
        self.theme_combo.addItem("浅色", ThemeMode.LIGHT.value)
        self.theme_combo.addItem("深色", ThemeMode.DARK.value)
        self.theme_combo.setAccessibleName("界面主题")
        current = theme_manager().current_mode
        idx = self.theme_combo.findData(
            current.value if isinstance(current, ThemeMode) else str(current)
        )
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        appearance_row.addWidget(self.theme_combo)
        appearance_row.addStretch()
        appearance_card = SACard()
        appearance_card.add_widget(
            SASectionHeader("外观", trailing=appearance)
        )
        layout.addWidget(appearance_card)

        hint = QLabel(
            "通过个性化设置，让 Study Agent 更符合你的学习习惯。"
            "模型与 API 设置负责连接模型，高级设置用于内部 Prompt 调整。"
        )
        hint.setObjectName("TaskMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tabs = QTabWidget()
        self.personalization_panel = PersonalizationPanel(personalization_service, self)
        self.profiles_panel = AIProfilesPanel(ai_config_service, self)
        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        warning = QLabel(
            "这里用于调整 Study Agent 内部结构化 AI 工作流。"
            "错误修改可能影响规划、验收或 JSON 输出。"
        )
        warning.setObjectName("TaskMeta")
        warning.setWordWrap(True)
        advanced_layout.addWidget(warning)
        self.prompt_panel = PromptManagerPanel(prompt_registry, preview_service, self.advanced_panel)
        advanced_layout.addWidget(self.prompt_panel, stretch=1)
        self.tabs.addTab(self.personalization_panel, "个性化")
        self.tabs.addTab(self.profiles_panel, "模型 / API")
        self.tabs.addTab(self.advanced_panel, "高级")
        self.tabs.setCurrentIndex(0)
        layout.addWidget(self.tabs, stretch=1)

    def _on_theme_combo_changed(self) -> None:
        mode = self.theme_combo.currentData()
        if not mode:
            return
        _theme_prefs.save_theme_mode(mode, self.theme_settings)
        theme_manager().set_theme(mode)

    def refresh(self) -> None:
        self.profiles_panel.refresh()
        self.personalization_panel.refresh()
        self.prompt_panel.refresh()
