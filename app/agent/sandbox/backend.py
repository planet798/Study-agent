"""Docker-only execution backend for a single Task workspace.

The host may invoke only the fixed Docker CLI using argument arrays and
``shell=False``. Model ``argv`` is placed after the configured image and runs
inside the constrained container; there is no host subprocess fallback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Callable

from .config import SandboxExecutionConfig
from .workspace import SandboxWorkspace, SandboxWorkspaceError

MAX_ARG_CHARS = 4096
MAX_ARGV_CHARS = 16_384
MAX_STDIN_CHARS = 20_000
_DOCKER_TMPFS = "/tmp:rw,nosuid,nodev,size=64m"


class SandboxExecutionUnavailable(Exception):
    """Docker backend is disabled or its configured local image is unavailable."""


class DockerSandboxBackend:
    """Bounded Docker runner; never executes model argv directly on the host."""

    def __init__(
        self,
        config: SandboxExecutionConfig,
        *,
        docker_path: str | None = None,
        run_runner: Callable = subprocess.run,
        execution_runner: Callable = subprocess.run,
        max_output_chars: int = 20_000,
    ):
        self.config = config
        if isinstance(max_output_chars, bool) or int(max_output_chars) < 1:
            raise ValueError("max_output_chars must be positive")
        self.max_output_chars = int(max_output_chars)
        self.docker_path = docker_path if docker_path is not None else shutil.which("docker")
        self._run = run_runner
        self._execute = execution_runner
        # Probe lazily from SandboxProvider.open_turn, after the user message and
        # authoritative Context/Skill snapshot have been persisted/constructed.
        self.available = False
        self._probed = False

    def probe(self) -> bool:
        """Require Docker daemon and configured image already present locally."""
        self._probed = True
        if not self.config.enabled or not self.docker_path:
            self.available = False
            return False
        try:
            info = self._run(
                [self.docker_path, "info", "--format", "{{.ServerVersion}}"],
                shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            if getattr(info, "returncode", 1) != 0:
                self.available = False
                return False
            image = self._run(
                [self.docker_path, "image", "inspect", self.config.image],
                shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            self.available = getattr(image, "returncode", 1) == 0
            return self.available
        except Exception:  # noqa: BLE001 - probe is optional and sanitized
            self.available = False
            return False

    def run(
        self,
        workspace: SandboxWorkspace,
        argv: list[str],
        cwd: str = ".",
        stdin: str = "",
    ) -> dict:
        if not self.available or not self.docker_path:
            raise SandboxExecutionUnavailable("Sandbox execution is unavailable.")
        checked_argv = _validate_argv(argv)
        if not isinstance(stdin, str) or len(stdin) > MAX_STDIN_CHARS or "\x00" in stdin:
            raise SandboxWorkspaceError("Sandbox stdin is invalid or too large.")
        workspace_root = workspace.ensure_workspace().resolve(strict=True)
        if any(ch in str(workspace_root) for ch in (",", "\n", "\r", "\x00")):
            raise SandboxExecutionUnavailable("Sandbox path is unsupported by Docker.")
        cwd_path, cwd_relative = workspace.resolve_relative(
            cwd, allow_root=True, must_exist=True
        )
        if not cwd_path.is_dir():
            raise SandboxWorkspaceError("Sandbox cwd must be a workspace directory.")
        container_cwd = (
            "/workspace" if cwd_relative == "."
            else f"/workspace/{cwd_relative}"
        )
        container_name = f"study-agent-sbx-{uuid.uuid4().hex}"
        command = self._build_command(
            container_name=container_name,
            workspace_root=workspace_root,
            container_cwd=container_cwd,
            argv=checked_argv,
        )

        stdout = bytearray()
        stderr = bytearray()
        stdout_cut = threading.Event()
        stderr_cut = threading.Event()
        reader_threads: list[threading.Thread] = []
        output_readers = []
        output_writers = []
        timed_out = False
        exit_code: int | None = None
        try:
            # Pipes are drained by bounded reader threads; subprocess.run never
            # accumulates unbounded stdout/stderr in memory.
            stdout_reader, stdout_writer = _open_pipe()
            stderr_reader, stderr_writer = _open_pipe()
            output_readers.extend((stdout_reader, stderr_reader))
            output_writers.extend((stdout_writer, stderr_writer))
            byte_limit = self.max_output_chars * 4 + 4
            reader_threads = [
                threading.Thread(
                    target=_drain_pipe,
                    args=(stdout_reader, stdout, byte_limit, stdout_cut),
                    daemon=True,
                ),
                threading.Thread(
                    target=_drain_pipe,
                    args=(stderr_reader, stderr, byte_limit, stderr_cut),
                    daemon=True,
                ),
            ]
            for thread in reader_threads:
                thread.start()
            try:
                completed = self._execute(
                    command,
                    shell=False,
                    input=stdin.encode("utf-8"),
                    stdout=stdout_writer,
                    stderr=stderr_writer,
                    timeout=self.config.timeout_seconds,
                    check=False,
                )
                exit_code = int(completed.returncode)
            except subprocess.TimeoutExpired:
                # subprocess.run kills and waits for its Docker CLI process.
                timed_out = True
            except Exception:  # noqa: BLE001 - do not expose host/Docker exception
                raise SandboxExecutionUnavailable("Sandbox execution failed.") from None
        except SandboxWorkspaceError:
            raise
        except SandboxExecutionUnavailable:
            raise
        except Exception:  # noqa: BLE001 - pipe/host errors are sanitized
            raise SandboxExecutionUnavailable("Sandbox execution failed.") from None
        finally:
            for stream in output_writers:
                try:
                    stream.close()
                except OSError:
                    pass
            for thread in reader_threads:
                thread.join(timeout=3)
            for stream in output_readers:
                try:
                    stream.close()
                except OSError:
                    pass
            # --rm is supplemented by best-effort cleanup, including timeout/error.
            self._cleanup_container(container_name)

        stdout_text, stdout_was_cut = _decode_bounded(stdout, self.max_output_chars)
        stderr_text, stderr_was_cut = _decode_bounded(stderr, self.max_output_chars)
        return {
            "exit_code": exit_code,
            "stdout": _sanitize_docker_output(
                stdout_text, workspace_root, self.docker_path, container_name
            ),
            "stderr": _sanitize_docker_output(
                stderr_text, workspace_root, self.docker_path, container_name
            ),
            "timed_out": timed_out,
            "stdout_truncated": stdout_was_cut or stdout_cut.is_set(),
            "stderr_truncated": stderr_was_cut or stderr_cut.is_set(),
        }

    def _build_command(
        self, *, container_name: str, workspace_root: Path,
        container_cwd: str, argv: list[str],
    ) -> list[str]:
        command = [
            self.docker_path,
            "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.config.pids_limit),
            "--memory", f"{self.config.memory_mb}m",
            "--cpus", f"{self.config.cpus:g}",
            "--read-only",
            "--tmpfs", _DOCKER_TMPFS,
            "--workdir", container_cwd,
            "--mount", f"type=bind,source={workspace_root},target=/workspace",
        ]
        if os.name == "posix" and hasattr(os, "getuid") and hasattr(os, "getgid"):
            command.extend(("--user", f"{os.getuid()}:{os.getgid()}"))
        command.extend((self.config.image, *argv))
        return command

    def _cleanup_container(self, container_name: str) -> None:
        if not self.docker_path:
            return
        try:
            self._run(
                [self.docker_path, "rm", "-f", container_name],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:  # noqa: BLE001 - best effort, don't leak diagnostics
            pass


def _open_pipe():
    read_fd, write_fd = os.pipe()
    try:
        reader = os.fdopen(read_fd, "rb", buffering=0)
        writer = os.fdopen(write_fd, "wb", buffering=0)
        return reader, writer
    except Exception:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _validate_argv(argv) -> list[str]:
    if not isinstance(argv, list) or not argv:
        raise SandboxWorkspaceError("argv must be a non-empty string list.")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg
           or len(arg) > MAX_ARG_CHARS for arg in argv):
        raise SandboxWorkspaceError("argv contains an invalid argument.")
    if sum(len(arg) for arg in argv) > MAX_ARGV_CHARS:
        raise SandboxWorkspaceError("argv exceeds the configured size limit.")
    return list(argv)


def _drain_pipe(stream, target: bytearray, limit: int, truncated: threading.Event) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = limit - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                truncated.set()
    except OSError:
        return


def _decode_bounded(data: bytearray, max_chars: int) -> tuple[str, bool]:
    text = bytes(data).decode("utf-8", errors="replace")
    was_cut = len(text) > max_chars
    return text[:max_chars], was_cut


def _sanitize_docker_output(
    text: str, workspace_root: Path, docker_path: str, container_name: str
) -> str:
    """Avoid returning host paths, executable details, or daemon diagnostics."""
    lower = text.lower()
    docker_errors = (
        "cannot connect to the docker daemon", "error response from daemon",
        "docker: error", "no such image", "pull access denied",
        "oci runtime create failed", "failed to create shim",
    )
    if any(marker in lower for marker in docker_errors):
        return "Sandbox execution failed."
    output = text.replace(str(workspace_root), "<task-workspace>")
    output = output.replace(container_name, "<sandbox-container>")
    if docker_path:
        output = output.replace(docker_path, "docker")
    return output
