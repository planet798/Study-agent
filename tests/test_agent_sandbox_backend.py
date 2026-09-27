"""Docker Sandbox command construction and limits using offline fake runners."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.agent.sandbox.backend import DockerSandboxBackend, SandboxExecutionUnavailable
from app.agent.sandbox.config import SandboxExecutionConfig
from app.agent.sandbox.workspace import SandboxWorkspace, SandboxWorkspaceError


class FakeRunner:
    def __init__(self, info=0, image=0):
        self.info = info
        self.image = image
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if command[1:3] == ["info", "--format"]:
            return SimpleNamespace(returncode=self.info, stdout="", stderr="")
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=self.image, stdout="", stderr="")
        if command[1:3] == ["rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected host command: {command!r}")


class FakeExecutionRunner:
    def __init__(self, stdout=b"program out", stderr=b"program err",
                 exit_code=0, timeout=False):
        self.stdout_data = stdout
        self.stderr_data = stderr
        self.exit_code = exit_code
        self.timeout = timeout
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        kwargs["stdout"].write(self.stdout_data)
        kwargs["stderr"].write(self.stderr_data)
        if self.timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=self.exit_code)


def _config(**overrides):
    values = {
        "enabled": True, "backend": "docker", "image": "python:3.12-slim",
        "timeout_seconds": 30, "memory_mb": 512, "cpus": 1.0,
        "pids_limit": 64,
    }
    values.update(overrides)
    return SandboxExecutionConfig(**values)


def test_docker_command_is_fixed_hardened_and_mounts_only_current_task(tmp_path):
    probe_runner = FakeRunner()
    execution_runner = FakeExecutionRunner()
    backend = DockerSandboxBackend(
        _config(), docker_path="/usr/bin/docker", run_runner=probe_runner,
        execution_runner=execution_runner,
    )
    assert backend.probe() is True
    workspace = SandboxWorkspace(42, tmp_path / "agent_workspaces", 1000)
    workspace.ensure_workspace()
    result = backend.run(workspace, ["python", "main.py"], cwd=".", stdin="input")

    command, kwargs = execution_runner.calls[0]
    assert kwargs["shell"] is False
    assert kwargs["input"] == b"input"
    assert "env" not in kwargs and "cwd" not in kwargs
    assert command[0:2] == ["/usr/bin/docker", "run"]
    assert "--rm" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert command[command.index("--memory") + 1] == "512m"
    assert command[command.index("--cpus") + 1] == "1"
    assert "--read-only" in command
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=64m"
    mount = command[command.index("--mount") + 1]
    assert mount == f"type=bind,source={workspace.task_root.resolve()},target=/workspace"
    assert command.count("--mount") == 1
    assert "docker.sock" not in " ".join(command)
    assert "--privileged" not in command and "--network host" not in command
    image_index = command.index("python:3.12-slim")
    assert command[image_index + 1:] == ["python", "main.py"]
    assert result == {
        "exit_code": 0, "stdout": "program out", "stderr": "program err",
        "timed_out": False, "stdout_truncated": False,
        "stderr_truncated": False,
    }
    assert [cmd[0][1:3] for cmd in probe_runner.commands] == [
        ["info", "--format"], ["image", "inspect"], ["rm", "-f"],
    ]
    assert all(call[1]["shell"] is False for call in probe_runner.commands)
    assert not any("pull" in call[0] for call in probe_runner.commands)


def test_docker_unavailable_or_image_missing_never_falls_back_to_host(tmp_path):
    execution_runner = FakeExecutionRunner()
    backend = DockerSandboxBackend(
        _config(), docker_path="/usr/bin/docker",
        run_runner=FakeRunner(info=1), execution_runner=execution_runner,
    )
    assert backend.probe() is False
    with pytest.raises(SandboxExecutionUnavailable):
        backend.run(SandboxWorkspace(1, tmp_path / "ws", 100), ["python", "x.py"])
    assert execution_runner.calls == []

    missing_image = DockerSandboxBackend(
        _config(), docker_path="/usr/bin/docker",
        run_runner=FakeRunner(info=0, image=1), execution_runner=execution_runner,
    )
    assert missing_image.probe() is False
    assert execution_runner.calls == []


def test_execution_timeout_reports_timeout_and_always_cleans_container(tmp_path):
    probe_runner = FakeRunner()
    execution_runner = FakeExecutionRunner(timeout=True)
    backend = DockerSandboxBackend(
        _config(timeout_seconds=1), docker_path="docker",
        run_runner=probe_runner, execution_runner=execution_runner,
    )
    assert backend.probe() is True
    result = backend.run(
        SandboxWorkspace(5, tmp_path / "ws", 100), ["python", "slow.py"]
    )
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    cleanup = probe_runner.commands[-1]
    assert cleanup[0][1:3] == ["rm", "-f"]
    assert cleanup[0][3].startswith("study-agent-sbx-")
    assert cleanup[1]["shell"] is False


def test_stdout_and_stderr_are_bounded_and_marked_truncated(tmp_path):
    backend = DockerSandboxBackend(
        _config(), docker_path="docker", run_runner=FakeRunner(),
        execution_runner=FakeExecutionRunner(stdout=b"x" * 200, stderr=b"y" * 200),
        max_output_chars=10,
    )
    assert backend.probe() is True
    result = backend.run(
        SandboxWorkspace(2, tmp_path / "ws", 100), ["python", "main.py"]
    )
    assert len(result["stdout"]) == 10
    assert len(result["stderr"]) == 10
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True


def test_invalid_argv_is_rejected_before_execution(tmp_path):
    execution_runner = FakeExecutionRunner()
    backend = DockerSandboxBackend(
        _config(), docker_path="docker", run_runner=FakeRunner(),
        execution_runner=execution_runner,
    )
    assert backend.probe() is True
    with pytest.raises(SandboxWorkspaceError):
        backend.run(SandboxWorkspace(1, tmp_path / "ws", 10), "python main.py")
    assert execution_runner.calls == []


def test_host_paths_and_docker_daemon_diagnostics_are_sanitized(tmp_path):
    root = tmp_path / "workspace"
    backend = DockerSandboxBackend(
        _config(), docker_path="/host/docker", run_runner=FakeRunner(),
        execution_runner=FakeExecutionRunner(
            stderr=(f"docker: Error response from daemon at {root}: "
                    "internal details").encode()
        ),
    )
    assert backend.probe() is True
    result = backend.run(SandboxWorkspace(3, root.parent, 100), ["python", "main.py"])
    assert result["stderr"] == "Sandbox execution failed."
    assert str(tmp_path) not in result["stderr"]
    assert "/host/docker" not in result["stderr"]
