"""Compact workspace selector and MainWindow binding actions."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QUrl

from app.agent.session import AgentSessionService
from app.database.agent_repository import AgentRepository
from app.ui.agent_workspace_card import AgentWorkspaceCard
from app.ui.agent_workspace_page import AgentWorkspacePage
from app.ui.main_window import MainWindow


def _view(task_id=7, kind="none", path="", available=False):
    return SimpleNamespace(
        task_id=task_id, kind=kind, label=("Study-Agent 托管工作区" if kind == "managed"
                                                else "llm-sft-lora"),
        display_path=path, available=available,
        readable=available, writable=kind == "managed" and available,
    )


def _action(card, label):
    return next(a for a in card.menu.actions() if a.text() == label)


def test_compact_states_and_header_position(qtbot, repo):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    card.set_view(_view())
    card.show()
    assert [a.text() for a in card.menu.actions()] == ["使用托管工作区", "选择本地项目"]
    card.set_view(_view(kind="managed", available=True, path="/tmp/study-agent/task_7"))
    assert card.badge.text() == "可读写"
    assert _action(card, "打开文件夹")
    assert "/tmp/study-agent/task_7" not in card.selector.text()
    assert card.selector.toolTip() == "/tmp/study-agent/task_7"
    card.set_view(_view(kind="local", available=False, path="/private/missing"))
    assert not card.badge.isHidden()
    assert card.badge.text() == "不可用"
    assert _action(card, "重新选择本地项目")
    assert not any(a.text() == "打开文件夹" for a in card.menu.actions())
    task = repo.create(title="Study", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 3}, [], task, None, True, workspace_view=_view(task.id))
    assert page.context_layout.indexOf(page.capability_warning_banner) >= 0
    assert page.layout().itemAt(0).layout() is page.context_layout
    assert page.layout().indexOf(page.conversation_scroll) == 1
    assert page.workspace_card.parent() is page


def test_menu_signals_busy_and_teardown(qtbot):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    card.set_view(_view(task_id=19))
    actions = []
    card.managed_requested.connect(lambda task: actions.append(("managed", task)))
    card.local_requested.connect(lambda task: actions.append(("local", task)))
    _action(card, "使用托管工作区").trigger()
    _action(card, "选择本地项目").trigger()
    assert actions == [("managed", 19), ("local", 19)]
    card.set_busy(True)
    assert not card.selector.isEnabled()
    _action(card, "使用托管工作区").trigger()
    assert len(actions) == 2
    card.set_busy(False)
    card.show()
    card.menu.popup(card.mapToGlobal(QPoint(0, 0)))
    qtbot.waitUntil(card.menu.isVisible)
    card.close()
    assert not card.menu.isVisible()


def test_path_available_in_menu_and_clipboard_not_conversation(qtbot):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    path = "D:\\Projects\\" + ("nested\\" * 100)
    card.set_view(_view(kind="local", path=path, available=True))
    card.resize(540, 50)
    card.show()
    assert card.selector.toolTip() == path
    assert len(card.selector.text()) < 40
    _action(card, "复制路径").trigger()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == path


class _WorkspaceService:
    def __init__(self, base):
        self.base = base
        self.binding = {}
        self.calls = []

    def get_view(self, task_id):
        kind, path = self.binding.get(task_id, ("none", ""))
        return _view(task_id, kind, path, kind == "managed" or (kind == "local" and Path(path).is_dir()))

    def use_managed(self, task_id):
        self.calls.append(("managed", task_id))
        self.binding[task_id] = ("managed", str(self.base / f"task_{task_id}"))

    def bind_local(self, task_id, path):
        self.calls.append(("local", task_id, path))
        self.binding[task_id] = ("local", path)

    def clear(self, task_id):
        self.calls.append(("clear", task_id))
        del self.binding[task_id]

    def ensure_managed_directory(self, task_id):
        self.calls.append(("mkdir", task_id))
        (self.base / f"task_{task_id}").mkdir()

    def resolve_open_path(self, task_id):
        return Path(self.binding[task_id][1])


def test_main_window_binding_dialog_open_and_clear(qtbot, conn, repo, task_service, date_service, tmp_path, monkeypatch):
    task = repo.create(title="Workspace Task", scheduled_date="2026-01-05", source="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    service = _WorkspaceService(tmp_path)
    window = MainWindow(task_service, date_service, today_provider=lambda: "2026-01-05",
                        agent_session_service=sessions, agent_runtime_factory=lambda conn: None,
                        task_workspace_service=service)
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    card = window.agent_workspace_page.workspace_card
    _action(card, "使用托管工作区").trigger()
    assert card.workspace_kind == "managed"
    opened = []
    monkeypatch.setattr("app.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    _action(card, "打开文件夹").trigger()
    assert opened == [QUrl.fromLocalFile(str(tmp_path / f"task_{task.id}"))]
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: str(project))
    _action(card, "选择本地项目").trigger()
    assert card.workspace_kind == "local"
    assert card.selector.toolTip() == str(project)
    _action(card, "打开文件夹").trigger()
    assert opened[-1] == QUrl.fromLocalFile(str(project))
    project.rmdir()
    window._reload_agent_session(window.agent_workspace_page.current_session_id)
    assert not any(a.text() == "打开文件夹" for a in card.menu.actions())
    _action(card, "解除绑定").trigger()
    assert card.workspace_kind == "none"
    assert (tmp_path / f"task_{task.id}").is_dir()
    window.close()


def test_busy_and_approval_busy_lock_workspace_controls(qtbot, repo):
    task = repo.create(title="Busy", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True, workspace_view=_view(task.id))
    page.set_busy(True)
    assert not page.workspace_card.selector.isEnabled()
    page.set_busy(False)
    page.set_approval_busy(4, True)
    assert not page.workspace_card.selector.isEnabled()
    page.set_approval_busy(4, False)
    assert page.workspace_card.selector.isEnabled()
