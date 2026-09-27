"""Task workspace isolation, relative-path checks, links and bounded text files."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.sandbox.workspace import (
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxWorkspace,
    SandboxWorkspaceError,
)


def _workspace(tmp_path, task_id=7, max_chars=200000):
    return SandboxWorkspace(task_id, tmp_path / "agent_workspaces", max_chars)


@pytest.mark.parametrize("path", [
    "../secret", "a/../../secret", "/etc/passwd", "C:\\Windows\\win.ini",
    "D:/projects/repo", "\\\\server\\share", "file:///etc/passwd", "~/.ssh/id_rsa",
    "bad\x00path", "", "a\\b", "C:relative",
])
def test_path_resolver_rejects_absolute_traversal_and_uri_paths(tmp_path, path):
    workspace = _workspace(tmp_path)
    with pytest.raises(SandboxPathError):
        workspace.resolve_relative(path)


def test_task_workspace_is_task_id_scoped_and_paths_are_relative(tmp_path):
    task_a = _workspace(tmp_path, task_id=42)
    task_b = _workspace(tmp_path, task_id=43)
    # Parent directories are explicitly created using the Sandbox tool operation.
    task_a.make_directory("src")
    task_a.write_text_file("src/main.py", "print('A')")
    assert task_a.task_root.name == "task_42"
    assert task_b.task_root.name == "task_43"
    assert task_a.read_text_file("src/main.py")["content"] == "print('A')"
    assert task_b.list_files(".")["entries"] == []
    assert "task_42" not in str(task_a.list_files("."))


def test_file_write_is_create_only_by_default_and_atomic_overwrite_is_explicit(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.write_text_file("note.txt", "first")
    with pytest.raises(SandboxWorkspaceError):
        workspace.write_text_file("note.txt", "second")
    result = workspace.write_text_file("note.txt", "second", overwrite=True)
    assert result["overwritten"] is True
    assert workspace.read_text_file("note.txt")["content"] == "second"
    assert not list(workspace.task_root.glob(".sandbox-write-*"))


def test_directory_creation_is_idempotent_and_files_are_listed_one_level(tmp_path):
    workspace = _workspace(tmp_path)
    assert workspace.make_directory("nested/folder")["created"] is True
    assert workspace.make_directory("nested/folder")["created"] is True
    workspace.write_text_file("nested/folder/a.txt", "A")
    listing = workspace.list_files("nested/folder")
    assert listing == {
        "path": "nested/folder",
        "entries": [{"name": "a.txt", "type": "file", "size": 1}],
        "truncated": False,
    }


def test_directory_listing_is_bounded_and_marks_symlinks_without_following(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace.ensure_workspace()
    for i in range(205):
        (root / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "host-secret.txt"
    outside.write_text("do not read", encoding="utf-8")
    link = root / "host-link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None

    listing = workspace.list_files(".")
    assert len(listing["entries"]) == 200
    assert listing["truncated"] is True
    if link is not None:
        # Put the link among the first lexical entries, then list it directly.
        try:
            workspace.read_text_file("host-link")
        except SandboxPathError:
            pass
        else:
            pytest.fail("sandbox must reject symlink reads")
        assert str(tmp_path) not in str(listing)


def test_symlink_inside_workspace_cannot_escape_to_host(tmp_path):
    workspace = _workspace(tmp_path)
    root = workspace.ensure_workspace()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(SandboxPathError):
        workspace.read_text_file("link/secret.txt")
    assert any(entry["type"] == "symlink" for entry in workspace.list_files(".")["entries"])


def test_workspace_root_symlink_or_junction_is_rejected(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    workspace.base_root.mkdir(parents=True)
    task_root = workspace.base_root / "task_7"
    task_root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxPathError):
        workspace.ensure_workspace()

    # On Windows a junction may not satisfy Path.is_symlink(); test its explicit
    # detection branch where pathlib exposes is_junction().
    if hasattr(Path, "is_junction"):
        other = _workspace(tmp_path / "junction-test", task_id=11)
        other.base_root.mkdir(parents=True)
        monkeypatch.setattr(Path, "is_junction", lambda self: self == other.task_root)
        with pytest.raises(SandboxPathError):
            other.ensure_workspace()


def test_utf8_read_write_and_binary_file_rejection(tmp_path):
    workspace = _workspace(tmp_path, max_chars=20)
    workspace.write_text_file("hello.txt", "学习 Agent")
    assert workspace.read_text_file("hello.txt")["content"] == "学习 Agent"
    binary = workspace.ensure_workspace() / "image.bin"
    binary.write_bytes(b"\x00\xff\x00")
    with pytest.raises(SandboxWorkspaceError, match="UTF-8"):
        workspace.read_text_file("image.bin")


def test_file_size_limits_and_bounded_read(tmp_path):
    workspace = _workspace(tmp_path, max_chars=10)
    with pytest.raises(SandboxFileTooLargeError):
        workspace.write_text_file("large.txt", "x" * 11)
    path = workspace.ensure_workspace() / "large.txt"
    path.write_text("x" * 1000, encoding="utf-8")
    result = workspace.read_text_file("large.txt")
    assert result["content"] == "x" * 10
    assert result["truncated"] is True


def test_task_workspace_identity_requires_positive_non_boolean_integer(tmp_path):
    for task_id in (0, -1, True, "42"):
        with pytest.raises(SandboxPathError):
            SandboxWorkspace(task_id, tmp_path / "ws", 100)
