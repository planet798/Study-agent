"""MCP tool result conversion, bounds, provenance, and controlled failures."""

from __future__ import annotations

import json

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
)

from app.agent.mcp.config import MCPServerConfig
from app.agent.mcp.tools import (
    MCPAgentTool,
    MCPToolExecutionError,
    mcp_result_to_data,
)
from app.agent.tools.base import AgentToolContext
from app.agent.tools.registry import AgentToolRegistry


class FakeBridge:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def call_tool(self, server, name, arguments):
        self.calls.append((server, name, arguments))
        if self.error:
            raise self.error
        return self.result


def test_text_and_structured_result_are_json_safe_and_keep_provenance():
    result = CallToolResult(
        content=[TextContent(text="external docs result")],
        structuredContent={"hits": [{"title": "MCP result"}]},
        isError=False,
    )
    data = mcp_result_to_data(
        result, server_key="docs", remote_tool_name="search"
    )
    assert data == {
        "source": "mcp", "server": "docs", "tool": "search",
        "is_error": False,
        "structured_content": {"hits": [{"title": "MCP result"}]},
        "text": ["external docs result"],
        "unsupported_content": [], "truncated": False,
        "structured_content_truncated": False,
    }
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def test_remote_tool_error_is_data_not_runtime_exception():
    result = CallToolResult(
        content=[TextContent(text="remote says failure")],
        isError=True,
    )
    data = mcp_result_to_data(
        result, server_key="s", remote_tool_name="read"
    )
    assert data["is_error"] is True
    assert data["text"] == ["remote says failure"]


def test_binary_and_multimodal_blocks_are_described_not_base64_forwarded():
    result = CallToolResult(content=[
        ImageContent(data="IMAGE_BASE64_SECRET", mimeType="image/png"),
        AudioContent(data="AUDIO_BASE64_SECRET", mimeType="audio/wav"),
        EmbeddedResource(resource=BlobResourceContents(
            uri="file:///fake", blob="BINARY_BASE64_SECRET"
        )),
    ])
    data = mcp_result_to_data(result, server_key="docs", remote_tool_name="x")
    assert set(data["unsupported_content"]) == {
        "image", "audio", "embedded_binary_resource",
    }
    encoded = json.dumps(data)
    assert "BASE64_SECRET" not in encoded


def test_result_content_and_structured_content_are_bounded_and_valid_json():
    result = CallToolResult(
        content=[TextContent(text="t" * 100), TextContent(text="second")],
        structuredContent={"value": "s" * 100},
    )
    data = mcp_result_to_data(
        result, server_key="docs", remote_tool_name="search", max_chars=20
    )
    assert len(data["text"][0]) == 20
    assert data["truncated"] is True
    assert data["structured_content"] is None
    assert data["structured_content_truncated"] is True
    json.dumps(data)


def test_tool_wrapper_calls_bound_remote_name_and_returns_mcp_error_envelope():
    server = MCPServerConfig(
        key="docs", transport="stdio", enabled=True,
        allowed_tools=("remote-search",), command="python",
    )
    bridge = FakeBridge(CallToolResult(
        content=[TextContent(text="found")], structuredContent={"count": 1}
    ))
    tool = MCPAgentTool(
        bridge=bridge,
        server=server,
        remote_tool_name="remote-search",
        exposed_name="mcp_docs_remote_search",
        description="external",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )
    registry = AgentToolRegistry()
    registry.register(tool)
    result = registry.execute_raw(
        "mcp_docs_remote_search", AgentToolContext(9, 42), "{}"
    )
    assert result["ok"] is True
    assert bridge.calls == [("docs", "remote-search", {})]
    assert result["data"]["source"] == "mcp"
    assert result["data"]["server"] == "docs"
    assert result["data"]["tool"] == "remote-search"


def test_transport_exception_is_sanitized_with_specific_error_code():
    server = MCPServerConfig(
        key="docs", transport="stdio", enabled=True,
        allowed_tools=("read",), command="python",
    )
    registry = AgentToolRegistry()
    registry.register(MCPAgentTool(
        bridge=FakeBridge(error=RuntimeError("secret env and traceback")),
        server=server,
        remote_tool_name="read",
        exposed_name="mcp_docs_read",
        description="external",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ))
    result = registry.execute("mcp_docs_read", AgentToolContext(1, 1), {})
    assert result == {
        "ok": False,
        "error": {
            "code": "mcp_tool_failed",
            "message": "External MCP tool failed.",
        },
    }
    assert "secret" not in json.dumps(result)


def test_mcp_wrapper_is_read_only_and_has_no_database_dependency():
    from app.agent.mcp.tools import MCPAgentTool

    params = MCPAgentTool.__init__.__annotations__
    assert "conn" not in params and "repository" not in params
    assert "execute" in vars(MCPAgentTool)
