"""Topic Learning Activity（学习活动）公共定义。

一个 Topic（例如 LoRA / Transformer / PPO）可以按不同方式学习：

    theory        理论学习
    code_reading  代码理解
    experiment    小实验
    interview     面试知识
    practice      综合实践

activity_kind 是独立于 task_type / source / status 的正交维度。

本模块只放常量与标签，避免标签在 UI / Planner / Service 里重复。
"""

from __future__ import annotations

ACTIVITY_THEORY = "theory"
ACTIVITY_CODE_READING = "code_reading"
ACTIVITY_EXPERIMENT = "experiment"
ACTIVITY_INTERVIEW = "interview"
ACTIVITY_PRACTICE = "practice"

# 展示顺序 = 学习推进顺序
ALL_ACTIVITY_KINDS: tuple[str, ...] = (
    ACTIVITY_THEORY,
    ACTIVITY_CODE_READING,
    ACTIVITY_EXPERIMENT,
    ACTIVITY_INTERVIEW,
    ACTIVITY_PRACTICE,
)

ACTIVITY_LABELS: dict[str, str] = {
    ACTIVITY_THEORY: "理论",
    ACTIVITY_CODE_READING: "代码理解",
    ACTIVITY_EXPERIMENT: "实验",
    ACTIVITY_INTERVIEW: "面试",
    ACTIVITY_PRACTICE: "综合实践",
}

ACTIVITY_DESCRIPTIONS: dict[str, str] = {
    ACTIVITY_THEORY: "理解核心概念与机制",
    ACTIVITY_CODE_READING: "阅读并解释关键实现",
    ACTIVITY_EXPERIMENT: "动手做最小实验并观察结果",
    ACTIVITY_INTERVIEW: "能用面试语言解释与推导",
    ACTIVITY_PRACTICE: "完成小型综合实践任务",
}


def is_valid_activity_kind(kind: str | None) -> bool:
    return kind in ALL_ACTIVITY_KINDS


def activity_label(kind: str | None) -> str:
    if not kind:
        return ""
    return ACTIVITY_LABELS.get(kind, kind)


def activity_order(kind: str) -> int:
    try:
        return ALL_ACTIVITY_KINDS.index(kind)
    except ValueError:
        return len(ALL_ACTIVITY_KINDS)
