"""Read-only, success-event scoped extraction; no scheduling or persistence."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass

from .memory_candidate import MemoryCandidate
from .personal_memory_extractor import PersonalMemoryExtractor
from .session import AgentSessionService
from ..database.personalization_repository import PersonalizationRepository
from ..services.personalization_service import PersonalizationService

EXTRACTION_NETWORK_TIMEOUT = 5.0


def log_extraction_skip(code: str, error: Exception | None = None) -> None:
    logger = logging.getLogger(__name__)
    if error is None:
        logger.info("Memory extraction skipped (%s).", code)
    else:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        logger.warning("Memory extraction skipped (%s; %s at %s:%d).",
                       code, type(error).__name__, frame.name, frame.lineno)


@dataclass(frozen=True, slots=True)
class ExtractionConsentSnapshot:
    allowed: bool
    revision: str = ""


def read_consent_snapshot(service: PersonalizationService) -> ExtractionConsentSnapshot:
    settings = service.get_settings()
    if not isinstance(settings["updated_at"], str):
        raise ValueError("invalid_consent_revision")
    return ExtractionConsentSnapshot(
        settings["memory_enabled"] == 1 and settings["auto_memory_enabled"] == 1,
        settings["updated_at"],
    )


def consent_allowed(service: PersonalizationService) -> bool:
    """Shared hard gate, also required by future E-C confirmation/save code."""
    return read_consent_snapshot(service).allowed


def capture_consent_snapshot(conn) -> ExtractionConsentSnapshot:
    """Best-effort turn-start snapshot from that turn worker's connection."""
    try:
        return read_consent_snapshot(PersonalizationService(PersonalizationRepository(conn)))
    except Exception as error:
        log_extraction_skip("consent_read_failed", error)
        return ExtractionConsentSnapshot(False)


def capture_extraction_consent(conn) -> bool:
    return capture_consent_snapshot(conn).allowed


@dataclass(frozen=True, slots=True)
class CompletedMemoryExtractionTurn:
    session_id: int
    user_message_id: int
    assistant_message_id: int
    consent_at_turn_start: bool
    consent_revision_at_turn_start: str = ""

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (
            self.session_id, self.user_message_id, self.assistant_message_id,
        )) or type(self.consent_at_turn_start) is not bool or not isinstance(self.consent_revision_at_turn_start, str):
            raise ValueError("invalid_completed_turn")

    @classmethod
    def from_result(cls, result, consent: bool, revision: str = ""):
        # Only called after Runtime.send_message returned successfully. Never on
        # failed turns, task lifecycle changes, Session resume, or settings changes.
        return cls(result.session_id, result.user_message["id"],
                   result.assistant_message["id"], consent, revision)


@dataclass(frozen=True, slots=True)
class MemoryCandidateBatch:
    turn: CompletedMemoryExtractionTurn
    candidates: tuple[MemoryCandidate, ...]
    consent_revision: str = ""

    @property
    def session_id(self):
        return self.turn.session_id

    @property
    def source_message_id(self):
        return self.turn.user_message_id


class MemoryExtractionService:
    def __init__(self, personalization_service: PersonalizationService,
                 session_service: AgentSessionService, extractor: PersonalMemoryExtractor):
        self.personalization_service = personalization_service
        self.session_service = session_service
        self.extractor = extractor

    def consent_allowed(self) -> bool:
        return consent_allowed(self.personalization_service)

    def extract_completed_turn(self, turn: CompletedMemoryExtractionTurn,
                               cancelled=lambda: False) -> MemoryCandidateBatch | None:
        try:
            initial = read_consent_snapshot(self.personalization_service)
            if (not turn.consent_at_turn_start or cancelled() or not initial.allowed
                    or (turn.consent_revision_at_turn_start
                        and initial.revision != turn.consent_revision_at_turn_start)):
                return None
            source = self.session_service.completed_turn_user_message(
                turn.session_id, turn.user_message_id, turn.assistant_message_id,
            )
            if cancelled() or not self.extractor.model_client.is_configured():
                return None
            # Recheck immediately before the independent model request as well.
            before_model = read_consent_snapshot(self.personalization_service)
            if not before_model.allowed or before_model.revision != initial.revision:
                return None
            candidates = self.extractor.extract(
                source["content"], source_session_id=turn.session_id,
                source_message_id=turn.user_message_id,
            )
            if not candidates or cancelled():
                return None
            after_model = read_consent_snapshot(self.personalization_service)
            if not after_model.allowed or after_model.revision != initial.revision:
                return None
            return MemoryCandidateBatch(turn, tuple(candidates), initial.revision)
        except Exception as error:
            log_extraction_skip("extraction_unavailable", error)
            return None
