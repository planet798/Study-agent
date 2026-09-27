"""Local MCP server configuration (no secrets stored, Agent-5)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

_CONFIG_VERSION = 1
_SERVER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MCPConfigError(ValueError):
    """Controlled local MCP config validation/load error (never includes values)."""


@dataclass(frozen=True)
class MCPServerConfig:
    key: str
    transport: str
    enabled: bool
    allowed_tools: tuple[str, ...]
    command: str = field(default="", repr=False)
    args: tuple[str, ...] = field(default=(), repr=False)
    env_from_process: tuple[str, ...] = ()
    url: str = field(default="", repr=False)


@dataclass(frozen=True)
class MCPConfig:
    version: int
    servers: tuple[MCPServerConfig, ...]


def default_mcp_config_path() -> Path:
    """Resolve operator-controlled config path; default is ignored local data."""
    override = os.environ.get("STUDY_AGENT_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    # app/agent/mcp/config.py -> repository root / data / mcp_servers.json
    repository_root = Path(__file__).resolve().parents[3]
    return repository_root / "data" / "mcp_servers.json"


def load_mcp_config(path: str | Path | None = None) -> MCPConfig:
    """Load and strictly validate v1 JSON config; missing file means MCP disabled."""
    config_path = Path(path).expanduser() if path is not None else default_mcp_config_path()
    if not config_path.exists():
        return MCPConfig(version=_CONFIG_VERSION, servers=())
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MCPConfigError("MCP configuration could not be read as valid JSON.") from None
    return parse_mcp_config(raw)


def parse_mcp_config(raw: object) -> MCPConfig:
    """Validate untrusted config data without echoing commands, URLs, or env values."""
    if not isinstance(raw, dict) or type(raw.get("version")) is not int:
        raise MCPConfigError("MCP configuration must contain integer version 1.")
    if raw.get("version") != _CONFIG_VERSION:
        raise MCPConfigError("Unsupported MCP configuration version.")
    if set(raw) - {"version", "servers"}:
        raise MCPConfigError("MCP configuration contains unsupported fields.")
    entries = raw.get("servers")
    if not isinstance(entries, list):
        raise MCPConfigError("MCP configuration servers must be a list.")

    servers: list[MCPServerConfig] = []
    seen_keys: set[str] = set()
    for entry in entries:
        servers.append(_parse_server(entry, seen_keys))
    return MCPConfig(version=_CONFIG_VERSION, servers=tuple(servers))


def _parse_server(entry: object, seen_keys: set[str]) -> MCPServerConfig:
    if not isinstance(entry, dict):
        raise MCPConfigError("Each MCP server entry must be an object.")
    key = entry.get("key")
    if not isinstance(key, str) or not _SERVER_KEY_RE.fullmatch(key):
        raise MCPConfigError("MCP server key is invalid.")
    if key in seen_keys:
        raise MCPConfigError("Duplicate MCP server key.")
    seen_keys.add(key)

    transport = entry.get("transport")
    if transport not in ("stdio", "streamable_http"):
        raise MCPConfigError("MCP server transport is unsupported.")
    enabled = entry.get("enabled")
    if type(enabled) is not bool:
        raise MCPConfigError("MCP server enabled must be boolean.")

    allowed_raw = entry.get("allowed_tools", [])
    if not isinstance(allowed_raw, list):
        raise MCPConfigError("allowed_tools must be a list.")
    allowed: list[str] = []
    for name in allowed_raw:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise MCPConfigError("allowed_tools contains an invalid tool name.")
        if name == "*":
            raise MCPConfigError("Wildcard MCP tool authorization is forbidden.")
        if "\x00" in name:
            raise MCPConfigError("allowed_tools contains an invalid tool name.")
        if name in allowed:
            raise MCPConfigError("Duplicate allowed MCP tool name.")
        allowed.append(name)
    if enabled and not allowed:
        raise MCPConfigError("Enabled MCP servers require explicit allowed_tools.")

    common_fields = {"key", "transport", "enabled", "allowed_tools"}
    if transport == "stdio":
        permitted = common_fields | {"command", "args", "env_from_process"}
        if set(entry) - permitted:
            raise MCPConfigError("stdio MCP server contains unsupported fields.")
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip() or "\x00" in command:
            raise MCPConfigError("stdio MCP server requires a valid command.")
        args_raw = entry.get("args", [])
        if not isinstance(args_raw, list) or any(
            not isinstance(arg, str) or "\x00" in arg for arg in args_raw
        ):
            raise MCPConfigError("stdio MCP server args must be strings.")
        env_raw = entry.get("env_from_process", [])
        if not isinstance(env_raw, list):
            raise MCPConfigError("env_from_process must be a list of environment names.")
        env_names: list[str] = []
        for env_name in env_raw:
            if not isinstance(env_name, str) or not _ENV_NAME_RE.fullmatch(env_name):
                raise MCPConfigError("env_from_process contains an invalid name.")
            if env_name in env_names:
                raise MCPConfigError("Duplicate env_from_process name.")
            env_names.append(env_name)
        return MCPServerConfig(
            key=key,
            transport=transport,
            enabled=enabled,
            allowed_tools=tuple(allowed),
            command=command,
            args=tuple(args_raw),
            env_from_process=tuple(env_names),
        )

    permitted = common_fields | {"url"}
    if set(entry) - permitted:
        raise MCPConfigError("streamable_http MCP server contains unsupported fields.")
    url = entry.get("url")
    if (not isinstance(url, str) or not url.strip() or url != url.strip()
            or "\x00" in url):
        raise MCPConfigError("streamable_http MCP server requires a valid URL.")
    try:
        parsed = urlparse(url)
        _port = parsed.port
    except ValueError:
        raise MCPConfigError("streamable_http MCP server URL is invalid.") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment):
        raise MCPConfigError("streamable_http MCP server URL is invalid.")
    return MCPServerConfig(
        key=key,
        transport=transport,
        enabled=enabled,
        allowed_tools=tuple(allowed),
        url=url,
    )
