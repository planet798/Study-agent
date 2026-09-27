"""MCP SDK bridge loop ownership and per-turn client lifecycle (offline fakes)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.agent.mcp.client import MCPClientBridge, MCPClientError
from app.agent.mcp.config import MCPServerConfig


class FakeAsyncClient:
    instances = []

    def __init__(self, server, **kwargs):
        self.server = server
        self.kwargs = kwargs
        self.loop_ids = []
        self.thread_ids = []
        self.closed = False
        self.calls = []
        self.pages = {
            None: SimpleNamespace(tools=["first"], next_cursor="next"),
            "next": SimpleNamespace(tools=["second"], next_cursor=None),
        }
        self.instances.append(self)

    async def __aenter__(self):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.thread_ids.append(threading.get_ident())
        return self

    async def __aexit__(self, *args):
        self.closed = True
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.thread_ids.append(threading.get_ident())
        return False

    async def list_tools(self, cursor=None):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.thread_ids.append(threading.get_ident())
        return self.pages[cursor]

    async def call_tool(self, name, arguments):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.thread_ids.append(threading.get_ident())
        self.calls.append((name, arguments))
        return {"ok": True}


def _stdio(key="docs", env_names=("DOCS_TOKEN",)):
    return MCPServerConfig(
        key=key, transport="stdio", enabled=True, allowed_tools=("search",),
        command="python", args=("fake-server.py",), env_from_process=env_names,
    )


def test_one_private_loop_reuses_client_across_pages_and_calls(monkeypatch):
    import app.agent.mcp.client as bridge_module

    FakeAsyncClient.instances.clear()
    monkeypatch.setenv("DOCS_TOKEN", "secret-value")
    monkeypatch.setenv("UNLISTED_SECRET", "do-not-pass")
    stdio_parameters = []

    def fake_stdio_client(parameters, errlog):
        stdio_parameters.append((parameters, errlog))
        return parameters

    monkeypatch.setattr(bridge_module, "stdio_client", fake_stdio_client)
    bridge = MCPClientBridge((_stdio(),), client_factory=FakeAsyncClient)
    with bridge:
        pages = bridge.list_tools("docs")
        assert pages == ("first", "second")
        result = bridge.call_tool("docs", "search", {"query": "topic"})
        assert result == {"ok": True}
        loop = bridge._loop
        assert loop is not None and not loop.is_closed()
        client = FakeAsyncClient.instances[-1]
        parameters, stderr_sink = stdio_parameters[0]
        assert parameters.env == {"DOCS_TOKEN": "secret-value"}
        assert "UNLISTED_SECRET" not in parameters.env
        assert stderr_sink.closed is False
        assert client.server is parameters
        assert client.calls == [("search", {"query": "topic"})]
        assert len(set(client.loop_ids)) == 1
        assert len(set(client.thread_ids)) == 1

    assert client.closed is True
    assert stderr_sink.closed is True
    assert loop.is_closed()
    assert bridge._loop is None


def test_streamable_http_uses_official_client_url_transport():
    FakeAsyncClient.instances.clear()
    config = MCPServerConfig(
        key="http", transport="streamable_http", enabled=True,
        allowed_tools=("search",), url="http://127.0.0.1:8000/mcp",
    )
    bridge = MCPClientBridge((config,), client_factory=FakeAsyncClient)
    with bridge:
        bridge.list_tools("http")
        client = FakeAsyncClient.instances[-1]
        assert client.server == "http://127.0.0.1:8000/mcp"
    assert client.closed


def test_bridge_connect_failure_is_isolated_to_that_server(monkeypatch):
    import app.agent.mcp.client as bridge_module
    monkeypatch.setattr(
        bridge_module, "stdio_client", lambda parameters, errlog: parameters
    )

    class PartlyFailingClient(FakeAsyncClient):
        async def __aenter__(self):
            if getattr(self.server, "command", None) == "bad-command":
                raise RuntimeError("secret spawn failure")
            return await super().__aenter__()

    broken = MCPServerConfig(
        key="broken", transport="stdio", enabled=True, allowed_tools=("x",),
        command="bad-command",
    )
    healthy = MCPServerConfig(
        key="healthy", transport="streamable_http", enabled=True,
        allowed_tools=("search",), url="http://127.0.0.1:8000/mcp",
    )
    bridge = MCPClientBridge((broken, healthy), client_factory=PartlyFailingClient)
    with bridge:
        assert bridge.available_servers == ["healthy"]
        assert bridge.unavailable_servers == ["broken"]
        assert bridge.list_tools("healthy") == ("first", "second")
    assert bridge._loop is None


def test_separate_turn_bridges_do_not_share_event_loop_or_clients():
    loops = []
    for _ in range(2):
        bridge = MCPClientBridge((_stdio(),), client_factory=FakeAsyncClient)
        with bridge:
            loops.append(bridge._loop)
        assert bridge._loop is None
    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)


def test_client_scope_closes_all_clients_even_when_turn_raises():
    bridge = MCPClientBridge((_stdio(),), client_factory=FakeAsyncClient)
    with pytest.raises(RuntimeError):
        with bridge:
            client = FakeAsyncClient.instances[-1]
            raise RuntimeError("turn failed")
    assert client.closed is True
    assert bridge._loop is None


def test_bridge_rejects_cross_thread_client_use():
    bridge = MCPClientBridge((_stdio(),), client_factory=FakeAsyncClient)
    ready = threading.Event()
    release = threading.Event()
    errors = []

    def owner_thread():
        with bridge:
            bridge.list_tools("docs")
            ready.set()
            release.wait(3)

    thread = threading.Thread(target=owner_thread)
    thread.start()
    assert ready.wait(3)
    try:
        with pytest.raises(MCPClientError):
            bridge.list_tools("docs")
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert FakeAsyncClient.instances[-1].closed is True
    assert bridge._loop is None
