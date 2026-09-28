"""AgentToolRegistry: strict arguments, OpenAI declaration, safe result envelope."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import (
    AgentTool,
    AgentToolContext,
    AgentToolError,
    AgentToolSpec,
    ToolArgumentsError,
    ToolExecutionError,
    ToolNotFoundError,
)

_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class AgentToolRegistry:
    """Registry of explicitly registered read-only Agent tools.

    ``execute`` always returns a JSON-safe ``{ok, data/error}`` envelope. Handler
    exceptions are intentionally hidden behind a generic execution error.
    """

    def __init__(self, allowed_mutation_scopes: tuple[str, ...] = ()):
        scopes = tuple(allowed_mutation_scopes)
        if any(scope not in ("sandbox", "approval") for scope in scopes):
            raise ValueError("only sandbox and approval mutation scopes are supported")
        if len(set(scopes)) != len(scopes):
            raise ValueError("duplicate mutation scope")
        self._allowed_mutation_scopes = frozenset(scopes)
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if not isinstance(tool, AgentTool):
            raise TypeError("tool must implement AgentTool")
        spec = tool.spec
        if not isinstance(spec, AgentToolSpec):
            raise TypeError("tool.spec must be AgentToolSpec")
        if not _TOOL_NAME_RE.fullmatch(spec.name or ""):
            raise ValueError("invalid tool name")
        if not isinstance(spec.read_only, bool):
            raise ValueError("tool read_only must be boolean")
        if spec.read_only:
            if spec.mutation_scope:
                raise ValueError("read-only tools cannot declare a mutation scope")
        elif (not spec.mutation_scope
              or spec.mutation_scope not in self._allowed_mutation_scopes):
            raise ValueError("tool mutation scope is not authorized")
        if spec.name in self._tools:
            raise ValueError(f"duplicate Agent tool name: {spec.name}")
        self._tools[spec.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except (KeyError, TypeError):
            raise ToolNotFoundError(str(name)) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def registered_tools(self) -> tuple[AgentTool, ...]:
        """Return immutable registration order for safe Registry composition."""
        return tuple(self._tools.values())

    @property
    def allowed_mutation_scopes(self) -> tuple[str, ...]:
        """Public read-only view of this Registry's explicitly authorized scopes."""
        return tuple(sorted(self._allowed_mutation_scopes))

    def model_tools(self) -> tuple[dict, ...]:
        """Return OpenAI-compatible declarations, detached from internal specs."""
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": tool.spec.name,
                    "description": tool.spec.description,
                    "parameters": json.loads(json.dumps(tool.spec.parameters)),
                },
            }
            for tool in self._tools.values()
        )

    def execute(
        self,
        name: str,
        context: AgentToolContext,
        arguments: dict,
    ) -> dict:
        """Validate an already-decoded JSON object and execute a registered tool."""
        try:
            tool = self.get(name)
            self._validate_arguments(tool.spec, arguments)
            data = tool.execute(context, arguments)
            # Enforce the JSON-safe boundary; don't return arbitrary Python objects.
            json.dumps(data, ensure_ascii=False, allow_nan=False)
            return {"ok": True, "data": data}
        except AgentToolError as exc:
            return {
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except Exception:  # noqa: BLE001 - do not expose exception/traceback to model
            exc = ToolExecutionError("Tool execution failed.")
            return {
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }

    def execute_raw(
        self,
        name: str,
        context: AgentToolContext,
        raw_arguments: str,
    ) -> dict:
        """Decode model raw arguments; require a JSON object before dispatch."""
        if not isinstance(raw_arguments, str):
            exc = ToolArgumentsError("Tool arguments must be a JSON object.")
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError):
            exc = ToolArgumentsError("Tool arguments contain invalid JSON.")
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        if not isinstance(arguments, dict):
            exc = ToolArgumentsError("Tool arguments must be a JSON object.")
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        return self.execute(name, context, arguments)

    @staticmethod
    def _validate_arguments(spec: AgentToolSpec, arguments: dict) -> None:
        if not isinstance(arguments, dict):
            raise ToolArgumentsError("Tool arguments must be a JSON object.")
        schema = spec.parameters
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(properties, dict) or not isinstance(required, (list, tuple)):
            raise ToolArgumentsError("Tool argument schema is invalid.")
        unknown = set(arguments) - set(properties)
        if unknown and schema.get("additionalProperties", True) is False:
            raise ToolArgumentsError("Unexpected tool argument.")
        missing = set(required) - set(arguments)
        if missing:
            raise ToolArgumentsError("Required tool argument is missing.")
        for key, value in arguments.items():
            if key not in properties:
                continue
            expected = properties[key].get("type")
            valid = {
                "object": lambda v: isinstance(v, dict),
                "array": lambda v: isinstance(v, list),
                "string": lambda v: isinstance(v, str),
                "boolean": lambda v: isinstance(v, bool),
                "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
                "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            }.get(expected)
            if valid is not None and not valid(value):
                raise ToolArgumentsError(f"Tool argument has invalid type: {key}.")
