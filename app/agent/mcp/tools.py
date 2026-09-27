"""Read-only MCP Tool wrappers and safe conversion to model-visible JSON."""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.types import EmbeddedResource, ResourceLink, TextContent, TextResourceContents

from ..tools.base import AgentTool, AgentToolContext, AgentToolError, AgentToolSpec
from .client import MCPClientBridge, MCPClientError
from .config import MCPServerConfig

MAX_MCP_DESCRIPTION_CHARS = 1200
MAX_MCP_RESULT_CHARS = 20_000
MAX_MCP_SCHEMA_CHARS = 20_000
MAX_MCP_RESULT_BLOCKS = 100
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class MCPToolExecutionError(AgentToolError):
    """Sanitized external MCP transport/call failure."""

    code = "mcp_tool_failed"

    def __init__(self):
        super().__init__("External MCP tool failed.")


class MCPAgentTool(AgentTool):
    """Bound wrapper for one locally-approved, remote read-only MCP Tool."""

    def __init__(
        self,
        *,
        bridge: MCPClientBridge,
        server: MCPServerConfig,
        remote_tool_name: str,
        exposed_name: str,
        description: str,
        parameters: dict,
    ):
        self.bridge = bridge
        self.server_key = server.key
        self.remote_tool_name = remote_tool_name
        self._spec = AgentToolSpec(
            name=exposed_name,
            description=description,
            parameters=parameters,
            read_only=True,
        )

    @property
    def spec(self) -> AgentToolSpec:
        return self._spec

    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        del context  # MCP is an external read-only capability, not a DB identity lookup.
        try:
            result = self.bridge.call_tool(
                self.server_key, self.remote_tool_name, arguments
            )
        except Exception as exc:  # noqa: BLE001 - never expose SDK/process details
            raise MCPToolExecutionError() from exc
        return mcp_result_to_data(
            result,
            server_key=self.server_key,
            remote_tool_name=self.remote_tool_name,
        )


def mcp_result_to_data(
    result: Any,
    *,
    server_key: str,
    remote_tool_name: str,
    max_chars: int = MAX_MCP_RESULT_CHARS,
) -> dict:
    """Convert SDK result blocks to bounded JSON-safe, provenance-tagged data."""
    texts: list[str] = []
    unsupported: list[str] = []
    truncated = False
    remaining = max(0, int(max_chars))

    for block_index, block in enumerate(getattr(result, "content", ()) or ()):
        if block_index >= MAX_MCP_RESULT_BLOCKS:
            truncated = True
            break
        block_type = getattr(block, "type", "")
        text_value = None
        if isinstance(block, TextContent) or block_type == "text":
            text_value = getattr(block, "text", None)
        elif isinstance(block, EmbeddedResource) or block_type == "resource":
            resource = getattr(block, "resource", None)
            if isinstance(resource, TextResourceContents):
                text_value = resource.text
            else:
                unsupported.append("embedded_binary_resource")
        elif isinstance(block, ResourceLink) or block_type == "resource_link":
            unsupported.append("resource_link")
        elif block_type in ("image", "audio"):
            unsupported.append(str(block_type))
        else:
            unsupported.append("unsupported_content")

        if text_value is None:
            continue
        text = str(text_value)
        if len(text) > remaining:
            texts.append(text[:remaining])
            truncated = True
            remaining = 0
        else:
            texts.append(text)
            remaining -= len(text)

    structured = getattr(result, "structured_content", None)
    structured_truncated = False
    if structured is not None:
        structured_budget = max(0, max_chars - sum(len(text) for text in texts))
        try:
            encoded = json.dumps(
                structured, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False,
            )
            if len(encoded) > structured_budget:
                structured = None
                structured_truncated = True
            else:
                # Normalize to plain JSON-native containers/scalars.
                structured = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            structured = None
            structured_truncated = True

    return {
        "source": "mcp",
        "server": server_key,
        "tool": remote_tool_name,
        "is_error": bool(getattr(result, "is_error", False)),
        "structured_content": structured,
        "text": texts,
        "unsupported_content": unsupported,
        "truncated": truncated,
        "structured_content_truncated": structured_truncated,
    }


def validate_remote_tool_schema(schema: Any) -> dict | None:
    """Accept only bounded top-level object schemas; leave nested validation remote."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict):
        return None
    if not isinstance(required, list) or any(not isinstance(k, str) for k in required):
        return None
    if any(not isinstance(value, dict) for value in properties.values()):
        return None
    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], (bool, dict)
    ):
        return None
    try:
        encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_MCP_SCHEMA_CHARS:
        return None
    # Copy/normalize so remote SDK model objects cannot mutate the declaration.
    return json.loads(encoded)


def exposed_tool_name(server_key: str, remote_name: str) -> str | None:
    """Namespace a remote name; normalize only hyphens and reject all other invalid forms."""
    if not isinstance(remote_name, str) or not remote_name:
        return None
    normalized = remote_name.replace("-", "_")
    name = f"mcp_{server_key}_{normalized}"
    if not _TOOL_NAME_RE.fullmatch(name):
        return None
    return name


def read_only_hint_is_true(remote_tool: Any) -> bool:
    """Require an explicit positive server annotation (it is not authorization)."""
    annotations = getattr(remote_tool, "annotations", None)
    if annotations is None:
        return False
    if isinstance(annotations, dict):
        return annotations.get("readOnlyHint", annotations.get("read_only_hint")) is True
    return getattr(annotations, "read_only_hint", None) is True
