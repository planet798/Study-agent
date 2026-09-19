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
# AI 学习总结（月）输出结构
# ============================================================


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


def parse_monthly_from_json(text: str) -> MonthlySummary:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"AI 月总结不是合法 JSON：{e}") from e
    return parse_monthly_summary(data)


# ============================================================
# AI 验收题生成（Assessment）输出结构
# ============================================================

# 允许的客观题型（不允许“自评掌握度”类题目）
ASSESSMENT_QUESTION_TYPES = (
    "concept",        # 概念解释
    "code_reading",   # 代码阅读
    "coding",         # 编程实现
    "debug",          # Debug / 错误分析
    "scenario",       # 简单应用场景
)

MAX_ASSESSMENT_QUESTIONS = 10
MAX_ASSESSMENT_POINTS = 20


@dataclass(frozen=True)
class AssessmentQuestion:
    """一道通过校验的客观验收题。"""

    question: str
    type: str
    expected_points: int


@dataclass(frozen=True)
class AssessmentQuestionSet:
    """一组验收题（围绕一个知识点）。"""

    questions: tuple[AssessmentQuestion, ...]

    def question_dicts(self) -> list[dict]:
        """转成可落库的 dict 列表。"""
        return [
            {
                "question": q.question,
                "type": q.type,
                "expected_points": q.expected_points,
            }
            for q in self.questions
        ]

    def questions_json(self) -> str:
        """序列化为 questions_json（供 assessment_attempts 保存）。"""
        return json.dumps(self.question_dicts(), ensure_ascii=False)


def parse_assessment_questions(raw: object) -> AssessmentQuestionSet:
    """把 dict 解析并校验为验收题集合；任何问题抛 AIServiceError。"""
    if not isinstance(raw, dict):
        raise AIServiceError(
            f"验收题输出必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    if "questions" not in raw:
        raise AIServiceError("验收题输出缺少字段：questions")

    items = raw["questions"]
    if not isinstance(items, list):
        raise AIServiceError("字段 questions 必须是数组")
    if not (1 <= len(items) <= MAX_ASSESSMENT_QUESTIONS):
        raise AIServiceError(
            f"questions 数量必须在 1~{MAX_ASSESSMENT_QUESTIONS} 之间"
        )

    out: list[AssessmentQuestion] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise AIServiceError(f"questions[{i}] 必须是对象")
        for field_name in ("question", "type", "expected_points"):
            if field_name not in item:
                raise AIServiceError(f"questions[{i}] 缺少字段：{field_name}")

        question = _require_str(item["question"], f"questions[{i}].question")
        qtype = _require_str(item["type"], f"questions[{i}].type", max_len=40)
        if qtype not in ASSESSMENT_QUESTION_TYPES:
            raise AIServiceError(
                f"questions[{i}].type 非法：{qtype}，"
                f"允许值：{', '.join(ASSESSMENT_QUESTION_TYPES)}"
            )
        points = _require_number_in_range(
            item["expected_points"],
            f"questions[{i}].expected_points",
            1.0,
            float(MAX_ASSESSMENT_POINTS),
        )
        out.append(
            AssessmentQuestion(
                question=question,
                type=qtype,
                expected_points=int(points),
            )
        )
    return AssessmentQuestionSet(questions=tuple(out))


def parse_assessment_questions_from_json(text: str) -> AssessmentQuestionSet:
    """字符串 -> JSON -> 校验。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"验收题返回的不是合法 JSON：{e}") from e
    return parse_assessment_questions(data)


# ============================================================
# AI 验收判题（Assessment grading）输出结构
# ============================================================

# 每题判定结果
ASSESSMENT_VERDICTS = ("correct", "partial", "incorrect")

# 整体验收等级（由 AI 根据实际作答推断）
ASSESSMENT_RESULT_LEVELS = ("excellent", "good", "ok", "poor")

MAX_ASSESSMENT_JUDGMENTS = 10
MAX_WEAK_POINTS = 20


@dataclass(frozen=True)
class QuestionJudgment:
    """单题的判题结果。"""

    question_index: int
    verdict: str       # correct / partial / incorrect
    reason: str        # 简要理由


@dataclass(frozen=True)
class AssessmentJudgment:
    """一组验收题的 AI 判题结果。"""

    question_judgments: tuple[QuestionJudgment, ...]
    weak_points: tuple[str, ...]
    result_level: str   # excellent / good / ok / poor
    mastery_estimate: float  # 0~1，由 AI 基于证据推断

    def to_dict(self) -> dict:
        return {
            "questions": [
                {
                    "question_index": j.question_index,
                    "verdict": j.verdict,
                    "reason": j.reason,
                }
                for j in self.question_judgments
            ],
            "weak_points": list(self.weak_points),
            "result_level": self.result_level,
            "mastery_estimate": self.mastery_estimate,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def parse_assessment_judgment(raw: object) -> AssessmentJudgment:
    """把 dict 解析并校验为判题结果；任何问题抛 AIServiceError。"""
    if not isinstance(raw, dict):
        raise AIServiceError(
            f"判题输出必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    for field_name in (
        "questions",
        "weak_points",
        "result_level",
        "mastery_estimate",
    ):
        if field_name not in raw:
            raise AIServiceError(f"判题输出缺少字段：{field_name}")

    items = raw["questions"]
    if not isinstance(items, list):
        raise AIServiceError("字段 questions 必须是数组")
    if not (1 <= len(items) <= MAX_ASSESSMENT_JUDGMENTS):
        raise AIServiceError(
            f"questions 数量必须在 1~{MAX_ASSESSMENT_JUDGMENTS} 之间"
        )

    judgments: list[QuestionJudgment] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise AIServiceError(f"questions[{i}] 必须是对象")
        for field_name in ("question_index", "verdict", "reason"):
            if field_name not in item:
                raise AIServiceError(f"questions[{i}] 缺少字段：{field_name}")
        idx = item["question_index"]
        if isinstance(idx, bool) or not isinstance(idx, (int, float)):
            raise AIServiceError(f"questions[{i}].question_index 必须是数字下标")
        idx = int(idx)
        if idx < 0:
            raise AIServiceError(f"questions[{i}].question_index 不能为负")
        verdict = _require_str(item["verdict"], f"questions[{i}].verdict", max_len=20)
        if verdict not in ASSESSMENT_VERDICTS:
            raise AIServiceError(
                f"questions[{i}].verdict 非法：{verdict}，"
                f"允许值：{', '.join(ASSESSMENT_VERDICTS)}"
            )
        reason = _require_str(item["reason"], f"questions[{i}].reason", max_len=1000)
        judgments.append(
            QuestionJudgment(question_index=idx, verdict=verdict, reason=reason)
        )

    weak_list = raw["weak_points"]
    if not isinstance(weak_list, list):
        raise AIServiceError("字段 weak_points 必须是数组")
    if len(weak_list) > MAX_WEAK_POINTS:
        raise AIServiceError(f"weak_points 数量不能超过 {MAX_WEAK_POINTS}")
    weak_points: list[str] = []
    for i, w in enumerate(weak_list):
        weak_points.append(_require_str(w, f"weak_points[{i}]", max_len=200))

    result_level = _require_str(raw["result_level"], "result_level", max_len=20)
    if result_level not in ASSESSMENT_RESULT_LEVELS:
        raise AIServiceError(
            f"result_level 非法：{result_level}，"
            f"允许值：{', '.join(ASSESSMENT_RESULT_LEVELS)}"
        )

    mastery = _require_number_in_range(
        raw["mastery_estimate"], "mastery_estimate", 0.0, 1.0
    )
    return AssessmentJudgment(
        question_judgments=tuple(judgments),
        weak_points=tuple(weak_points),
        result_level=result_level,
        mastery_estimate=round(float(mastery), 4),
    )


def parse_assessment_judgment_from_json(text: str) -> AssessmentJudgment:
    """字符串 -> JSON -> 校验。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"判题返回的不是合法 JSON：{e}") from e
    return parse_assessment_judgment(data)


