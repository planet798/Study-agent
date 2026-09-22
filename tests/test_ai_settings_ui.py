"""AI 设置中心 UI 测试（offscreen）。

覆盖：顶部导航入口、两个 Tab、API Profile 增删改切、Key 默认遮挡、
Prompt 分类/编辑/保存/恢复/预览、占位符校验 UI、网络测试后台不冻结。
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from app.ai.config_service import ConnectionTestResult
from app.services.prompt_preview_service import PromptPreviewService
from app.ui.ai_settings_dialogs import AddAIProfileDialog
from app.ui.ai_settings_page import (
    AIProfilesPanel,
    AISettingsPage,
    PromptManagerPanel,
)


@pytest.fixture()
def preview_service(prompt_registry):
    return PromptPreviewService(
        prompt_registry, today_provider=lambda: "2026-01-05"
    )


@pytest.fixture()
def settings_page(qtbot, ai_config_service, prompt_registry, preview_service):
    page = AISettingsPage(ai_config_service, prompt_registry, preview_service)
    qtbot.addWidget(page)
    return page


class TestPageStructure:
    def test_both_panels_present(self, settings_page):
        assert isinstance(settings_page.profiles_panel, AIProfilesPanel)
        assert isinstance(settings_page.prompt_panel, PromptManagerPanel)

    def test_tab_labels(self, settings_page):
        from PySide6.QtWidgets import QTabWidget

        tab = settings_page.findChild(QTabWidget)
        labels = [tab.tabText(i) for i in range(tab.count())]
        assert labels == ["模型 / API", "Prompt 管理"]


class TestProfilePanel:
    def test_add_shows_in_list(self, qtbot, ai_config_service, settings_page):
        p = ai_config_service.create_profile(
            "USTC DeepSeek", "https://api.llm.ustc.edu.cn/v1", "deepseek",
            api_key="sk-test-1",
        )
        settings_page.profiles_panel.refresh()
        assert settings_page.profiles_panel.list_widget.count() == 1
        assert "USTC DeepSeek" in settings_page.profiles_panel.list_widget.item(0).text()

    def test_selected_profile_detail_masks_key(
        self, qtbot, ai_config_service, settings_page
    ):
        p = ai_config_service.create_profile(
            "USTC", "https://x/v1", "m", api_key="sk-test-secret-xyz"
        )
        panel = settings_page.profiles_panel
        panel.refresh()
        panel.list_widget.setCurrentRow(0)
        text = panel.info_label.text()
        assert "已配置" in text
        assert "sk-test-secret-xyz" not in text
        # 设置 active 按钮对当前 profile 禁用
        assert panel.set_active_btn.isEnabled() is False

    def test_set_current_via_panel(self, qtbot, ai_config_service, settings_page):
        a = ai_config_service.create_profile("A", "https://a/v1", "m", api_key="k")
        b = ai_config_service.create_profile("B", "https://b/v1", "m", api_key="k")
        panel = settings_page.profiles_panel
        panel.refresh()
        # 选中 B
        for i in range(panel.list_widget.count()):
            if "B" in panel.list_widget.item(i).text():
                panel.list_widget.setCurrentRow(i)
                break
        panel.set_active_btn.click()
        assert ai_config_service.get_active_profile().id == b.id

    def test_rename_via_service_then_refresh(
        self, qtbot, ai_config_service, settings_page
    ):
        p = ai_config_service.create_profile("Old", "https://x/v1", "m", api_key="k")
        ai_config_service.rename_profile(p.id, "新名字")
        settings_page.profiles_panel.refresh()
        assert "新名字" in settings_page.profiles_panel.list_widget.item(0).text()

    def test_delete_via_service_then_refresh(
        self, qtbot, ai_config_service, settings_page
    ):
        p = ai_config_service.create_profile("A", "https://x/v1", "m", api_key="k")
        ai_config_service.delete_profile(p.id)
        settings_page.profiles_panel.refresh()
        assert settings_page.profiles_panel.list_widget.count() == 0

    def test_key_masked_in_add_dialog(self, qtbot):
        dlg = AddAIProfileDialog()
        qtbot.addWidget(dlg)
        assert dlg.api_key_edit.echoMode() == QLineEdit.EchoMode.Password

    def test_legacy_banner_when_no_profile(self, conn, fake_keyring, prompt_registry,
                                            preview_service, qtbot):
        from app.ai.client import ENV_API_KEY, ENV_MODEL
        from app.ai.config_service import AIConfigService
        from app.ai.secrets import SecretStore

        svc = AIConfigService(
            conn=conn, secret_store=SecretStore(keyring_module=fake_keyring),
            env={ENV_API_KEY: "sk-env", ENV_MODEL: "m"},
        )
        panel = AIProfilesPanel(svc)
        qtbot.addWidget(panel)
        assert "环境变量配置" in panel.source_label.text()
        assert panel.legacy_btn.isVisible() or panel.legacy_btn.isEnabled()


class TestPromptPanel:
    def test_tree_groups_by_category(self, qtbot, prompt_registry, preview_service):
        panel = PromptManagerPanel(prompt_registry, preview_service)
        qtbot.addWidget(panel)
        cats = [
            panel.tree.topLevelItem(i).text(0)
            for i in range(panel.tree.topLevelItemCount())
        ]
        assert "Planner" in cats
        assert "Assessment" in cats

    def test_select_loads_template_and_variables(
        self, qtbot, prompt_registry, preview_service
    ):
        panel = PromptManagerPanel(prompt_registry, preview_service)
        qtbot.addWidget(panel)
        child = panel.tree.topLevelItem(1).child(0)
        panel.tree.setCurrentItem(child)
        key = panel._current_key
        assert key is not None
        assert panel.editor.toPlainText() == prompt_registry.effective_template(key)
        assert "必需变量" in panel.var_label.text()

    def test_save_and_reset(self, qtbot, prompt_registry, preview_service, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )
        panel = PromptManagerPanel(prompt_registry, preview_service)
        qtbot.addWidget(panel)
        # 找到 planner.user
        key = "planner.user"
        for i in range(panel.tree.topLevelItemCount()):
            cat = panel.tree.topLevelItem(i)
            for j in range(cat.childCount()):
                if cat.child(j).data(0, Qt.ItemDataRole.UserRole) == key:
                    panel.tree.setCurrentItem(cat.child(j))
        assert panel._current_key == key
        panel.editor.setPlainText(
            "MY {{context_json}} {{output_instruction}}"
        )
        panel.save_btn.click()
        assert prompt_registry.effective_template(key) == \
            "MY {{context_json}} {{output_instruction}}"
        panel.reset_btn.click()
        assert prompt_registry.is_customized(key) is False

    def test_invalid_template_blocks_save(
        self, qtbot, prompt_registry, preview_service, monkeypatch
    ):
        from PySide6.QtWidgets import QMessageBox

        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append(a),
        )
        panel = PromptManagerPanel(prompt_registry, preview_service)
        qtbot.addWidget(panel)
        key = "planner.user"
        for i in range(panel.tree.topLevelItemCount()):
            cat = panel.tree.topLevelItem(i)
            for j in range(cat.childCount()):
                if cat.child(j).data(0, Qt.ItemDataRole.UserRole) == key:
                    panel.tree.setCurrentItem(cat.child(j))
        panel.editor.setPlainText("缺少所有必需变量")
        panel.save_btn.click()
        assert warnings, "应弹出校验警告"
        assert prompt_registry.is_customized(key) is False

    def test_preview_dialog_receives_rendered_content(
        self, qtbot, prompt_registry, preview_service, monkeypatch
    ):
        import app.ui.ai_settings_page as page_mod

        captured = {}

        class FakeDialog:
            def __init__(self, title, system_text="", user_text="",
                         context_text="", parent=None):
                captured["title"] = title
                captured["system"] = system_text
                captured["user"] = user_text
                captured["context"] = context_text

            def exec(self):
                return 1

        monkeypatch.setattr(page_mod, "FinalPromptPreviewDialog", FakeDialog)
        panel = PromptManagerPanel(prompt_registry, preview_service)
        qtbot.addWidget(panel)
        key = "planner.user"
        for i in range(panel.tree.topLevelItemCount()):
            cat = panel.tree.topLevelItem(i)
            for j in range(cat.childCount()):
                if cat.child(j).data(0, Qt.ItemDataRole.UserRole) == key:
                    panel.tree.setCurrentItem(cat.child(j))
        panel.preview_btn.click()
        assert "system" in captured
        assert "user" in captured
        assert captured["user"]  # 真实渲染内容
        assert captured["context"]


class TestConnectionWorkerNonBlocking:
    def test_worker_emits_result(self, qtbot, monkeypatch):
        import app.ai.config_service as cfg_mod
        from app.ui.ai_worker import AIConnectionTestWorker

        def fake_run(base_url, model, api_key, timeout=10.0, urlopen=None):
            time.sleep(0.02)
            return ConnectionTestResult(ok=True, message="连接成功", model=model)

        monkeypatch.setattr(cfg_mod, "run_connection_test", fake_run)
        worker = AIConnectionTestWorker("https://x/v1", "m", "sk-test-1")
        results = []
        worker.succeeded.connect(lambda r: results.append(r))
        with qtbot.waitSignal(worker.succeeded, timeout=3000):
            worker.start()
        worker.wait()
        assert results and results[0].ok is True

    def test_worker_never_receives_repo(self):
        from app.ui.ai_worker import AIConnectionTestWorker
        import inspect

        sig = inspect.signature(AIConnectionTestWorker.__init__)
        params = set(sig.parameters)
        assert "service" not in params
        assert "repo" not in params
        assert "conn" not in params


class TestMainWindowNav:
    def test_ai_settings_nav_present(self, qtbot, repo, task_service, date_service,
                                     ai_config_service, prompt_registry,
                                     preview_service):
        from app.ui.main_window import MainWindow

        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: "2026-01-05",
            ai_config_service=ai_config_service,
            prompt_registry=prompt_registry,
            prompt_preview_service=preview_service,
        )
        qtbot.addWidget(w)
        assert w.nav_ai_btn.text() == "设置"
        assert w.ai_settings_page_index is not None
        w._switch_to_ai_settings()
        assert w.stack.currentIndex() == w.ai_settings_page_index
