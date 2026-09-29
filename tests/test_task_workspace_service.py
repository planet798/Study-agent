"""Workspace validation and non-destructive binding lifecycle."""

from pathlib import PureWindowsPath

import pytest

from app.database.task_workspace_repository import TaskWorkspaceRepository
from app.services.task_service import TaskNotFoundError
from app.services.workspace_service import (
    TaskWorkspaceService, canonical_local_directory, is_filesystem_root,
)


def test_task_validation_and_managed_directory_lifecycle(conn, repo, task_service, tmp_path, monkeypatch):
    import app.services.workspace_service as module

    root = tmp_path / "managed"
    monkeypatch.setattr(module, "default_sandbox_workspace_root", lambda: root)
    service = TaskWorkspaceService(TaskWorkspaceRepository(conn), task_service)
    for action in (service.get_view, service.use_managed, service.clear,
                   service.runtime_spec, service.resolve_open_path):
        with pytest.raises(TaskNotFoundError):
            action(123456)
    with pytest.raises(TaskNotFoundError):
        service.bind_local(123456, tmp_path)

    task = repo.create("Workspace task")
    assert service.get_view(task.id).kind == "none"
    assert service.runtime_spec(task.id).root is None
    view = service.use_managed(task.id)
    assert view.readable and view.writable and view.available
    assert view.display_path == str(root / f"task_{task.id}")
    assert service.managed_path(task.id) == root / f"task_{task.id}"
    assert not root.exists()  # opening a session/view never creates directories
    opened = service.resolve_open_path(task.id)
    (opened / "keep.md").write_text("keep", encoding="utf-8")
    assert service.runtime_spec(task.id).execution_allowed
    service.clear(task.id)
    assert (opened / "keep.md").read_text(encoding="utf-8") == "keep"
    assert service.get_view(task.id).kind == "none"
    service.use_managed(task.id)
    assert service.managed_path(task.id) == opened


def test_local_path_canonical_and_missing(conn, repo, task_service, tmp_path):
    service = TaskWorkspaceService(TaskWorkspaceRepository(conn), task_service)
    task = repo.create("Local task")
    project = tmp_path / "my-project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    view = service.bind_local(task.id, alias)
    assert view.display_path == str(project.resolve())
    assert view.label == "my-project"
    assert view.readable and not view.writable
    assert service.runtime_spec(task.id).root == project.resolve()
    project.rmdir()
    view = service.get_view(task.id)
    assert view.kind == "local" and not view.available
    assert view.display_path == str(project.resolve())
    spec = service.runtime_spec(task.id)
    assert spec.kind == "local" and spec.root is None and not spec.readable
    with pytest.raises(ValueError):
        service.resolve_open_path(task.id)
    assert not project.exists()


def test_local_path_rejections(conn, repo, task_service, tmp_path):
    service = TaskWorkspaceService(TaskWorkspaceRepository(conn), task_service)
    task = repo.create("Reject paths")
    for path in ("relative", "/", "\x00", "/" + "x" * 4097,
                 tmp_path / "missing", PureWindowsPath("C:\\")):
        with pytest.raises(ValueError):
            service.bind_local(task.id, path)
    assert service.get_view(task.id).kind == "none"
    assert is_filesystem_root(PureWindowsPath("C:\\"))
    assert is_filesystem_root("/")
    assert not is_filesystem_root(PureWindowsPath("D:\\Projects\\llm-sft-lora"))
    assert not is_filesystem_root("/home/user/project")
    assert not is_filesystem_root("/tmp/project")
    assert canonical_local_directory(tmp_path) == tmp_path.resolve()
