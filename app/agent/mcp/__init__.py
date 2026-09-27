"""Official-MCP-SDK integration for explicitly allowlisted read-only tools."""

from .client import MCPClientBridge, MCPClientError
from .config import (
    MCPConfig,
    MCPConfigError,
    MCPServerConfig,
    default_mcp_config_path,
    load_mcp_config,
    parse_mcp_config,
)
from .provider import MCPDiscoveryReport, MCPTurnScope, MCPToolProvider
from .tools import MCPAgentTool, MCPToolExecutionError

__all__ = [
    "MCPAgentTool",
    "MCPClientBridge",
    "MCPClientError",
    "MCPConfig",
    "MCPConfigError",
    "MCPDiscoveryReport",
    "MCPServerConfig",
    "MCPToolExecutionError",
    "MCPToolProvider",
    "MCPTurnScope",
    "default_mcp_config_path",
    "load_mcp_config",
    "parse_mcp_config",
]
