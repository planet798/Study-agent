"""Persistence for versioned, deterministic Agent turn evaluations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


class AgentEvaluationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_for_trace(self, trace_id: int, evaluator_version: int) -> dict | None:
        tid = self._positive_int(trace_id, "trace_id")
        version = self._positive_int(evaluator_version, "evaluator_version")
        row = self.conn.execute(
            "SELECT * FROM agent_turn_evaluations "
            "WHERE trace_id = ? AND evaluator_version = ?",
            (tid, version),
        ).fetchone()
        return dict(row) if row is not None else None

    def upsert(
        self,
        trace_id: int,
        evaluator_version: int,
        status: str,
        checks_json: dict | str,
        metrics_json: dict | str,
    ) -> dict:
        tid = self._positive_int(trace_id, "trace_id")
        version = self._positive_int(evaluator_version, "evaluator_version")
        if status not in {"pass", "warn", "fail"}:
            raise ValueError("evaluation status must be pass, warn, or fail")
        checks = self._json_object(checks_json, "checks_json")
        metrics = self._json_object(metrics_json, "metrics_json")
        trace = self.conn.execute(
            "SELECT 1 FROM agent_turn_traces WHERE id = ?", (tid,)
        ).fetchone()
        if trace is None:
            raise ValueError("trace does not exist")
        created_at = datetime.now().replace(microsecond=0).isoformat()
        self.conn.execute(
            "INSERT INTO agent_turn_evaluations "
            "(trace_id, evaluator_version, status, checks_json, metrics_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trace_id, evaluator_version) DO UPDATE SET "
            "status=excluded.status, checks_json=excluded.checks_json, "
            "metrics_json=excluded.metrics_json, created_at=excluded.created_at",
            (tid, version, status, checks, metrics, created_at),
        )
        self.conn.commit()
        return self.get_for_trace(tid, version)

    def list_for_trace(self, trace_id: int) -> list[dict]:
        tid = self._positive_int(trace_id, "trace_id")
        rows = self.conn.execute(
            "SELECT * FROM agent_turn_evaluations WHERE trace_id = ? "
            "ORDER BY evaluator_version",
            (tid,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _json_object(value: dict | str, field: str) -> str:
        if isinstance(value, dict):
            try:
                parsed = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False)
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be JSON serializable") from None
        elif isinstance(value, str):
            parsed = value
        else:
            raise ValueError(f"{field} must be an object")
        try:
            decoded = json.loads(parsed)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"{field} must be valid JSON") from None
        if not isinstance(decoded, dict):
            raise ValueError(f"{field} must encode a JSON object")
        return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _positive_int(value, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value
