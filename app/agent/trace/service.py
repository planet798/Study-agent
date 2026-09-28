"""Fail-open Trace persistence and deterministic evaluation orchestration."""

from __future__ import annotations

from ...database.agent_evaluation_repository import AgentEvaluationRepository
from ...database.agent_trace_repository import AgentTraceRepository
from ..eval.evaluator import AgentTurnEvaluator
from .collector import AgentTraceCollector, error_code_for_exception
from .models import TraceReceipt


class AgentTraceService:
    """Persist completed turns and best-effort versioned evaluations."""

    def __init__(
        self,
        trace_repository: AgentTraceRepository,
        evaluation_repository: AgentEvaluationRepository,
        evaluator: AgentTurnEvaluator | None = None,
        *,
        collector_factory=AgentTraceCollector,
    ):
        self.trace_repository = trace_repository
        self.evaluation_repository = evaluation_repository
        self.evaluator = evaluator or AgentTurnEvaluator()
        self.collector_factory = collector_factory

    def new_collector(
        self, session_id: int, task_id: int, user_message_id: int
    ) -> AgentTraceCollector:
        return self.collector_factory(session_id, task_id, user_message_id)

    def safe_record_success(
        self, collector: AgentTraceCollector, assistant_message_id: int
    ) -> TraceReceipt:
        return self._safe_record(
            collector,
            status="succeeded",
            assistant_message_id=assistant_message_id,
            error_code="",
        )

    def safe_record_failure(
        self, collector: AgentTraceCollector, error: BaseException
    ) -> TraceReceipt:
        return self._safe_record(
            collector,
            status="failed",
            assistant_message_id=None,
            error_code=error_code_for_exception(error) if isinstance(error, Exception)
            else "unexpected_error",
        )

    def _safe_record(
        self,
        collector: AgentTraceCollector,
        *,
        status: str,
        assistant_message_id: int | None,
        error_code: str,
    ) -> TraceReceipt:
        try:
            trace, events = collector.finalize(
                status=status,
                assistant_message_id=assistant_message_id,
                error_code=error_code,
            )
        except Exception:  # trace is optional observability
            return TraceReceipt()
        try:
            persisted = self.trace_repository.record_turn(trace, events)
        except Exception:  # never turn a learning result into a telemetry failure
            return TraceReceipt()
        trace_id = int(persisted["id"])
        try:
            persisted_events = self.trace_repository.list_events(trace_id)
            result = self.evaluator.evaluate(persisted, persisted_events)
            self.evaluation_repository.upsert(
                trace_id=trace_id,
                evaluator_version=int(result["evaluator_version"]),
                status=result["status"],
                checks_json=result["checks"],
                metrics_json=result["metrics"],
            )
            return TraceReceipt(trace_id, str(result["status"]))
        except Exception:  # trace remains useful even when evaluation is unavailable
            return TraceReceipt(trace_id, "")
