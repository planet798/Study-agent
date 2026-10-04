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


def test_frozen_business_modules_share_user_context_and_skip_application_git(tmp_path):
    import json
    import os
    import subprocess

    code = """
import sys, json
sys.frozen = True
from app.ai.long_term_context import DEFAULT_CONTEXT_PATH
from app.services.skill_service import DEFAULT_CAREER_CONTEXT_PATH as skill
from app.services.jd_service import DEFAULT_CAREER_CONTEXT_PATH as jd
from app.services.learning_outcome_service import LearningOutcomeService
print(json.dumps({'paths': [str(DEFAULT_CONTEXT_PATH), str(skill), str(jd)],
                  'git': LearningOutcomeService(None).current_git_commit()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "LOCALAPPDATA": str(tmp_path)},
        capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["paths"] == [str(tmp_path / "StudyAgent/data/career_context.json")] * 3
    assert report["git"] is None
    assert not (tmp_path / "StudyAgent").exists()
