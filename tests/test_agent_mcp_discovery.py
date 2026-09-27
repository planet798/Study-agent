"""Per-turn discovery gates, pagination contract, namespace and collisions."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from mcp.types import Tool, ToolAnnotations

from app.agent.mcp.config import MCPConfig, MCPServerConfig
from app.agent.mcp.provider import MCPToolProvider
from app.agent.tools.base import AgentTool, AgentToolContext, AgentToolSpec, EMPTY_OBJECT_SCHEMA
from app.agent.tools.registry import AgentToolRegistry


def _tool(name, *, read_only=None, schema=None, description="remote description"):
    annotations = None if read_only is None else ToolAnnotations(readOnlyHint=read_only)
    return Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {},
                                "additionalProperties": False},
        annotations=annotations,
    )


class StaticBridge:
    def __init__(self, configs, results):
        self.configs = tuple(configs)
        self.results = results
        self.available_servers = [c.key for c in self.configs]
        self.unavailable_servers = []
        self.closed = False
        self.calls = []

    def __enter__(self):
        return self

    def list_tools(self, server_key):
        value = self.results[server_key]
        if isinstance(value, Exception):
            raise value
        return tuple(value)

    def call_tool(self, server, name, arguments):
        self.calls.append((server, name, arguments))
        return SimpleNamespace(
            is_error=False, content=[], structured_content={"remote": True}
        )

    def close(self):
        self.closed = True


def _server(key="docs", *, enabled=True, allowed=("search",)):
    return MCPServerConfig(
        key=key, transport="streamable_http", enabled=enabled,
        allowed_tools=tuple(allowed), url=f"http://127.0.0.1/{key}/mcp",
    )


def _provider(configs, responses):
    bridges = []

    def factory(enabled_configs):
        bridge = StaticBridge(enabled_configs, responses)
        bridges.append(bridge)
        return bridge

    return MCPToolProvider(MCPConfig(1, tuple(configs)), bridge_factory=factory), bridges


def test_allowlist_and_read_only_hint_are_both_required_and_namespaced():
    server = _server(allowed=("search", "writable", "no_hint"))
    responses = {"docs": [
        _tool("search", read_only=True),
        _tool("not_allowed", read_only=True),
        _tool("writable", read_only=False),
        _tool("no_hint", read_only=None),
    ]}
    provider, _bridges = _provider((server,), responses)
    with provider.open_turn() as scope:
        assert scope.registry.names() == ("mcp_docs_search",)
        assert scope.report.available_servers == ("docs",)
        assert set(scope.report.skipped_tools) == {
            "docs/writable", "docs/no_hint",
        }
        assert scope.registry.get("mcp_docs_search").spec.read_only is True
        declaration, = scope.registry.model_tools()
        assert declaration["function"]["name"] == "mcp_docs_search"
        assert "untrusted data" in declaration["function"]["description"]


def test_remote_name_normalization_and_collision_is_skipped_not_overwritten():
    provider, _ = _provider(
        (_server(allowed=("foo-bar", "foo_bar")),),
        {"docs": [
            _tool("foo-bar", read_only=True),
            _tool("foo_bar", read_only=True),
        ]},
    )
    with provider.open_turn() as scope:
        assert scope.registry.names() == ("mcp_docs_foo_bar",)
        assert scope.report.skipped_tools == ("docs/foo_bar",)
        assert scope.registry.get("mcp_docs_foo_bar").remote_tool_name == "foo-bar"


def test_native_tool_name_collision_never_overwrites_native():
    class Native(AgentTool):
        @property
        def spec(self):
            return AgentToolSpec(
                "mcp_docs_search", "native wins", EMPTY_OBJECT_SCHEMA, True
            )

        def execute(self, context: AgentToolContext, arguments: dict):
            return {"native": True}

    native = AgentToolRegistry()
    native.register(Native())
    provider, _ = _provider(
        (_server(allowed=("search",)),),
        {"docs": [_tool("search", read_only=True)]},
    )
    with provider.open_turn(native_registry=native) as scope:
        assert scope.registry.names() == ("mcp_docs_search",)
        assert scope.registry.get("mcp_docs_search") is native.get("mcp_docs_search")
        assert scope.report.skipped_tools == ("docs/search",)


def test_invalid_schema_skipped_and_remote_description_bounded():
    long_description = "x" * 5000
    provider, _ = _provider(
        (_server(allowed=("good", "bad")),),
        {"docs": [
            _tool("good", read_only=True, description=long_description),
            _tool("bad", read_only=True, schema={"type": "array"}),
        ]},
    )
    with provider.open_turn() as scope:
        assert scope.registry.names() == ("mcp_docs_good",)
        description = scope.registry.model_tools()[0]["function"]["description"]
        assert len(description) < 1400
        assert scope.report.skipped_tools == ("docs/bad",)


def test_disabled_server_is_not_connected_and_native_registry_survives():
    called = []

    def factory(configs):
        called.append(configs)
        return StaticBridge(configs, {})

    native = AgentToolRegistry()
    provider = MCPToolProvider(MCPConfig(1, (_server(enabled=False),)), factory)
    with provider.open_turn(native_registry=native) as scope:
        assert scope.registry.names() == ()
        assert scope.report.available_servers == ()
        assert scope.report.unavailable_servers == ()
    assert called == []


def test_discovery_failure_is_per_server_and_native_tools_remain_usable():
    provider, bridges = _provider(
        (_server("bad", allowed=("x",)), _server("healthy", allowed=("search",))),
        {"bad": RuntimeError("private transport detail"),
         "healthy": [_tool("search", read_only=True)]},
    )
    native = AgentToolRegistry()
    with provider.open_turn(native_registry=native) as scope:
        assert scope.registry.names() == ("mcp_healthy_search",)
        assert scope.report.available_servers == ("healthy",)
        assert scope.report.unavailable_servers == ("bad",)
    assert bridges[0].closed


def test_multiple_servers_have_separate_namespaces_and_provenance():
    provider, bridges = _provider(
        (_server("docs", allowed=("search",)),
         _server("github", allowed=("search",))),
        {"docs": [_tool("search", read_only=True)],
         "github": [_tool("search", read_only=True)]},
    )
    with provider.open_turn() as scope:
        assert scope.registry.names() == (
            "mcp_docs_search", "mcp_github_search",
        )
        first = scope.registry.execute_raw(
            "mcp_github_search", AgentToolContext(1, 2), "{}"
        )
        assert first["data"]["source"] == "mcp"
        assert first["data"]["server"] == "github"
        assert first["data"]["tool"] == "search"
    assert bridges[0].calls == [("github", "search", {})]
    assert bridges[0].closed
