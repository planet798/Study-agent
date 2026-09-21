"""CapabilityService（Phase 3）。

从已有真实证据提取能力等级：
- Task(正式学习, done, 有 kp)      → AWARE(1)
- Assessment(judged, 题型+单题通过) → EXPLAIN(2) / IMPLEMENT(3)
- learning_outcomes(kind=experiment) + done experiment task + 真实产物 → EXPERIMENT(4)
- PracticeTopicEvidence(用户显式确认) → PROJECT(5)（Phase 5）

PROJECT 的**唯一** production path 是 `sync_from_practice_topic_evidence`；
Task / Assessment / Experiment 三条路径永远不能产生 level=5。

与 mastery 彻底分开：
- 不使用 mastery 阈值；
- 不修改 mastery / review；
- capability 不自动下降（后续失败不降级）。

current capability = MAX(active level)，无证据 = 0（UNLEARNED，不写库）。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..database.assessment_repository import AssessmentRepository
from ..database.capability_repository import CapabilityEvidenceRepository
from ..database.practice_repository import (
    PracticeProjectRepository,
    PracticeTopicEvidenceRepository,
)
from ..database.repository import TaskRepository
from ..database.skill_repository import LearningOutcomeRepository
from .capability import (
    AWARE,
    EVIDENCE_TYPE_ASSESSMENT,
    EVIDENCE_TYPE_EXPERIMENT_OUTCOME,
    EVIDENCE_TYPE_LEARNING_ACTIVITY,
    EVIDENCE_TYPE_PRACTICE_PROJECT,
    EXPLAIN,
    IMPLEMENT,
    PROJECT,
    capability_label,
    capability_name,
    is_valid_evidence_level,
)
from .capability_extractors import extract_assessment_capability

# Task → AWARE 的正式学习任务类型
_FORMAL_TASK_TYPE = "new"
_FORMAL_SOURCES = ("generated", "manual")


def _guard_non_practice_level(level: int) -> int:
    """非 Practice 路径不得产生 Level 5（防回归）。"""
    level = int(level)
    if level >= PROJECT:
        raise ValueError(
            "只有 PracticeTopicEvidence 路径能产生 Level 5 PROJECT"
        )
    return level


class CapabilityService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        evidence_repo: CapabilityEvidenceRepository | None = None,
        task_repo: TaskRepository | None = None,
        assessment_repo: AssessmentRepository | None = None,
        outcome_repo: LearningOutcomeRepository | None = None,
        practice_evidence_repo: PracticeTopicEvidenceRepository | None = None,
        practice_project_repo: PracticeProjectRepository | None = None,
    ):
        self.conn = conn
        self.repo = evidence_repo or CapabilityEvidenceRepository(conn)
        self.task_repo = task_repo or TaskRepository(conn)
        self.assessment_repo = assessment_repo or AssessmentRepository(conn)
        self.outcome_repo = outcome_repo or LearningOutcomeRepository(conn)
        self.practice_evidence_repo = \
            practice_evidence_repo or PracticeTopicEvidenceRepository(conn)
        self.practice_project_repo = \
            practice_project_repo or PracticeProjectRepository(conn)

    # ================= current capability =================

    def get_current_level(self, knowledge_point_id: int) -> int:
        return self.repo.max_level_by_kp(knowledge_point_id)

    def get_current_capability(self, knowledge_point_id: int) -> dict:
        level = self.get_current_level(knowledge_point_id)
        evidence = self.repo.list_active_by_kp(knowledge_point_id)
        return {
            "knowledge_point_id": int(knowledge_point_id),
            "level": level,
            "name": capability_name(level),
            "label": capability_label(level),
            "evidence_count": len(evidence),
            "has_evidence": bool(evidence),
        }

    def list_evidence(self, knowledge_point_id: int) -> list[dict]:
        return self.repo.list_by_kp(knowledge_point_id, active_only=False)

    def get_route_capability_summary(self, route_id: int) -> dict:
        counts = self.repo.count_by_level_for_route(route_id)
        return {
            "route_id": int(route_id),
            "total": sum(counts.values()),
            "level_counts": counts,
        }

    def revoke_evidence(self, evidence_id: int, reason: str = "") -> Optional[dict]:
        return self.repo.revoke(evidence_id, reason)

    # ================= Task → AWARE =================

    def sync_from_task(self, task_id: int) -> Optional[dict]:
        task = self.task_repo.get(task_id)
        return self.sync_from_task_obj(task)

    def sync_from_task_obj(self, task) -> Optional[dict]:
        if task is None:
            return None
        if task.status != "done":
            return None
        if task.knowledge_point_id is None:
            return None
        if task.task_type != _FORMAL_TASK_TYPE:
            return None  # review / manual todo 不产生
        if task.source not in _FORMAL_SOURCES:
            return None
        return self.repo.create_or_update_by_key(
            knowledge_point_id=task.knowledge_point_id,
            capability_level=_guard_non_practice_level(AWARE),
            evidence_type=EVIDENCE_TYPE_LEARNING_ACTIVITY,
            evidence_key=f"task:{task.id}",
            source_task_id=task.id,
            description=f"完成学习活动：{task.title}",
            details={
                "task_id": task.id,
                "title": task.title,
                "learning_activity_kind": task.learning_activity_kind,
                "component_id": task.component_id,
            },
        )

    # ================= Assessment → EXPLAIN / IMPLEMENT =================

    def sync_from_assessment(self, attempt_id: int) -> Optional[dict]:
        attempt = self.assessment_repo.get_attempt(attempt_id)
        if attempt is None:
            return None
        extracted = extract_assessment_capability(attempt)
        if extracted is None:
            return None
        kp_id = attempt.get("knowledge_point_id")
        if kp_id is None:
            return None
        level = extracted["level"]
        details = dict(extracted["details"])
        details["attempt_id"] = attempt_id
        return self.repo.create_or_update_by_key(
            knowledge_point_id=kp_id,
            capability_level=_guard_non_practice_level(level),
            evidence_type=EVIDENCE_TYPE_ASSESSMENT,
            evidence_key=f"assessment:{attempt_id}",
            assessment_attempt_id=attempt_id,
            description=f"验收证明：{capability_label(level)}",
            details=details,
        )

    # ================= experiment outcome → EXPERIMENT =================

    def _resolve_outcome_kp(self, outcome: dict) -> tuple[int | None, str]:
        """确定 outcome 对应的 kp（多来源必须一致，否则 conflict）。"""
        candidates: set[int] = set()
        if outcome.get("linked_kp_id") is not None:
            candidates.add(int(outcome["linked_kp_id"]))
        task = None
        if outcome.get("task_id") is not None:
            task = self.task_repo.get(int(outcome["task_id"]))
            if task is not None and task.knowledge_point_id is not None:
                candidates.add(int(task.knowledge_point_id))
        if outcome.get("linked_topic_id") is not None:
            kp = self.assessment_repo.get_knowledge_point_by_topic(
                int(outcome["linked_topic_id"])
            )
            if kp is not None:
                candidates.add(int(kp["id"]))
        if not candidates:
            return None, "no_kp_linkage"
        if len(candidates) > 1:
            return None, "conflicting_kp_linkage"
        # 必须有关联的 done experiment task
        if task is None:
            return None, "no_linked_task"
        if task.status != "done":
            return None, "task_not_done"
        if task.learning_activity_kind != "experiment":
            return None, "task_not_experiment_activity"
        return next(iter(candidates)), ""

    @staticmethod
    def has_real_experiment_evidence(outcome: dict) -> bool:
        """真实实验证据：结果说明 + 至少一项产物字段。"""
        content = (outcome.get("content") or "").strip()
        metrics = outcome.get("metrics") or {}
        git_commit = (outcome.get("git_commit") or "").strip()
        github_url = (outcome.get("github_url") or "").strip()
        dataset = (outcome.get("dataset") or "").strip()
        has_result = bool(content)
        has_artifact = bool(metrics) or bool(git_commit) or bool(github_url) \
            or bool(dataset)
        return has_result and has_artifact

    def sync_from_experiment_outcome(self, outcome_id: int) -> Optional[dict]:
        outcome = self.outcome_repo.get(outcome_id)
        if outcome is None or outcome.get("kind") != "experiment":
            return None
        if not self.has_real_experiment_evidence(outcome):
            return None
        kp_id, reason = self._resolve_outcome_kp(outcome)
        if kp_id is None:
            return None
        return self.repo.create_or_update_by_key(
            knowledge_point_id=kp_id,
            capability_level=_guard_non_practice_level(4),  # EXPERIMENT
            evidence_type=EVIDENCE_TYPE_EXPERIMENT_OUTCOME,
            evidence_key=f"experiment_outcome:{outcome_id}",
            learning_outcome_id=outcome_id,
            source_task_id=outcome.get("task_id"),
            description=f"实验成果：{outcome.get('title') or ''}",
            details={
                "outcome_id": outcome_id,
                "kind": outcome.get("kind"),
                "has_metrics": bool(outcome.get("metrics")),
                "has_git": bool((outcome.get("git_commit") or "").strip()),
                "has_url": bool((outcome.get("github_url") or "").strip()),
            },
        )

    # ================= Backfill / preview =================

    def preview(self) -> dict:
        """只读预览：列出会产生的 capability evidence（不写库）。"""
        result = {
            "aware_from_tasks": 0,
            "explain_from_assessments": 0,
            "implement_from_assessments": 0,
            "experiment_from_outcomes": 0,
            "skipped_assessment_insufficient_data": 0,
            "skipped_outcome_insufficient_evidence": 0,
            "conflicts": 0,
            "entries": [],
        }
        # tasks
        rows = self.conn.execute(
            "SELECT id FROM tasks WHERE status='done' "
            "AND knowledge_point_id IS NOT NULL AND task_type='new' "
            "AND source IN ('generated','manual') ORDER BY id"
        ).fetchall()
        for (tid,) in rows:
            result["aware_from_tasks"] += 1
            result["entries"].append({"key": f"task:{tid}", "level": 1})
        # assessments
        attempts = self.assessment_repo.list_attempts()
        for a in attempts:
            extracted = extract_assessment_capability(a)
            if extracted is None:
                if a.get("judge_status") == "judged":
                    result["skipped_assessment_insufficient_data"] += 1
                continue
            if extracted["level"] == IMPLEMENT:
                result["implement_from_assessments"] += 1
            else:
                result["explain_from_assessments"] += 1
            result["entries"].append({
                "key": f"assessment:{a['id']}", "level": extracted["level"],
            })
        # experiment outcomes
        outcomes = self.outcome_repo.list_all()
        for o in outcomes:
            if o.get("kind") != "experiment":
                continue
            if not self.has_real_experiment_evidence(o):
                result["skipped_outcome_insufficient_evidence"] += 1
                continue
            kp_id, reason = self._resolve_outcome_kp(o)
            if kp_id is None:
                result["conflicts"] += 1
                continue
            result["experiment_from_outcomes"] += 1
            result["entries"].append({
                "key": f"experiment_outcome:{o['id']}", "level": 4,
            })
        return result

    def backfill(self) -> dict:
        """幂等 backfill（与 preview 同源，但会写库）。"""
        stats = {
            "aware_from_tasks": 0,
            "explain_from_assessments": 0,
            "implement_from_assessments": 0,
            "experiment_from_outcomes": 0,
            "skipped_assessment_insufficient_data": 0,
            "skipped_outcome_insufficient_evidence": 0,
            "conflicts": 0,
        }
        rows = self.conn.execute(
            "SELECT id FROM tasks WHERE status='done' "
            "AND knowledge_point_id IS NOT NULL AND task_type='new' "
            "AND source IN ('generated','manual') ORDER BY id"
        ).fetchall()
        for (tid,) in rows:
            if self.sync_from_task(tid) is not None:
                stats["aware_from_tasks"] += 1
        for a in self.assessment_repo.list_attempts():
            extracted = extract_assessment_capability(a)
            if extracted is None:
                if a.get("judge_status") == "judged":
                    stats["skipped_assessment_insufficient_data"] += 1
                continue
            if self.sync_from_assessment(a["id"]) is not None:
                if extracted["level"] == IMPLEMENT:
                    stats["implement_from_assessments"] += 1
                else:
                    stats["explain_from_assessments"] += 1
        for o in self.outcome_repo.list_all():
            if o.get("kind") != "experiment":
                continue
            if not self.has_real_experiment_evidence(o):
                stats["skipped_outcome_insufficient_evidence"] += 1
                continue
            if self.sync_from_experiment_outcome(o["id"]) is not None:
                stats["experiment_from_outcomes"] += 1
            else:
                stats["conflicts"] += 1
        return stats

    # ================= PracticeTopicEvidence → PROJECT（Phase 5） =================

    def sync_from_practice_topic_evidence(
        self, practice_topic_evidence_id: int, commit: bool = True
    ) -> Optional[dict]:
        """**唯一合法产生 Level 5 PROJECT 的 production path**。

        只读取用户已确认的 PracticeTopicEvidence + 其 Outputs；
        不自动遍历项目 Topic，也不因为 project completed 而触发。
        其它 sync（task / assessment / experiment）永远不能产生 level=5。
        """
        evidence = self.practice_evidence_repo.get(practice_topic_evidence_id)
        if evidence is None or not evidence.get("is_active"):
            return None
        kp_id = evidence.get("knowledge_point_id")
        if kp_id is None:
            return None
        project = self.practice_project_repo.get(evidence["project_id"])
        output_ids = self.practice_evidence_repo.list_output_ids(
            practice_topic_evidence_id
        )
        project_name = (project or {}).get("name", "")
        topic = self.conn.execute(
            "SELECT name FROM study_topics WHERE id = ?",
            (int(evidence["topic_id"]),),
        ).fetchone()
        topic_name = topic[0] if topic else ""
        return self.repo.create_or_update_by_key(
            knowledge_point_id=int(kp_id),
            capability_level=PROJECT,
            evidence_type=EVIDENCE_TYPE_PRACTICE_PROJECT,
            evidence_key=f"practice_topic_evidence:{int(practice_topic_evidence_id)}",
            practice_topic_evidence_id=int(practice_topic_evidence_id),
            description=f"已在真实项目中使用：{project_name}",
            details={
                "practice_topic_evidence_id": int(practice_topic_evidence_id),
                "project_id": int(evidence["project_id"]),
                "topic_id": int(evidence["topic_id"]),
                "topic_name": topic_name,
                "output_ids": output_ids,
            },
            commit=commit,
        )

    # ================= 其它路径禁止产生 PROJECT =================

    @staticmethod
    def can_generate_project() -> bool:
        """Phase 5：Level 5 现在可由 PracticeTopicEvidence 显式确认后产生。

        注意：这不代表 task / assessment / experiment 路径可以产生 level=5；
        它们只能产生 level<=4。
        """
        return True

    # 保留旧名以便外部检查（语义与 can_generate_project 相同）
    @staticmethod
    def project_requires_practice_evidence() -> bool:
        return True
