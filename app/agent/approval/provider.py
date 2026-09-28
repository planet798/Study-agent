"""Compose the local approval-request tool after Native, MCP and Sandbox."""
from __future__ import annotations

from ..tools.registry import AgentToolRegistry
from .service import AgentApprovalService
from .tools import RequestCompleteCurrentTaskTool


class AgentApprovalProvider:
    def __init__(self, service: AgentApprovalService):
        self.service = service

    def compose(self, base_registry: AgentToolRegistry | None) -> AgentToolRegistry:
        scopes = set(base_registry.allowed_mutation_scopes) if base_registry else set()
        scopes.add("approval")
        registry = AgentToolRegistry(allowed_mutation_scopes=tuple(sorted(scopes)))
        if base_registry is not None:
            for tool in base_registry.registered_tools():
                registry.register(tool)
        registry.register(RequestCompleteCurrentTaskTool(self.service))
        return registry
