"""Workspace binding card and MainWindow's user-only binding actions."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt, QUrl

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


def test_card_states_and_relative_position(qtbot, repo):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    card.set_view(_view())
    card.show()
    assert card.managed_button.isVisible() and card.local_button.isVisible()
    assert not card.open_button.isVisible()
    assert "尚未" in card.state_label.text()
    card.set_view(_view(kind="managed", available=True, path="/tmp/study-agent/task_7"))
    assert card.badge.text() == "可读写"
    assert card.open_button.isVisible() and card.replace_button.isVisible()
    assert card.path_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    card.set_view(_view(kind="local", available=True, path="D:\\Projects\\llm-sft-lora"))
    assert card.badge.text() == "本地项目 · 只读"
    assert card.path_label.text() == "D:\\Projects\\llm-sft-lora"
    card.set_view(_view(kind="local", available=False, path="/private/missing"))
    assert card.reselect_button.isVisible() and card.clear_button.isVisible()
    assert not card.open_button.isVisible()
    assert "重新选择" in card.hint_label.text()

    task = repo.create(title="Study", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 3}, [], task, None, True, workspace_view=_view(task.id))
    assert page.layout().indexOf(page.workspace_card) == page.layout().indexOf(page.task_context_card) + 1
    assert page.layout().indexOf(page.workspace_card) < page.layout().indexOf(page.conversation_scroll)


def test_card_signals_and_busy_disable(qtbot):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    card.set_view(_view(task_id=19))
    actions = []
    card.managed_requested.connect(lambda task: actions.append(("managed", task)))
    card.local_requested.connect(lambda task: actions.append(("local", task)))
    card.managed_button.click()
    card.local_button.click()
    assert actions == [("managed", 19), ("local", 19)]
    card.set_busy(True)
    assert all(not button.isEnabled() for button in card._buttons)
    card.managed_button.click()
    assert len(actions) == 2
    card.set_busy(False)
    assert card.managed_button.isEnabled()
    card.set_view(_view(task_id=19, kind="managed", available=True))
    card.replace_button.menu().actions()[1].trigger()
    assert actions[-1] == ("local", 19)


def test_replace_popup_is_closed_with_card(qtbot):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    card.set_view(_view(kind="managed", available=True))
    card.show()
    menu = card.replace_button.menu()
    menu.popup(card.mapToGlobal(QPoint(0, 0)))
    qtbot.waitUntil(menu.isVisible)
    card.close()
    assert not menu.isVisible()


def test_long_path_is_wrapped_selectable_plain_text(qtbot):
    card = AgentWorkspaceCard()
    qtbot.addWidget(card)
    path = "/tmp/<img src=x>/" + ("nested/" * 100)
    card.set_view(_view(kind="local", path=path, available=True))
    card.resize(540, 280)
    card.show()
    assert card.path_label.text() == path
    assert card.path_label.textFormat() == Qt.TextFormat.PlainText
    assert card.path_label.wordWrap()
    assert card.path_label.minimumWidth() == 0


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


def test_main_window_binding_dialog_open_and_clear(
    qtbot, conn, repo, task_service, date_service, tmp_path, monkeypatch,
):
    task = repo.create(title="Workspace Task", scheduled_date="2026-01-05", source="manual")
    sessions = AgentSessionService(AgentRepository(conn), task_service)
    service = _WorkspaceService(tmp_path)
    window = MainWindow(
        task_service, date_service, today_provider=lambda: "2026-01-05",
        agent_session_service=sessions, agent_runtime_factory=lambda conn: None,
        task_workspace_service=service,
    )
    qtbot.addWidget(window)
    window._on_start_study(task.id)
    card = window.agent_workspace_page.workspace_card
    assert card.workspace_kind == "none"
    assert service.calls == []
    card.managed_button.click()
    assert card.workspace_kind == "managed"
    assert not (tmp_path / f"task_{task.id}").exists()
    opened = []
    monkeypatch.setattr("app.ui.main_window.QDesktopServices.openUrl", lambda url: opened.append(url) or True)
    card.open_button.click()
    assert opened == [QUrl.fromLocalFile(str(tmp_path / f"task_{task.id}"))]
    assert (tmp_path / f"task_{task.id}").is_dir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: "")
    card.replace_button.menu().actions()[1].trigger()
    assert card.workspace_kind == "managed"  # cancel leaves binding unchanged
    monkeypatch.setattr("app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: str(project))
    card.replace_button.menu().actions()[1].trigger()
    assert card.workspace_kind == "local"
    assert card.path_label.text() == str(project)
    card.open_button.click()
    assert opened[-1] == QUrl.fromLocalFile(str(project))
    project.rmdir()
    window._reload_agent_session(window.agent_workspace_page.current_session_id)
    assert not card.reselect_button.isHidden()
    assert not card.open_button.isVisible()
    card.clear_button.click()
    assert card.workspace_kind == "none"
    assert (tmp_path / f"task_{task.id}").is_dir()
    window._on_start_study(task.id)
    assert card.workspace_kind == "none"
    window.close()


def test_busy_and_approval_busy_lock_workspace_controls(qtbot, repo):
    task = repo.create(title="Busy", scheduled_date="2026-01-05", source="manual")
    page = AgentWorkspacePage()
    qtbot.addWidget(page)
    page.load_session({"id": 1}, [], task, None, True, workspace_view=_view(task.id))
    page.set_busy(True)
    assert not page.workspace_card.managed_button.isEnabled()
    page.set_busy(False)
    page.set_approval_busy(4, True)
    assert not page.workspace_card.managed_button.isEnabled()
    page.set_approval_busy(4, False)
    assert page.workspace_card.managed_button.isEnabled()
