"""Agent Tool contracts and controlled errors (Agent-2).

Tools receive only a session-scoped identity and injected domain Services; they
never receive a database connection or Repository. All Agent-2 tools are read-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentToolContext:
    """Identity resolved by Runtime from the current Agent Session."""

    session_id: int
    task_id: int


@dataclass(frozen=True)
class AgentToolSpec:
    """Provider-independent tool declaration."""

    name: str
    description: str
    parameters: dict[str, Any]
    read_only: bool = True
    mutation_scope: str = ""


class AgentToolError(Exception):
    """Controlled tool-layer error; never carries traceback to the model."""

    code = "tool_error"

    def __init__(self, message: str = "Tool request failed."):
        super().__init__(message)
        self.message = message


class ToolNotFoundError(AgentToolError):
    code = "tool_not_found"

    def __init__(self, name: str):
        super().__init__(f"Tool is not available: {name}.")


class ToolArgumentsError(AgentToolError):
    code = "invalid_arguments"


class ToolExecutionError(AgentToolError):
    code = "tool_execution_failed"


class AgentTool(ABC):
    """Read-only or explicitly permissioned Agent Tool handler."""

    @property
    @abstractmethod
    def spec(self) -> AgentToolSpec:
        """Return this tool's provider-independent declaration."""

    @abstractmethod
    def execute(self, context: AgentToolContext, arguments: dict) -> dict:
        """Run a tool using only its injected service dependencies."""


EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
