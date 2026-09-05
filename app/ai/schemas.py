"""AI 结构化输出定义与校验。

期望模型返回的严格 JSON：

{
  "reasonable": true,          # boolean
  "score": 0.85,               # 0 ~ 1 数字
  "should_postpone": true,     # boolean
  "suggested_date": "2026-09-05",  # YYYY-MM-DD 或 null
  "analysis": "简短中文分析",   # 字符串
  "suggestion": "简短中文建议"  # 字符串
}

非法结构（缺字段 / 类型错 / 取值越界 / 日期格式错）一律抛 AIServiceError，
任何情况下都不会直接使用未验证的模型输出。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .interface import AIServiceError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED_FIELDS = (
    "reasonable",
    "score",
    "should_postpone",
    "suggested_date",
    "analysis",
    "suggestion",
)


@dataclass(frozen=True)
class TaskReview:
    """通过校验的 AI 判断结果。"""

    reasonable: bool
    score: float
    should_postpone: bool
    suggested_date: str | None
    analysis: str
    suggestion: str


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise AIServiceError(f"字段 {field} 必须是布尔值，实际为 {type(value).__name__}")
    return value


def _require_number_in_range(value: object, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AIServiceError(f"字段 {field} 必须是数字，实际为 {type(value).__name__}")
    num = float(value)
    if not (low <= num <= high):
        raise AIServiceError(f"字段 {field} 越界：{num}，应在 [{low}, {high}] 内")
    return num


def _require_str(value: object, field: str, max_len: int = 2000) -> str:
    if not isinstance(value, str):
        raise AIServiceError(f"字段 {field} 必须是字符串，实际为 {type(value).__name__}")
    text = value.strip()
    if not text:
        raise AIServiceError(f"字段 {field} 不能为空")
    if len(text) > max_len:
        raise AIServiceError(f"字段 {field} 过长（{len(text)} 字符）")
    return text


def validate_date(value: object) -> str | None:
    """校验 suggested_date：必须是合法 YYYY-MM-DD 或 None。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AIServiceError(
            f"字段 suggested_date 必须是字符串或 null，实际为 {type(value).__name__}"
        )
    text = value.strip()
    if not _DATE_RE.match(text):
        raise AIServiceError(f"字段 suggested_date 格式错误：'{text}'，应为 YYYY-MM-DD")
    y, m, d = (int(x) for x in text.split("-"))
    if not (1 <= m <= 12 and 1 <= d <= 31):
        raise AIServiceError(f"字段 suggested_date 不是合法日期：'{text}'")
    return text


def parse_review(raw: object) -> TaskReview:
    """把 dict 解析并校验为 TaskReview；任何问题抛 AIServiceError。"""
    if not isinstance(raw, dict):
        raise AIServiceError(f"模型输出必须是 JSON 对象，实际为 {type(raw).__name__}")

    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        raise AIServiceError(f"模型输出缺少字段：{', '.join(missing)}")

    reasonable = _require_bool(raw["reasonable"], "reasonable")
    score = _require_number_in_range(raw["score"], "score", 0.0, 1.0)
    should_postpone = _require_bool(raw["should_postpone"], "should_postpone")
    suggested_date = validate_date(raw["suggested_date"])
    analysis = _require_str(raw["analysis"], "analysis")
    suggestion = _require_str(raw["suggestion"], "suggestion")

    # 建议延期时必须给出可用的 suggested_date
    if should_postpone and suggested_date is None:
        raise AIServiceError("should_postpone 为 true 时必须有 suggested_date")

    return TaskReview(
        reasonable=reasonable,
        score=score,
        should_postpone=should_postpone,
        suggested_date=suggested_date,
        analysis=analysis,
        suggestion=suggestion,
    )


