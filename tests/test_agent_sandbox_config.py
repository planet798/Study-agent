"""Agent-6 local Sandbox config validation and fail-closed defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.sandbox.config import (
    SandboxConfigError,
    default_sandbox_config_path,
    load_sandbox_config,
    parse_sandbox_config,
)


def test_missing_config_disables_sandbox(tmp_path):
    config = load_sandbox_config(tmp_path / "missing.json")
    assert config.enabled is False
    assert config.file_tools is False
    assert config.execution.enabled is False


def test_default_path_and_environment_override(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDY_AGENT_SANDBOX_CONFIG", raising=False)
    assert default_sandbox_config_path() == (
        Path(__file__).resolve().parents[1] / "data" / "sandbox.json"
    )
    override = tmp_path / "operator-sandbox.json"
    override.write_text('{"version":1,"enabled":false}', encoding="utf-8")
    monkeypatch.setenv("STUDY_AGENT_SANDBOX_CONFIG", str(override))
    assert default_sandbox_config_path() == override
    assert load_sandbox_config().enabled is False


def test_valid_file_tools_config_execution_disabled_by_default():
    config = parse_sandbox_config({"version": 1, "enabled": True})
    assert config.enabled is True
    assert config.file_tools is True
    assert config.max_file_chars == 200000
    assert config.max_output_chars == 20000
    assert config.execution.enabled is False
    assert config.execution.backend == "docker"


def test_valid_docker_config():
    config = parse_sandbox_config({
        "version": 1,
        "enabled": True,
        "file_tools": True,
        "max_file_chars": 100000,
        "max_output_chars": 10000,
        "execution": {
            "enabled": True, "backend": "docker", "image": "python:3.12-slim",
            "timeout_seconds": 12, "memory_mb": 256, "cpus": 1.5,
            "pids_limit": 32,
        },
    })
    assert config.execution.enabled is True
    assert config.execution.timeout_seconds == 12
    assert config.execution.memory_mb == 256
    assert config.execution.cpus == 1.5
    assert config.execution.pids_limit == 32


@pytest.mark.parametrize("raw", [
    {"version": 2, "enabled": True},
    {"version": 1, "enabled": "yes"},
    {"version": 1, "enabled": True, "file_tools": 1},
    {"version": 1, "enabled": True, "max_file_chars": 0},
    {"version": 1, "enabled": True, "max_file_chars": 2_000_001},
    {"version": 1, "enabled": True, "max_output_chars": 200_001},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "backend": "host"}},
    {"version": 1, "enabled": False,
     "execution": {"enabled": True, "backend": "docker"}},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "image": "--privileged"}},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "timeout_seconds": 301}},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "memory_mb": 32}},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "cpus": float("nan")}},
    {"version": 1, "enabled": True,
     "execution": {"enabled": True, "pids_limit": 0}},
])
def test_invalid_config_is_controlled_and_rejected(raw):
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config(raw)


def test_invalid_json_error_does_not_echo_config_contents(tmp_path):
    path = tmp_path / "sandbox.json"
    path.write_text('{"version":1,"secret":"not-for-logs"', encoding="utf-8")
    with pytest.raises(SandboxConfigError) as exc:
        load_sandbox_config(path)
    assert "not-for-logs" not in str(exc.value)
