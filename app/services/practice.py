"""Practice / Project 公共定义（Phase 4）。

Practice 层独立于 LearningRoute：项目 ↔ Route / Skill / Topic 均为 N:N。

DB 存稳定英文 enum；UI 显示中文（见 *_LABELS）。
"""

from __future__ import annotations

# ---------- project_type ----------
PROJECT_TYPE_KAGGLE = "kaggle"
PROJECT_TYPE_GITHUB = "github"
PROJECT_TYPE_PAPER_REPRODUCTION = "paper_reproduction"
PROJECT_TYPE_STUDY_AGENT = "study_agent"
PROJECT_TYPE_LLM_TRAINING = "llm_training"
PROJECT_TYPE_AGENT = "agent"
PROJECT_TYPE_RECOMMENDATION_SEARCH = "recommendation_search"
PROJECT_TYPE_BENCHMARK_EVALUATION = "benchmark_evaluation"
PROJECT_TYPE_OTHER = "other"

ALL_PROJECT_TYPES: tuple[str, ...] = (
    PROJECT_TYPE_KAGGLE,
    PROJECT_TYPE_GITHUB,
    PROJECT_TYPE_PAPER_REPRODUCTION,
    PROJECT_TYPE_STUDY_AGENT,
    PROJECT_TYPE_LLM_TRAINING,
    PROJECT_TYPE_AGENT,
    PROJECT_TYPE_RECOMMENDATION_SEARCH,
    PROJECT_TYPE_BENCHMARK_EVALUATION,
    PROJECT_TYPE_OTHER,
)

PROJECT_TYPE_LABELS: dict[str, str] = {
    PROJECT_TYPE_KAGGLE: "Kaggle",
    PROJECT_TYPE_GITHUB: "GitHub / 开源项目",
    PROJECT_TYPE_PAPER_REPRODUCTION: "论文复现",
    PROJECT_TYPE_STUDY_AGENT: "Study-Agent",
    PROJECT_TYPE_LLM_TRAINING: "LLM 微调",
    PROJECT_TYPE_AGENT: "Agent 项目",
    PROJECT_TYPE_RECOMMENDATION_SEARCH: "推荐 / 搜索",
    PROJECT_TYPE_BENCHMARK_EVALUATION: "Benchmark / Evaluation",
    PROJECT_TYPE_OTHER: "其他",
}

# ---------- project status ----------
STATUS_PLANNED = "planned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED = "archived"

ALL_PROJECT_STATUSES: tuple[str, ...] = (
    STATUS_PLANNED, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_ARCHIVED,
)

PROJECT_STATUS_LABELS: dict[str, str] = {
    STATUS_PLANNED: "计划中",
    STATUS_IN_PROGRESS: "进行中",
    STATUS_COMPLETED: "已完成",
    STATUS_ARCHIVED: "已归档",
}

# ---------- source ----------
SOURCE_MANUAL = "manual"
SOURCE_IMPORTED = "imported"
SOURCE_AI = "ai"  # 预留语义；Phase 4 禁止 AI 自动创建
ALL_PROJECT_SOURCES: tuple[str, ...] = (SOURCE_MANUAL, SOURCE_IMPORTED, SOURCE_AI)

# ---------- milestone status ----------
MILESTONE_TODO = "todo"
MILESTONE_IN_PROGRESS = "in_progress"
MILESTONE_DONE = "done"

ALL_MILESTONE_STATUSES: tuple[str, ...] = (
    MILESTONE_TODO, MILESTONE_IN_PROGRESS, MILESTONE_DONE,
)

MILESTONE_STATUS_LABELS: dict[str, str] = {
    MILESTONE_TODO: "待办",
    MILESTONE_IN_PROGRESS: "进行中",
    MILESTONE_DONE: "已完成",
}

# ---------- output_type ----------
OUTPUT_TYPES: tuple[str, ...] = (
    "repository", "code", "result", "benchmark", "checkpoint",
    "report", "readme", "demo", "paper_reproduction", "dataset", "other",
)

OUTPUT_TYPE_LABELS: dict[str, str] = {
    "repository": "Repository",
    "code": "代码",
    "result": "实验结果",
    "benchmark": "Benchmark",
    "checkpoint": "Checkpoint",
    "report": "技术报告",
    "readme": "README",
    "demo": "Demo",
    "paper_reproduction": "论文复现结果",
    "dataset": "Dataset",
    "other": "其他",
}


def is_valid_project_type(value: str) -> bool:
    return value in ALL_PROJECT_TYPES


def is_valid_project_status(value: str) -> bool:
    return value in ALL_PROJECT_STATUSES


def is_valid_milestone_status(value: str) -> bool:
    return value in ALL_MILESTONE_STATUSES


def is_valid_output_type(value: str) -> bool:
    return value in OUTPUT_TYPES


def project_type_label(value: str | None) -> str:
    return PROJECT_TYPE_LABELS.get(value or "", value or "")


def project_status_label(value: str | None) -> str:
    return PROJECT_STATUS_LABELS.get(value or "", value or "")


def milestone_status_label(value: str | None) -> str:
    return MILESTONE_STATUS_LABELS.get(value or "", value or "")


def output_type_label(value: str | None) -> str:
    return OUTPUT_TYPE_LABELS.get(value or "", value or "")
