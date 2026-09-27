"""Static, trusted Agent learning-behavior strategy types (Agent-4)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSkill:
    """Application-defined teaching strategy; never a DB or permission entity."""

    key: str
    title: str
    description: str
    instruction: str
    activity_kinds: tuple[str, ...] = ()
    priority: int = 0
