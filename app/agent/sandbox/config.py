"""Operator-controlled Sandbox config; no DB and no model-controlled settings."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

_CONFIG_VERSION = 1
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")
DEFAULT_MAX_FILE_CHARS = 200_000
DEFAULT_MAX_OUTPUT_CHARS = 20_000


class SandboxConfigError(ValueError):
    """Sanitized, controlled local Sandbox configuration error."""


@dataclass(frozen=True)
class SandboxExecutionConfig:
    enabled: bool = False
    backend: str = "docker"
    image: str = "python:3.12-slim"
    timeout_seconds: int = 30
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64


@dataclass(frozen=True)
class SandboxConfig:
    version: int = 1
    enabled: bool = False
    file_tools: bool = True
    max_file_chars: int = DEFAULT_MAX_FILE_CHARS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    execution: SandboxExecutionConfig = SandboxExecutionConfig()


def default_sandbox_config_path() -> Path:
    override = os.environ.get("STUDY_AGENT_SANDBOX_CONFIG")
    if override:
        return Path(override).expanduser()
    root = Path(__file__).resolve().parents[3]
    return root / "data" / "sandbox.json"


def load_sandbox_config(path: str | Path | None = None) -> SandboxConfig:
    config_path = Path(path).expanduser() if path is not None else default_sandbox_config_path()
    if not config_path.exists():
        return SandboxConfig(enabled=False, file_tools=False)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SandboxConfigError("Sandbox configuration could not be read as valid JSON.") from None
    return parse_sandbox_config(raw)


def parse_sandbox_config(raw: object) -> SandboxConfig:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int:
        raise SandboxConfigError("Sandbox configuration must contain integer version 1.")
    if raw.get("version") != _CONFIG_VERSION:
        raise SandboxConfigError("Unsupported Sandbox configuration version.")
    if set(raw) - {
        "version", "enabled", "file_tools", "max_file_chars",
        "max_output_chars", "execution",
    }:
        raise SandboxConfigError("Sandbox configuration contains unsupported fields.")
    enabled = _bool(raw, "enabled", default=False)
    file_tools = _bool(raw, "file_tools", default=True)
    max_file_chars = _int_range(
        raw.get("max_file_chars", DEFAULT_MAX_FILE_CHARS), 1, 2_000_000,
        "max_file_chars",
    )
    max_output_chars = _int_range(
        raw.get("max_output_chars", DEFAULT_MAX_OUTPUT_CHARS), 1, 200_000,
        "max_output_chars",
    )

    execution_raw = raw.get("execution", {})
    if not isinstance(execution_raw, dict):
        raise SandboxConfigError("Sandbox execution config must be an object.")
    if set(execution_raw) - {
        "enabled", "backend", "image", "timeout_seconds", "memory_mb",
        "cpus", "pids_limit",
    }:
        raise SandboxConfigError("Sandbox execution config contains unsupported fields.")
    execution_enabled = _bool(execution_raw, "enabled", default=False)
    backend = execution_raw.get("backend", "docker")
    if backend != "docker":
        raise SandboxConfigError("Only the Docker Sandbox backend is supported.")
    image = execution_raw.get("image", "python:3.12-slim")
    if (not isinstance(image, str) or not _IMAGE_RE.fullmatch(image)
            or image.startswith("-")):
        raise SandboxConfigError("Sandbox Docker image reference is invalid.")
    timeout = _int_range(
        execution_raw.get("timeout_seconds", 30), 1, 300, "timeout_seconds"
    )
    memory = _int_range(execution_raw.get("memory_mb", 512), 64, 4096, "memory_mb")
    cpus = execution_raw.get("cpus", 1.0)
    if (isinstance(cpus, bool) or not isinstance(cpus, (int, float))
            or not math.isfinite(float(cpus)) or not 0.1 <= float(cpus) <= 8.0):
        raise SandboxConfigError("Sandbox cpus must be between 0.1 and 8.0.")
    pids = _int_range(execution_raw.get("pids_limit", 64), 1, 1024, "pids_limit")
    if execution_enabled and not enabled:
        raise SandboxConfigError("Sandbox execution cannot be enabled while Sandbox is disabled.")

    return SandboxConfig(
        version=_CONFIG_VERSION,
        enabled=enabled,
        file_tools=file_tools,
        max_file_chars=max_file_chars,
        max_output_chars=max_output_chars,
        execution=SandboxExecutionConfig(
            enabled=execution_enabled,
            backend=backend,
            image=image,
            timeout_seconds=timeout,
            memory_mb=memory,
            cpus=float(cpus),
            pids_limit=pids,
        ),
    )


def _bool(data: dict, key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise SandboxConfigError(f"Sandbox {key} must be boolean.")
    return value


def _int_range(value, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SandboxConfigError(f"Sandbox {name} is outside the permitted range.")
    return value
