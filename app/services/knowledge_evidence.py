"""知识掌握证据 → 候选主题优先级/去重的共享计算（Phase 8）。

供 StudyPlanService（规则生成）与 DailyPlannerService（AI 二次校验）共用，
保证“薄弱优先、高掌握不重复”在两条生成链路上行为一致。

核心原则：
- 没有验收证据的知识点不参与任何判断（绝不把默认 0.0 当作“已经证明不会”）；
- mastery_estimate 只是 AI 根据真实作答得到的当前估计，不作为路线控制器：
  - 仅用于“是否针对薄弱点优先安排 / 是否减少重复基础任务”；
  - 不会据此跳过整个 study phase；
  - 也不会因单次 poor 永久锁死某个知识点。
"""

from __future__ import annotations

import json
from typing import Any

# 阈值（保守、可解释）
WEAK_MASTERY_THRESHOLD = 0.5      # mastery 低于该值视为薄弱，需要巩固
HIGH_MASTERY_THRESHOLD = 0.85     # mastery 高于该值且最近验收良好 -> 减少重复基础任务
_GOOD_LEVELS = ("good", "excellent")


def _kp_topic_id(kp: dict) -> int | None:
    tid = kp.get("topic_id")
    return int(tid) if tid is not None else None


def _latest_judged_attempt(assessment_repo, knowledge_point_id: int) -> dict | None:
    """最近一次已判题的验收记录（按 id 升序取最后一个）。"""
    judged = [
        a for a in assessment_repo.list_attempts_for_knowledge_point(
            knowledge_point_id
        )
        if a.get("judge_status") == "judged"
    ]
    return judged[-1] if judged else None


def _attempt_weak_points(attempt: dict | None) -> list[str]:
    if not attempt:
        return []
    try:
        data = json.loads(attempt.get("weak_points_json") or "[]")
    except json.JSONDecodeError:
        return []
    return [str(w) for w in data] if isinstance(data, list) else []


def weak_topic_ids(repo, assessment_repo) -> set[int]:
    """因薄弱（低掌握度或有 weak_points）而应优先安排的 topic_id 集合。"""
    if assessment_repo is None:
        return set()
    out: set[int] = set()
    for kp in assessment_repo.list_knowledge_points():
        if not kp.get("last_assessed_at"):
            continue  # 无真实验收证据：不参与判断
        tid = _kp_topic_id(kp)
        if tid is None:
            continue
        mastery = float(kp.get("mastery_estimate") or 0.0)
        attempt = _latest_judged_attempt(assessment_repo, kp["id"])
        if mastery < WEAK_MASTERY_THRESHOLD or _attempt_weak_points(attempt):
            out.add(tid)
    return out


def skip_topic_ids(repo, assessment_repo) -> set[int]:
    """因“已掌握/复习进行中”应跳过（不要再安排正式任务）的 topic_id 集合。

    高掌握（mastery >= 0.85）且最近验收良好（good/excellent）时，
    减少重复基础任务。
    """
    if assessment_repo is None:
        return set()
    out: set[int] = set()
    for kp in assessment_repo.list_knowledge_points():
        tid = _kp_topic_id(kp)
        if tid is None:
            continue
        # 1) 高掌握且最近良好
        if kp.get("last_assessed_at"):
            mastery = float(kp.get("mastery_estimate") or 0.0)
            attempt = _latest_judged_attempt(assessment_repo, kp["id"])
            if (
                mastery >= HIGH_MASTERY_THRESHOLD
                and attempt is not None
                and attempt.get("result_level") in _GOOD_LEVELS
            ):
                out.add(tid)
                continue
    return out