def parse_review_from_json(text: str) -> TaskReview:
    """先把字符串解析为 JSON，再做结构校验。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"模型返回的不是合法 JSON：{e}") from e
    return parse_review(data)


# ============================================================
# AI 动态规划（Daily Planner）输出结构
# ============================================================

MAX_PLANNER_RECOMMENDATIONS = 5
MAX_DAILY_LIMIT = 180


@dataclass(frozen=True)
class RecommendedTask:
    """AI 推荐的计划任务（topic 相关）。"""

    topic_id: int
    title: str
    description: str = ""
    estimated_minutes: int = 0
    priority: int = 1


@dataclass(frozen=True)
class CarryOverTask:
    """AI 建议带过来的延期任务。"""

    task_id: int
    reason: str = ""


@dataclass(frozen=True)
class DailyPlan:
    """通过校验的 AI 规划结果。"""

    reasoning: str
    recommended_tasks: tuple[RecommendedTask, ...]
    carry_over_tasks: tuple[CarryOverTask, ...]
    daily_minutes: int
    adjustment: str


def parse_daily_plan(raw: object) -> DailyPlan:
    """校验并解析 AI 返回的每日规划 JSON。违反任何约束抛 AIServiceError。"""
    if not isinstance(raw, dict):
        raise AIServiceError(
            f"AI 规划输出必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    for field_name in ("reasoning", "recommended_tasks", "carry_over_tasks",
                       "daily_minutes", "adjustment"):
        if field_name not in raw:
            raise AIServiceError(f"AI 规划输出缺少字段：{field_name}")

    reasoning = _require_str(raw["reasoning"], "reasoning", max_len=800)
    adjustment = _require_str(raw["adjustment"], "adjustment", max_len=800)
    daily_minutes = _require_number_in_range(
        raw["daily_minutes"], "daily_minutes", 1, MAX_DAILY_LIMIT
    )
    daily_minutes_int = int(daily_minutes)

    recs = _validate_recommended(raw["recommended_tasks"])
    carries = _validate_carry_over(raw["carry_over_tasks"])
    return DailyPlan(
        reasoning=reasoning,
        recommended_tasks=recs,
        carry_over_tasks=carries,
        daily_minutes=daily_minutes_int,
        adjustment=adjustment,
    )


def _validate_recommended(value: object) -> tuple[RecommendedTask, ...]:
    if not isinstance(value, list):
        raise AIServiceError("recommended_tasks 必须是数组")
    if not (1 <= len(value) <= MAX_PLANNER_RECOMMENDATIONS):
        raise AIServiceError(
            f"recommended_tasks 数量必须在 1~{MAX_PLANNER_RECOMMENDATIONS} 之间"
        )
    out: list[RecommendedTask] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise AIServiceError(f"recommended_tasks[{i}] 必须是对象")
        for field_name in ("topic_id", "title", "estimated_minutes"):
            if field_name not in item:
                raise AIServiceError(
                    f"recommended_tasks[{i}] 缺少字段：{field_name}"
                )
        topic_id = item["topic_id"]
        if isinstance(topic_id, bool) or not isinstance(topic_id, (int, float)):
            raise AIServiceError(f"recommended_tasks[{i}].topic_id 必须是数字 id")
        topic_id = int(topic_id)
        title = _require_str(item["title"], f"recommended_tasks[{i}].title")
        minutes = _require_number_in_range(
            item.get("estimated_minutes"), f"recommended_tasks[{i}].estimated_minutes",
            1, MAX_DAILY_LIMIT,
        )
        minutes = int(minutes)
        desc = item.get("description", "")
        if not isinstance(desc, str):
            desc = ""
        prio = item.get("priority", 1)
        if isinstance(prio, bool) or not isinstance(prio, (int, float)):
            prio = 1
        prio = int(prio)
        out.append(
            RecommendedTask(
                topic_id=topic_id,
                title=title,
                description=desc,
                estimated_minutes=minutes,
                priority=prio,
            )
        )
    return tuple(out)


def _validate_carry_over(value: object) -> tuple[CarryOverTask, ...]:
    if not isinstance(value, list):
        raise AIServiceError("carry_over_tasks 必须是数组")
    out: list[CarryOverTask] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or "task_id" not in item:
            raise AIServiceError(f"carry_over_tasks[{i}] 格式错误")
        task_id = item["task_id"]
        if isinstance(task_id, bool) or not isinstance(task_id, (int, float)):
            raise AIServiceError(f"carry_over_tasks[{i}].task_id 必须是数字 id")
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            reason = ""
        out.append(CarryOverTask(task_id=int(task_id), reason=reason.strip()))
    return tuple(out)


def parse_daily_plan_from_json(text: str) -> DailyPlan:
    """字符串 -> JSON -> 校验。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"AI 规划返回的不是合法 JSON：{e}") from e
    return parse_daily_plan(data)


# ============================================================
# AI 学习总结（周 / 月）输出结构
# ============================================================


@dataclass(frozen=True)
class WeeklySummary:
    overview: str
    strengths: tuple[str, ...]
    problems: tuple[str, ...]
    recommendations: tuple[str, ...]
    next_week_focus: tuple[str, ...]


@dataclass(frozen=True)
class MonthlySummary:
    overview: str
    progress: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    recommendations: tuple[str, ...]
    next_month_focus: tuple[str, ...]


def _require_str_list(value: object, field: str, max_items: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AIServiceError(f"字段 {field} 必须是数组")
    out: list[str] = []
    for i, item in enumerate(value[:max_items]):
        if not isinstance(item, str) or not item.strip():
            raise AIServiceError(f"字段 {field}[{i}] 必须是非空字符串")
        out.append(item.strip())
    return tuple(out)


def parse_weekly_summary(raw: object) -> WeeklySummary:
    if not isinstance(raw, dict):
        raise AIServiceError("周总结输出必须是 JSON 对象")
    for field_name in ("overview", "strengths", "problems", "recommendations",
                       "next_week_focus"):
        if field_name not in raw:
            raise AIServiceError(f"周总结缺少字段：{field_name}")
    return WeeklySummary(
        overview=_require_str(raw["overview"], "overview", max_len=1500),
        strengths=_require_str_list(raw["strengths"], "strengths"),
        problems=_require_str_list(raw["problems"], "problems"),
        recommendations=_require_str_list(raw["recommendations"], "recommendations"),
        next_week_focus=_require_str_list(raw["next_week_focus"], "next_week_focus"),
    )


def parse_monthly_summary(raw: object) -> MonthlySummary:
    if not isinstance(raw, dict):
        raise AIServiceError("月总结输出必须是 JSON 对象")
    for field_name in ("overview", "progress", "strengths", "weaknesses",
                       "recommendations", "next_month_focus"):
        if field_name not in raw:
            raise AIServiceError(f"月总结缺少字段：{field_name}")
    return MonthlySummary(
        overview=_require_str(raw["overview"], "overview", max_len=1500),
        progress=_require_str(raw["progress"], "progress", max_len=1500),
        strengths=_require_str_list(raw["strengths"], "strengths"),
        weaknesses=_require_str_list(raw["weaknesses"], "weaknesses"),
        recommendations=_require_str_list(raw["recommendations"], "recommendations"),
        next_month_focus=_require_str_list(raw["next_month_focus"], "next_month_focus"),
    )


def parse_weekly_from_json(text: str) -> WeeklySummary:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"AI 周总结不是合法 JSON：{e}") from e
    return parse_weekly_summary(data)


def parse_monthly_from_json(text: str) -> MonthlySummary:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"AI 月总结不是合法 JSON：{e}") from e
    return parse_monthly_summary(data)
