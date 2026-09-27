"""Official MCP SDK bridge with one private asyncio loop per Agent turn.

This synchronous adapter is intended to be created/used/disposed inside
``AgentTurnWorker.run``. It uses the official ``mcp.Client`` for both stdio and
Streamable HTTP and keeps each client alive across discovery and all tool rounds.
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import AsyncExitStack

from mcp import Client, StdioServerParameters, stdio_client

from .config import MCPServerConfig

MAX_DISCOVERY_PAGES = 1000
MCP_READ_TIMEOUT_SECONDS = 60.0


class MCPClientError(Exception):
    """Sanitized MCP bridge failure; never contains command/env/URL values."""


class MCPClientBridge:
    """Synchronous lifecycle adapter around official async MCP Clients.

    A bridge owns one event loop and one AsyncExitStack. It is deliberately not a
    global/singleton and rejects cross-thread access.
    """

    def __init__(
        self,
        server_configs: tuple[MCPServerConfig, ...] | list[MCPServerConfig],
        *,
        client_factory=Client,
    ):
        self.server_configs = tuple(c for c in server_configs if c.enabled)
        self._client_factory = client_factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stack: AsyncExitStack | None = None
        self._owner_thread: int | None = None
        self._clients: dict[str, Client] = {}
        self._stderr_sinks = []
        self.available_servers: list[str] = []
        self.unavailable_servers: list[str] = []

    def __enter__(self) -> "MCPClientBridge":
        if self._loop is not None:
            raise MCPClientError("MCP turn scope is already open.")
        self._owner_thread = threading.get_ident()
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stack = AsyncExitStack()
        try:
            loop.run_until_complete(self._stack.__aenter__())
            for config in self.server_configs:
                try:
                    loop.run_until_complete(self._connect(config))
                    self.available_servers.append(config.key)
                except Exception:  # noqa: BLE001 - per-server failure is isolated
                    self.unavailable_servers.append(config.key)
            return self
        except BaseException:
            self.close()
            raise

    async def _connect(self, config: MCPServerConfig) -> None:
        if config.transport == "stdio":
            # Only explicitly allowlisted environment variable names are copied.
            env = {
                name: os.environ[name]
                for name in config.env_from_process
                if name in os.environ
            }
            parameters = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=env or None,
            )
            # Suppress untrusted subprocess stderr so secrets cannot be echoed to logs.
            stderr_sink = open(os.devnull, "w", encoding="utf-8")
            self._stderr_sinks.append(stderr_sink)
            server = stdio_client(parameters, errlog=stderr_sink)
        elif config.transport == "streamable_http":
            # Official Client(url) selects Streamable HTTP. No custom headers/auth.
            server = config.url
        else:  # configs are validated, fail closed if a forged object is supplied
            raise MCPClientError("Unsupported MCP transport.")

        client = self._client_factory(
            server, read_timeout_seconds=MCP_READ_TIMEOUT_SECONDS
        )
        assert self._stack is not None
        await self._stack.enter_async_context(client)
        self._clients[config.key] = client

    def list_tools(self, server_key: str) -> tuple[Any, ...]:
        """Read all pages of tools from one connected server using the same client."""
        self._assert_owner()
        if server_key not in self._clients:
            raise MCPClientError("MCP server is not connected for this turn.")
        return tuple(self._run(self._list_tools_all(server_key)))

    async def _list_tools_all(self, server_key: str) -> list[Any]:
        client = self._clients[server_key]
        cursor: str | None = None
        seen_cursors: set[str] = set()
        tools: list[Any] = []
        for _ in range(MAX_DISCOVERY_PAGES):
            result = await client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise MCPClientError("MCP tool pagination cursor repeated.")
            seen_cursors.add(cursor)
        raise MCPClientError("MCP tool discovery exceeded page limit.")

    def call_tool(
        self, server_key: str, remote_tool_name: str, arguments: dict
    ) -> Any:
        """Call only an already-discovered mapping on a connected server."""
        self._assert_owner()
        if server_key not in self._clients:
            raise MCPClientError("MCP server is not connected for this turn.")
        return self._run(
            self._clients[server_key].call_tool(remote_tool_name, arguments)
        )

    def _run(self, awaitable):
        self._assert_owner()
        assert self._loop is not None
        try:
            return self._loop.run_until_complete(awaitable)
        except MCPClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - do not propagate SDK/transport detail
            raise MCPClientError("MCP operation failed.") from exc

    def _assert_owner(self) -> None:
        if (self._loop is None or self._loop.is_closed()
                or self._owner_thread != threading.get_ident()):
            raise MCPClientError("MCP client is unavailable outside its owning turn thread.")

    def close(self) -> None:
        """Close every client, drain/cancel loop tasks, then close the private loop."""
        loop = self._loop
        stack = self._stack
        if loop is None:
            return
        try:
            if stack is not None and not loop.is_closed():
                try:
                    loop.run_until_complete(stack.aclose())
                except Exception:  # noqa: BLE001 - shutdown errors are not user data
                    pass
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    try:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._clients.clear()
            for sink in self._stderr_sinks:
                try:
                    sink.close()
                except OSError:
                    pass
            self._stderr_sinks.clear()
            self._stack = None
            self._loop = None
            self._owner_thread = None
            try:
                asyncio.set_event_loop(None)
            except RuntimeError:
                pass
            if not loop.is_closed():
                loop.close()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False
