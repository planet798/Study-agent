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

from PySide6.QtCore import Qt, QUrl, QSignalBlocker
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
from .settings_views import build_profiles_view, build_prompt_view
from .settings_drafts import SettingsDrafts
from .components.settings_controls import feedback



# ============================================================
# Tab 1：模型 / API
# ============================================================


class AIProfilesPanel(QWidget):
    """API Profile 管理面板。"""

    def __init__(self, ai_config_service, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = ai_config_service
        self._test_worker: AIConnectionTestWorker | None = None
        self._stopping = False
        self._test_profile_id = None
        self._oauth_worker: OAuthLoginWorker | None = None
        self._oauth_provider_catalog: dict[str, dict] = {}
        self._build_ui()
        self.refresh()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        build_profiles_view(self)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        if self.service is None:
            self._set_unavailable("模型配置暂不可用，请稍后重试。")
            return
        current_id = self._selected_id()
        try:
            profiles = self.service.list_profiles()
        except Exception:
            self._set_unavailable("暂时无法加载模型配置，请稍后重新打开设置。")
            return
        self.add_btn.setEnabled(True)
        self.oauth_btn.setEnabled(self._oauth_worker is None)
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
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
        try:
            self._update_source_banner()
            self._load_selected()
        except Exception:
            self._set_unavailable("暂时无法加载模型配置，请稍后重新打开设置。")

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
                    f"当前使用的配置：{cfg.profile_name}"
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
        self.test_btn.setEnabled(enabled and self._test_worker is None)
        feedback(self.test_result_label, "测试中…" if self._test_worker is not None
                 and pid == self._test_profile_id else "")
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
        self._stopping = False
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
        if self._stopping:
            return
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
        if self._stopping:
            return
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
        if self._stopping:
            return
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
            from ..ai.secrets import SecretStoreError
            details = (getattr(self.service.secrets, "last_error", "")
                       if isinstance(error, SecretStoreError) else "")
            message = str(error)
            if details:
                message += f"\n\n诊断（已脱敏）：{details}"
            QMessageBox.critical(self, "保存订阅失败", message)
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
        self.refresh()
        feedback(self.test_result_label, f"已保存「{profile.display_name}」并设为当前。", "success")

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
        self._load_selected()
        feedback(self.test_result_label, "API Key 已保存。", "success")

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
        if self._test_worker is not None:
            return
        self._stopping = False
        pid = self._selected_id()
        if pid is None:
            return
        p = self.service.get_profile(pid)
        if p is None:
            return
        # 凭据仅在后台请求内存中使用；Worker 不接收 DB connection/repository。
        is_oauth = p.provider_type.startswith("pi_oauth:")
        cfg = self.service.resolve_profile_config(pid) if is_oauth else None
        if is_oauth and cfg is not None and not cfg.is_configured:
            feedback(self.test_result_label,
                     f"连接失败：{cfg.error_message or 'OAuth 凭据无效，请重新登录'}", "error")
            return
        api_key = "" if is_oauth else (self.service.secrets.get(p.secret_ref) or "")
        self.test_btn.setEnabled(False)
        feedback(self.test_result_label, "测试中…")
        worker = AIConnectionTestWorker(
            base_url=p.base_url, model=p.model, api_key=api_key,
            timeout=10.0, parent=self,
            oauth_provider=cfg.oauth_provider_id if cfg else "",
            oauth_credential=cfg.oauth_credential if cfg else None,
        )
        self._test_worker = worker
        self._test_profile_id = pid

        def _done(result) -> None:
            if self._stopping:
                return
            self.test_btn.setEnabled(self._selected_id() is not None)
            self._test_worker = None
            if result.oauth_credential and is_oauth:
                try:
                    self.service.save_oauth_credential(pid, result.oauth_credential)
                except Exception as error:  # noqa: BLE001
                    feedback(self.test_result_label, "连接成功，但凭据更新未成功。请重新登录。", "error")
                    return
            if self._selected_id() != pid:
                return
            if result.ok:
                feedback(self.test_result_label,
                         f"连接成功 · Model: {result.model} · {result.latency_ms} ms", "success")
            else:
                feedback(self.test_result_label, f"连接失败：{result.message}", "error")

        worker.succeeded.connect(_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def stop_test_worker(self) -> None:
        """退出前停止连接测试线程，避免 QThread 仍在运行时被销毁。"""
        self._stopping = True
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
        self._drafts = SettingsDrafts()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        build_prompt_view(self)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        key = self._current_key
        if key is not None:
            self._drafts.edit(key, self.editor.toPlainText())
        if self.registry is None:
            self.tree.clear()
            self.name_label.setText("Prompt 管理暂不可用")
            self.desc_label.setText("请稍后重试，未保存内容会保留在当前页面。")
            self._set_editor_enabled(False)
            self.route_combo.setEnabled(False)
            return
        try:
            by_cat = self.registry.by_category()
            with QSignalBlocker(self.tree):
                self.tree.clear()
                for category, definitions in by_cat.items():
                    cat_item = QTreeWidgetItem([category])
                    cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.tree.addTopLevelItem(cat_item)
                    for definition in definitions:
                        status = "已自定义" if self.registry.is_customized(definition.key) else "系统默认"
                        child = QTreeWidgetItem([f"{definition.display_name}  [{status}]"])
                        child.setData(0, Qt.ItemDataRole.UserRole, definition.key)
                        cat_item.addChild(child)
                    cat_item.setExpanded(True)
            self._reload_routes()
            if key is not None and self._reselect(key):
                return
        except Exception:
            self._set_editor_enabled(False)
            feedback(self.feedback, "暂时无法加载 Prompt，草稿已保留。", "error")
            return
        self._current_key = None
        self._set_editor_enabled(False)
        self.name_label.setText("请选择 Prompt")
        self.desc_label.clear()
        self.status_label.clear()
        self.status_tag.hide()
        self.var_label.clear()
        with QSignalBlocker(self.editor):
            self.editor.clear()
        self._update_draft_status()

    def _reload_routes(self) -> None:
        route_id = self.route_combo.currentData()
        self.route_combo.clear()
        self.route_combo.addItem("（当前路线）", None)
        if self.preview_service is None:
            self.route_combo.setEnabled(False)
            return
        for route in self.preview_service.available_routes():
            self.route_combo.addItem(route["name"], route["id"])
        index = self.route_combo.findData(route_id)
        self.route_combo.setCurrentIndex(index if index >= 0 else 0)
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
        previous = self._current_key
        if previous is not None:
            self._drafts.edit(previous, self.editor.toPlainText())
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
        text = self._drafts.load(key, self.registry.effective_template(key))
        self._current_key = key
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(text)
        self._set_editor_enabled(True)
        self._update_draft_status()
        if previous != key:
            self.feedback.clear()

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.editor.setEnabled(enabled)
        for b in (self.save_btn, self.reset_btn, self.default_btn, self.preview_btn):
            b.setEnabled(enabled)
        self.discard_btn.setEnabled(enabled and self._current_key is not None
                                    and self._drafts.dirty(self._current_key))

    def _on_editor_changed(self):
        if self._current_key is not None:
            self._drafts.edit(self._current_key, self.editor.toPlainText())
        self._update_draft_status()

    def _update_draft_status(self):
        dirty = self._current_key is not None and self._drafts.dirty(self._current_key)
        self.draft_label.setText("未保存 · 预览使用已保存版本" if dirty else
                                 "已保存版本" if self._current_key else "")
        self.discard_btn.setEnabled(self.editor.isEnabled() and dirty)

    def _discard(self):
        key = self._current_key
        if key is None:
            return
        try:
            saved = self.registry.effective_template(key)
        except Exception:
            feedback(self.feedback, "暂时无法重新加载 Prompt，草稿已保留。", "error")
            return
        self._drafts.accept(key, saved)
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(saved)
        self._update_draft_status()
        feedback(self.feedback, "已放弃未保存修改。")

    # ---------- 操作 ----------

    def _on_save(self) -> None:
        key = self._current_key
        if key is None:
            return
        try:
            self.registry.set_override(key, self.editor.toPlainText())
        except PromptValidationError as error:
            feedback(self.feedback, f"{error}；请修正后再保存。草稿已保留。", "error")
            return
        except Exception:
            feedback(self.feedback, "无法保存 Prompt，请稍后重试。草稿已保留。", "error")
            return
        self._reload_after_write(key, "Prompt 已保存，下一次 AI 调用生效。")

    def _reload_after_write(self, key, message):
        try:
            saved = self.registry.effective_template(key)
        except Exception:
            feedback(self.feedback, "操作已保存，但暂时无法重新加载。请稍后刷新。", "error")
            return
        self._drafts.accept(key, saved)
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(saved)
        self.refresh()
        feedback(self.feedback, message, "success")

    def _on_reset(self) -> None:
        key = self._current_key
        if key is None:
            return
        if not self.registry.is_customized(key):
            feedback(self.feedback, "当前已是系统默认；可用“放弃修改”撤销草稿。")
            return
        if QMessageBox.question(
            self, "恢复默认",
            "将删除当前自定义版本并恢复 Study Agent 内置 Prompt。\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.registry.reset(key)
        except Exception:
            feedback(self.feedback, "无法恢复默认，草稿已保留。", "error")
            return
        self._reload_after_write(key, "已恢复系统默认 Prompt。")

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
                    return True
        return False


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
            "模型与 API 用于连接模型，高级用于调整 Prompt。"
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
