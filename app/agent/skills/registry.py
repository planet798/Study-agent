"""Deterministic registry for static, trusted Agent learning strategies."""

from __future__ import annotations

import re

from ...services.learning_activity import ALL_ACTIVITY_KINDS
from .base import AgentSkill

FALLBACK_SKILL_KEY = "general-study"
_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class AgentSkillError(Exception):
    """Controlled Agent Skill configuration/lookup error."""


class AgentSkillValidationError(AgentSkillError, ValueError):
    """Invalid static Skill declaration."""


class AgentSkillNotFoundError(AgentSkillError, LookupError):
    """Requested Agent Skill key is not registered."""


class AgentSkillRegistry:
    """In-memory, deterministic Registry; it performs no IO and grants no access."""

    def __init__(self):
        self._skills: dict[str, AgentSkill] = {}

    def register(self, skill: AgentSkill) -> None:
        if not isinstance(skill, AgentSkill):
            raise AgentSkillValidationError("skill must be an AgentSkill")
        if not isinstance(skill.key, str) or not _KEY_RE.fullmatch(skill.key):
            raise AgentSkillValidationError("invalid AgentSkill key")
        if skill.key in self._skills:
            raise AgentSkillValidationError(
                f"duplicate AgentSkill key: {skill.key}"
            )
        if not isinstance(skill.instruction, str) or not skill.instruction.strip():
            raise AgentSkillValidationError(
                f"AgentSkill instruction must not be empty: {skill.key}"
            )
        if not isinstance(skill.title, str) or not skill.title.strip():
            raise AgentSkillValidationError(
                f"AgentSkill title must not be empty: {skill.key}"
            )
        if not isinstance(skill.activity_kinds, tuple):
            raise AgentSkillValidationError("activity_kinds must be a tuple")
        if any(not isinstance(kind, str) for kind in skill.activity_kinds):
            raise AgentSkillValidationError("activity_kinds must contain strings")
        if isinstance(skill.priority, bool) or not isinstance(skill.priority, int):
            raise AgentSkillValidationError("priority must be an integer")
        unknown = set(skill.activity_kinds) - set(ALL_ACTIVITY_KINDS)
        if unknown:
            raise AgentSkillValidationError(
                f"unknown learning activity kind: {sorted(unknown)!r}"
            )
        self._skills[skill.key] = skill

    def get(self, key: str) -> AgentSkill:
        try:
            return self._skills[key]
        except (KeyError, TypeError):
            raise AgentSkillNotFoundError(f"AgentSkill not found: {key!r}") from None

    def all(self) -> tuple[AgentSkill, ...]:
        """Stable registration order, detached immutable Skill objects."""
        return tuple(self._skills.values())

    def select_for_activity(self, activity_kind: str | None) -> AgentSkill:
        """Select deterministically from activity kind; never consults a model/DB."""
        fallback = self._skills.get(FALLBACK_SKILL_KEY)
        if fallback is None:
            raise AgentSkillValidationError(
                f"fallback AgentSkill {FALLBACK_SKILL_KEY!r} is not registered"
            )
        if not isinstance(activity_kind, str) or not activity_kind.strip():
            return fallback
        matches = [
            skill for skill in self._skills.values()
            if activity_kind in skill.activity_kinds
        ]
        if not matches:
            return fallback
        # Stable registration order resolves equal priorities deterministically.
        return max(matches, key=lambda skill: skill.priority)
