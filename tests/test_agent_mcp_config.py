"""MCP local config validation and installed official SDK smoke test."""

from __future__ import annotations

import json

import pytest

from app.agent.mcp.config import (
    MCPConfigError,
    default_mcp_config_path,
    load_mcp_config,
    parse_mcp_config,
)


def test_missing_config_means_mcp_disabled(tmp_path):
    config = load_mcp_config(tmp_path / "missing.json")
    assert config.version == 1
    assert config.servers == ()


def test_default_config_path_and_environment_override(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("STUDY_AGENT_MCP_CONFIG", raising=False)
    repository_root = Path(__file__).resolve().parents[1]
    assert default_mcp_config_path() == repository_root / "data" / "mcp_servers.json"

    target = tmp_path / "operator.json"
    target.write_text('{"version":1,"servers":[]}', encoding="utf-8")
    monkeypatch.setenv("STUDY_AGENT_MCP_CONFIG", str(target))
    assert default_mcp_config_path() == target
    assert load_mcp_config().servers == ()


def test_valid_stdio_and_streamable_http_config():
    cfg = parse_mcp_config({
        "version": 1,
        "servers": [
            {
                "key": "docs", "enabled": True, "transport": "stdio",
                "command": "python", "args": ["path/to/server.py"],
                "env_from_process": ["DOCS_TOKEN"],
                "allowed_tools": ["search", "lookup-symbol"],
            },
            {
                "key": "local_http", "enabled": False,
                "transport": "streamable_http", "url": "http://127.0.0.1:8000/mcp",
                "allowed_tools": ["search"],
            },
        ],
    })
    assert [(s.key, s.transport, s.enabled) for s in cfg.servers] == [
        ("docs", "stdio", True), ("local_http", "streamable_http", False),
    ]
    assert cfg.servers[0].env_from_process == ("DOCS_TOKEN",)
    assert cfg.servers[0].allowed_tools == ("search", "lookup-symbol")
    assert cfg.servers[1].url == "http://127.0.0.1:8000/mcp"


def test_config_holds_only_environment_names_not_secret_values():
    config = parse_mcp_config({
        "version": 1,
        "servers": [{
            "key": "private", "enabled": True, "transport": "stdio",
            "command": "python", "args": [], "env_from_process": ["MCP_TOKEN"],
            "allowed_tools": ["lookup"],
        }],
    })
    assert config.servers[0].env_from_process == ("MCP_TOKEN",)
    assert "fake-secret-value" not in repr(config)


def test_duplicate_server_key_rejected():
    entry = {
        "key": "docs", "enabled": False, "transport": "stdio",
        "command": "python", "args": [], "allowed_tools": [],
    }
    with pytest.raises(MCPConfigError, match="Duplicate"):
        parse_mcp_config({"version": 1, "servers": [entry, dict(entry)]})


@pytest.mark.parametrize(
    "entry",
    [
        {"key": "Bad Key", "enabled": True, "transport": "stdio",
         "command": "python", "allowed_tools": ["x"]},
        {"key": "docs", "enabled": True, "transport": "sse",
         "command": "python", "allowed_tools": ["x"]},
        {"key": "docs", "enabled": True, "transport": "stdio",
         "args": [], "allowed_tools": ["x"]},
        {"key": "docs", "enabled": True, "transport": "stdio",
         "command": "python", "allowed_tools": []},
        {"key": "docs", "enabled": False, "transport": "streamable_http",
         "allowed_tools": []},
        {"key": "docs", "enabled": False, "transport": "stdio",
         "command": "python", "allowed_tools": ["x", "x"]},
        {"key": "docs", "enabled": True, "transport": "stdio",
         "command": "python", "allowed_tools": ["*"]},
        {"key": "docs", "enabled": False, "transport": "stdio",
         "command": "python", "env_from_process": ["=bad"],
         "allowed_tools": []},
    ],
)
def test_invalid_server_config_is_rejected(entry):
    with pytest.raises(MCPConfigError):
        parse_mcp_config({"version": 1, "servers": [entry]})


@pytest.mark.parametrize("version", [0, 2, "1", True, None])
def test_unknown_or_invalid_config_version_rejected(version):
    with pytest.raises(MCPConfigError):
        parse_mcp_config({"version": version, "servers": []})


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mcp", "http://", "http://user:secret@example.com/mcp",
        "http://example.com:bad/mcp", "http://example.com/mcp#fragment", " http://x/mcp ",
    ],
)
def test_http_url_validation_rejects_unsupported_or_credentialed_url(url):
    with pytest.raises(MCPConfigError):
        parse_mcp_config({"version": 1, "servers": [{
            "key": "http", "enabled": False, "transport": "streamable_http",
            "url": url, "allowed_tools": [],
        }]})


def test_invalid_config_json_has_sanitized_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"version":1,"token":"fake-secret"', encoding="utf-8")
    with pytest.raises(MCPConfigError) as exc:
        load_mcp_config(path)
    assert "fake-secret" not in str(exc.value)


def test_official_mcp_v2_client_and_stdio_parameter_imports():
    from mcp import Client, StdioServerParameters

    assert Client is not None
    assert StdioServerParameters is not None
    assert "mcp" in Client.__module__
