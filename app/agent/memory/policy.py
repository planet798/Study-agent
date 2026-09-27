"""Immutable, character-based policy for bounded Agent conversation context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentMemoryPolicy:
    history_budget_chars: int = 60_000
    compaction_trigger_chars: int = 45_000
    target_tail_chars: int = 24_000
    keep_recent_turns: int = 4
    summary_input_max_chars: int = 40_000
    summary_max_chars: int = 10_000
    per_message_summary_chars: int = 6_000
    max_compaction_passes: int = 8
    summary_max_tokens: int = 3_000

    def __post_init__(self) -> None:
        positive = (
            "history_budget_chars", "compaction_trigger_chars", "target_tail_chars",
            "summary_input_max_chars", "summary_max_chars",
            "per_message_summary_chars", "max_compaction_passes", "summary_max_tokens",
        )
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (isinstance(self.keep_recent_turns, bool)
                or not isinstance(self.keep_recent_turns, int)
                or self.keep_recent_turns < 0):
            raise ValueError("keep_recent_turns must be a non-negative integer")
        if not self.target_tail_chars < self.compaction_trigger_chars < self.history_budget_chars:
            raise ValueError(
                "expected target_tail_chars < compaction_trigger_chars < history_budget_chars"
            )
        if self.per_message_summary_chars < 128:
            raise ValueError("per_message_summary_chars must be at least 128")
        if self.summary_max_chars >= self.summary_input_max_chars:
            raise ValueError("summary_max_chars must be less than summary_input_max_chars")