# ============================================================
# AI 学习路线草稿（Route Builder）输出结构（Phase F）
# ============================================================
#
# 只生成课程结构草稿；不包含任何数据库 id / route_id / phase_id / topic_id。
# 规模限制防止一次生成过多内容；任何越界都抛 AIServiceError，绝不部分写库。

MIN_ROUTE_PHASES = 2
MAX_ROUTE_PHASES = 8
MIN_PHASE_TOPICS = 2
MAX_PHASE_TOPICS = 12
MAX_ROUTE_TOPICS = 40
MIN_TOPIC_MINUTES = 10
MAX_TOPIC_MINUTES = 180
MIN_TOPIC_PRIORITY = 1
MAX_TOPIC_PRIORITY = 5


def normalize_draft_name(name: str) -> str:
    """仅规范化完全一致的名称（去首尾/折叠空白/小写）；不做模糊合并。"""
    return " ".join((name or "").split()).lower()


@dataclass(frozen=True)
class AITopicDraft:
    name: str
    description: str
    estimated_minutes: int
    priority: int
    order: int


@dataclass(frozen=True)
class AIPhaseDraft:
    name: str
    goal: str
    order: int
    topics: tuple[AITopicDraft, ...]


@dataclass(frozen=True)
class AIRouteDraft:
    route_name: str
    plan_name: str
    summary: str
    phases: tuple[AIPhaseDraft, ...]

    @property
    def topic_count(self) -> int:
        return sum(len(p.topics) for p in self.phases)


