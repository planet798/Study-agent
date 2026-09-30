"""Plain-text preferences for Settings and Agent context injection; no automatic extraction."""

import unicodedata

from app.database.personalization_repository import PersonalizationRepository


class PersonalizationService:
    def __init__(self, repository: PersonalizationRepository):
        self.repository = repository

    @staticmethod
    def _text(text, *, maximum: int, allow_empty: bool) -> str:
        if not isinstance(text, str):
            raise ValueError("content must be str")
        # Validate before stripping so unsafe surrounding controls cannot disappear.
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in "\n\r\t"
               for char in text):
            raise ValueError("content contains unsafe control or surrogate characters")
        text = text.strip()
        if not allow_empty and not text:
            raise ValueError("memory content must not be empty")
        if len(text) > maximum:
            raise ValueError(f"content exceeds {maximum} characters")
        return text

    @staticmethod
    def _bool(enabled) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be bool")
        return enabled

    def get_settings(self) -> dict:
        return self.repository.get_settings()

    def set_instructions(self, text: str) -> dict:
        return self.repository.set_instructions(self._text(text, maximum=8000, allow_empty=True))

    def set_memory_enabled(self, enabled: bool) -> dict:
        return self.repository.set_memory_enabled(self._bool(enabled))

    def set_auto_memory_enabled(self, enabled: bool) -> dict:
        return self.repository.set_auto_memory_enabled(self._bool(enabled))

    def list_memories(self, include_disabled: bool = True) -> list[dict]:
        return self.repository.list_memories(self._bool(include_disabled))

    def add_manual_memory(self, content: str) -> dict:
        return self.repository.create_memory(self._text(content, maximum=1000, allow_empty=False))

    def add_session_memory(self, content: str, source_session_id: int,
                           source_message_id: int | None = None) -> dict:
        return self.repository.create_memory(
            self._text(content, maximum=1000, allow_empty=False), "session",
            source_session_id, source_message_id,
        )

    def edit_memory(self, memory_id: int, content: str) -> dict:
        return self.repository.update_memory(
            memory_id, self._text(content, maximum=1000, allow_empty=False)
        )

    def enable_memory(self, memory_id: int) -> dict:
        return self.repository.set_memory_enabled_state(memory_id, True)

    def disable_memory(self, memory_id: int) -> dict:
        return self.repository.set_memory_enabled_state(memory_id, False)

    def delete_memory(self, memory_id: int) -> None:
        self.repository.delete_memory(memory_id)
