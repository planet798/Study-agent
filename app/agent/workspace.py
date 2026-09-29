"""Runtime-only Workspace capability; never inject root into model text or trace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentWorkspaceSpec:
    kind: str  # none / managed / local
    root: Path | None
    readable: bool
    writable: bool
    execution_allowed: bool
