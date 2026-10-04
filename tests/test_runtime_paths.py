from pathlib import Path
import sys

import pytest

from app import runtime_paths as paths


def test_source_paths_preserve_existing_layout(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    root = Path(__file__).resolve().parents[1]
    assert paths.data_dir() == root / "data"
    assert paths.logs_dir() == root / "data/logs"
    assert paths.context_path() == root / "docs/career_context.json"
    assert paths.notes_dir() == root / "docs/obsidian"


def test_frozen_resources_and_data_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "安装 目录/_internal"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "用户 目录"))
    monkeypatch.setenv("STUDY_AGENT_NODE", "untrusted-node")
    root = tmp_path / "用户 目录/StudyAgent"
    assert paths.data_dir() == root / "data"
    assert paths.logs_dir() == root / "logs"
    assert paths.context_path() == root / "data/career_context.json"
    assert paths.notes_dir() == root / "notes"
    assert paths.node_executable() == str(tmp_path / "安装 目录/_internal/runtime/node.exe")
    assert not root.exists()


def test_missing_windows_user_directory_fails_closed(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError):
        paths.data_dir()


def test_source_node_override(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("STUDY_AGENT_NODE", "/operator/node")
    assert paths.node_executable() == "/operator/node"
