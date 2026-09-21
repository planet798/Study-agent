"""Capability 等级定义（Phase 3）。

固定六级（0–5）：

    0 UNLEARNED   未学习
    1 AWARE       知道概念
    2 EXPLAIN     能够解释
    3 IMPLEMENT   能够写代码
    4 EXPERIMENT  完成独立实验
    5 PROJECT     已在真实项目中使用

重要：
- Level 0 不写数据库；current capability = MAX(active level) 或 0。
- Capability 不是递增进度条，也不由 mastery 阈值推断。
- Level 5 PROJECT 只能由 Practice 层的 PracticeTopicEvidence 经用户显式确认后产生
  （Phase 5）；Task / Assessment / Experiment 路径永远不能产生 Level 5。
"""

from __future__ import annotations

UNLEARNED = 0
AWARE = 1
EXPLAIN = 2
IMPLEMENT = 3
EXPERIMENT = 4
PROJECT = 5

CAPABILITY_LEVELS: dict[int, str] = {
    UNLEARNED: "UNLEARNED",
    AWARE: "AWARE",
    EXPLAIN: "EXPLAIN",
    IMPLEMENT: "IMPLEMENT",
    EXPERIMENT: "EXPERIMENT",
    PROJECT: "PROJECT",
}

CAPABILITY_LABELS: dict[int, str] = {
    UNLEARNED: "未学习",
    AWARE: "知道概念",
    EXPLAIN: "能够解释",
    IMPLEMENT: "能够写代码",
    EXPERIMENT: "完成独立实验",
    PROJECT: "已在真实项目中使用",
}

# evidence_type（canonical）
EVIDENCE_TYPE_LEARNING_ACTIVITY = "learning_activity"
EVIDENCE_TYPE_ASSESSMENT = "assessment"
EVIDENCE_TYPE_EXPERIMENT_OUTCOME = "experiment_outcome"
EVIDENCE_TYPE_MANUAL_VERIFIED_EXPERIMENT = "manual_verified_experiment"
# Phase 5：真实项目使用证据（唯一能产生 Level 5 的来源）
EVIDENCE_TYPE_PRACTICE_PROJECT = "practice_project"

ALL_EVIDENCE_TYPES = (
    EVIDENCE_TYPE_LEARNING_ACTIVITY,
    EVIDENCE_TYPE_ASSESSMENT,
    EVIDENCE_TYPE_EXPERIMENT_OUTCOME,
    EVIDENCE_TYPE_MANUAL_VERIFIED_EXPERIMENT,
    EVIDENCE_TYPE_PRACTICE_PROJECT,
)

MIN_EVIDENCE_LEVEL = 1
MAX_EVIDENCE_LEVEL = 5


def capability_label(level: int | None) -> str:
    if level is None:
        level = UNLEARNED
    return CAPABILITY_LABELS.get(int(level), "未学习")


def capability_name(level: int | None) -> str:
    if level is None:
        level = UNLEARNED
    return CAPABILITY_LEVELS.get(int(level), "UNLEARNED")


def is_valid_evidence_level(level: int) -> bool:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return False
    return MIN_EVIDENCE_LEVEL <= level <= MAX_EVIDENCE_LEVEL
