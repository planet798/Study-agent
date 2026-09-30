"""Bounded transient extraction queue; worker-owned read-only connections."""

from collections import deque

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..agent.memory_extraction import (
    CompletedMemoryExtractionTurn, MemoryCandidateBatch, capture_consent_snapshot,
    log_extraction_skip,
)
from ..database.connection import get_readonly_connection

MAX_PENDING_EXTRACTIONS = 8
MAX_TRANSIENT_BATCHES = 16


def open_extraction_connection(db_path):
    conn = get_readonly_connection(db_path)
    try:
        conn.execute("PRAGMA busy_timeout = 200")
        return conn
    except Exception:
        conn.close()
        raise


def publication_consent_allowed(db_path, revision="") -> bool:
    """Fresh short-lived read; never reuse the Settings/Agent connection."""
    conn = None
    try:
        conn = open_extraction_connection(db_path)
        snapshot = capture_consent_snapshot(conn)
        return snapshot.allowed and (not revision or snapshot.revision == revision)
    except Exception as error:
        log_extraction_skip("publication_consent_failed", error)
        return False
    finally:
        if conn is not None:
            conn.close()


class MemoryExtractionWorker(QThread):
    candidates_ready = Signal(object)

    def __init__(self, db_path, service_factory, turn, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.service_factory = service_factory
        self.turn = turn

    def run(self):
        conn = None
        try:
            if not self.turn.consent_at_turn_start or self.isInterruptionRequested():
                return
            conn = open_extraction_connection(self.db_path)
            # Gate before constructing even optional model/source dependencies.
            snapshot = capture_consent_snapshot(conn)
            if (not snapshot.allowed or self.isInterruptionRequested()
                    or (self.turn.consent_revision_at_turn_start
                        and snapshot.revision != self.turn.consent_revision_at_turn_start)):
                return
            service = self.service_factory(conn)
            batch = service.extract_completed_turn(self.turn, self.isInterruptionRequested)
            if batch is not None and not self.isInterruptionRequested():
                self.candidates_ready.emit(batch)
        except Exception as error:
            log_extraction_skip("worker_unavailable", error)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as error:
                    log_extraction_skip("connection_close_failed", error)


class MemoryExtractionCoordinator(QObject):
    """GUI-thread queue owner; at most one active worker, no retries/history replay.

    shutdown() is nonblocking. Owner must survive until drained; MainWindow defers
    destruction/quit while an interrupted worker returns from its timed request.
    """

    candidates_ready = Signal(object)
    drained = Signal()

    def __init__(self, db_path, service_factory, parent=None, *,
                 max_pending=MAX_PENDING_EXTRACTIONS, consent_check=None):
        super().__init__(parent)
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("invalid_pending_limit")
        self.db_path = db_path
        self.service_factory = service_factory
        self.max_pending = max_pending
        self._consent_check = consent_check
        self._pending = deque()
        self._seen = set()  # IDs only; process-lifetime at-most-once, including dropped jobs.
        self._active = None
        self._closed = False
        self.candidate_batches = deque(maxlen=MAX_TRANSIENT_BATCHES)

    @property
    def is_running(self):
        return self._active is not None

    @property
    def pending_count(self):
        return len(self._pending)

    def _allowed(self, revision=""):
        try:
            if self._consent_check is not None and self._consent_check() is not True:
                return False
            return publication_consent_allowed(self.db_path, revision)
        except Exception as error:
            log_extraction_skip("coordinator_consent_failed", error)
            return False

    @Slot(object)
    def enqueue(self, turn: CompletedMemoryExtractionTurn):
        try:
            if self._closed or not isinstance(turn, CompletedMemoryExtractionTurn):
                return False
            if turn.user_message_id in self._seen:
                log_extraction_skip("duplicate_event")
                return False
            self._seen.add(turn.user_message_id)
            if not turn.consent_at_turn_start or not self._allowed(turn.consent_revision_at_turn_start):
                return False
            if len(self._pending) >= self.max_pending:
                log_extraction_skip("queue_full")
                return False
            self._pending.append(turn)
            self._start_next()
            return True
        except Exception as error:
            log_extraction_skip("scheduling_unavailable", error)
            return False

    def _start_next(self):
        if self._closed or self._active is not None or not self._pending:
            return
        turn = self._pending.popleft()
        worker = MemoryExtractionWorker(self.db_path, self.service_factory, turn, self)
        self._active = worker
        worker.candidates_ready.connect(self._publish)
        worker.finished.connect(self._finished)
        try:
            worker.start()
        except Exception as error:
            self._active = None
            worker.deleteLater()
            log_extraction_skip("worker_start_failed", error)
            self._start_next()

    @Slot(object)
    def _publish(self, batch: MemoryCandidateBatch):
        # Queued signals may be delivered after consent was revoked or shutdown.
        try:
            if (self._closed or not isinstance(batch, MemoryCandidateBatch)
                    or not batch.candidates or self._active is None
                    or batch.turn != self._active.turn or not self._allowed(batch.consent_revision)):
                return
            self.candidate_batches.append(batch)
            self.candidates_ready.emit(batch)
        except Exception as error:
            log_extraction_skip("publication_unavailable", error)

    @Slot()
    def _finished(self):
        worker = self._active
        self._active = None
        if worker is not None:
            worker.deleteLater()  # finished, never delete a running QThread
        if self._closed:
            self.drained.emit()
        else:
            self._start_next()

    def shutdown(self):
        self._closed = True
        self._pending.clear()
        self.candidate_batches.clear()
        if self._active is not None:
            self._active.requestInterruption()
        # Never wait() on the GUI thread; drained drives safe deferred teardown.
        return not self.is_running