def _require_int(value: object, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AIServiceError(f"字段 {field} 必须是整数，实际为 {type(value).__name__}")
    num = int(value)
    if not (low <= num <= high):
        raise AIServiceError(f"字段 {field} 越界：{num}，应在 [{low}, {high}] 内")
    return num


def parse_route_draft(raw: object) -> AIRouteDraft:
    """校验并解析 AI 返回的学习路线草稿 JSON。"""
    if not isinstance(raw, dict):
        raise AIServiceError(
            f"路线草稿必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    route_name = _require_str(raw.get("route_name"), "route_name", max_len=100)
    plan_name = _require_str(raw.get("plan_name"), "plan_name", max_len=120)
    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    raw_phases = raw.get("phases")
    if not isinstance(raw_phases, list):
        raise AIServiceError("phases 必须是数组")
    if not (MIN_ROUTE_PHASES <= len(raw_phases) <= MAX_ROUTE_PHASES):
        raise AIServiceError(
            f"phases 数量必须在 {MIN_ROUTE_PHASES}~{MAX_ROUTE_PHASES} 之间"
        )

    phases: list[AIPhaseDraft] = []
    phase_names: set[str] = set()
    topic_names: set[str] = set()
    total_topics = 0
    for pi, p in enumerate(raw_phases):
        if not isinstance(p, dict):
            raise AIServiceError(f"phases[{pi}] 必须是对象")
        pname = _require_str(p.get("name"), f"phases[{pi}].name", max_len=100)
        key = normalize_draft_name(pname)
        if key in phase_names:
            raise AIServiceError(f"阶段名称重复：{pname}")
        phase_names.add(key)
        goal = p.get("goal", "")
        if not isinstance(goal, str):
            goal = ""
        order = _require_int(
            p.get("order", pi + 1), f"phases[{pi}].order", 1, 999
        )
        raw_topics = p.get("topics")
        if not isinstance(raw_topics, list):
            raise AIServiceError(f"phases[{pi}].topics 必须是数组")
        if not (MIN_PHASE_TOPICS <= len(raw_topics) <= MAX_PHASE_TOPICS):
            raise AIServiceError(
                f"phases[{pi}].topics 数量必须在 "
                f"{MIN_PHASE_TOPICS}~{MAX_PHASE_TOPICS} 之间"
            )
        topics: list[AITopicDraft] = []
        for ti, t in enumerate(raw_topics):
            if not isinstance(t, dict):
                raise AIServiceError(f"phases[{pi}].topics[{ti}] 必须是对象")
            tname = _require_str(
                t.get("name"), f"phases[{pi}].topics[{ti}].name", max_len=120
            )
            tkey = normalize_draft_name(tname)
            if tkey in topic_names:
                raise AIServiceError(f"知识点名称重复：{tname}")
            topic_names.add(tkey)
            desc = t.get("description", "")
            if not isinstance(desc, str) or not desc.strip():
                raise AIServiceError(
                    f"phases[{pi}].topics[{ti}].description 不能为空"
                )
            minutes = _require_int(
                t.get("estimated_minutes"),
                f"phases[{pi}].topics[{ti}].estimated_minutes",
                MIN_TOPIC_MINUTES, MAX_TOPIC_MINUTES,
            )
            priority = _require_int(
                t.get("priority", 3),
                f"phases[{pi}].topics[{ti}].priority",
                MIN_TOPIC_PRIORITY, MAX_TOPIC_PRIORITY,
            )
            order_no = _require_int(
                t.get("order", ti + 1),
                f"phases[{pi}].topics[{ti}].order", 1, 999,
            )
            topics.append(AITopicDraft(
                name=tname, description=desc.strip(),
                estimated_minutes=minutes, priority=priority, order=order_no,
            ))
        total_topics += len(topics)
        if total_topics > MAX_ROUTE_TOPICS:
            raise AIServiceError(f"总知识点数量不能超过 {MAX_ROUTE_TOPICS}")
        phases.append(AIPhaseDraft(
            name=pname, goal=goal.strip(), order=order, topics=tuple(topics),
        ))
    return AIRouteDraft(
        route_name=route_name, plan_name=plan_name,
        summary=summary.strip(), phases=tuple(phases),
    )


def parse_route_draft_from_json(text: str) -> AIRouteDraft:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"路线草稿返回的不是合法 JSON：{e}") from e
    return parse_route_draft(data)


@dataclass(frozen=True)
class AIRouteSuggestion:
    suggested_route_names: tuple[str, ...]
    reason: str = ""


def parse_route_suggestion(raw: object) -> AIRouteSuggestion:
    if not isinstance(raw, dict):
        raise AIServiceError(
            f"路线建议必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    names = raw.get("suggested_route_names", [])
    if not isinstance(names, list):
        raise AIServiceError("suggested_route_names 必须是数组")
    out = []
    for i, n in enumerate(names):
        if not isinstance(n, str) or not n.strip():
            continue
        out.append(n.strip())
    reason = raw.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    return AIRouteSuggestion(
        suggested_route_names=tuple(dict.fromkeys(out)), reason=reason.strip()
    )


def parse_route_suggestion_from_json(text: str) -> AIRouteSuggestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIServiceError(f"路线建议返回的不是合法 JSON：{e}") from e
    return parse_route_suggestion(data)
