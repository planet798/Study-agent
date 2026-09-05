"""Prompt 管理。

系统提示 / 用户提示集中于此，便于统一维护与测试。
"""

from __future__ import annotations

from ..database.repository import Task
from ..database.schema import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM
from ..utils.date_utils import to_display
from .long_term_context import LongTermContext, make_long_term_summary

SYSTEM_PROMPT = """你是一个学习计划辅助助手。

你的职责不是批评用户，而是判断任务未完成原因是否合理，并根据任务的重要程度、预计耗时和用户提供的原因，判断是否适合延期到下一天。

判断标准：
- 突发课程、实验室任务、学校事务、合理身体原因、明显时间冲突：通常合理
- 无计划刷视频、游戏、拖延、忘记任务等：通常不合理
- 不要因为一次未完成就过度惩罚用户
- 如果任务明显过大，可以建议拆分
- 最终判断只作为学习辅助，不代表绝对正确

你必须只输出严格 JSON，不要输出任何其他文字，不要使用 Markdown 代码块。"""

_PRIORITY_TEXT = {
    PRIORITY_LOW: "低",
    PRIORITY_MEDIUM: "中",
    PRIORITY_HIGH: "高",
}

_OUTPUT_FORMAT_INSTRUCTION = """
请严格按照以下 JSON 结构输出（作为 assistant 消息的纯文本，不要包裹在代码块里）：
{
  "reasonable": true,
  "score": 0.85,
  "should_postpone": true,
  "suggested_date": "2026-09-05",
  "analysis": "简短中文分析",
  "suggestion": "简短中文建议"
}

字段说明：
- reasonable: boolean，原因是否合理
- score: 0 到 1 之间的数字，合理性得分
- should_postpone: boolean，是否建议延期到下一天
- suggested_date: 如果建议延期，给出具体的下一天日期（YYYY-MM-DD），否则为 null
- analysis: 简短中文分析（不超过 150 字）
- suggestion: 简短中文建议（不超过 150 字）
"""


def build_user_prompt(
    task: Task,
    reason: str,
    today: str | None = None,
) -> str:
    """根据任务 + 用户原因构造用户提示。"""
    lines = [
        "请判断以下学习任务未完成原因是否合理。",
        "",
        "【任务信息】",
        f"- 标题：{task.title}",
        f"- 描述：{task.description or '（无）'}",
        f"- 分类：{task.category or '未分类'}",
        f"- 预计时间：{task.estimated_minutes} 分钟"
        if task.estimated_minutes > 0
        else "- 预计时间：未设置",
        f"- 优先级：{_PRIORITY_TEXT.get(task.priority, '未知')}",
        f"- 计划日期：{to_display(task.scheduled_date)}",
        f"- 已延期次数：{task.postpone_count}",
        "",
        "【用户填写的未完成原因】",
        f"{reason}",
        "",
        "请给出判断结果。",
    ]
    if today:
        lines.append("")
        lines.append(f"今天是 {today}，你建议的日期应不早于今天。")
    return "\n".join(lines) + _OUTPUT_FORMAT_INSTRUCTION


# ============================================================
# AI 动态规划（Daily Planner）Prompt
# ============================================================

PLANNER_SYSTEM_PROMPT_TEMPLATE = """你是个人学习规划助手。

你的目标不是让用户每天学习越多越好，而是制定"能够持续完成"的学习计划。

遵守以下规划原则：
1. 延期任务优先处理，但不要无限堆积。
2. 如果用户连续多天完成率低，应降低第二天任务量。
3. 如果完成率稳定较高，可以逐步增加任务难度。
4. 同一任务连续延期 3 次以上，应建议：拆分任务、降低预计时长、调整任务顺序。
5. 不要因为一天完成率低就大幅调整整个学习路线。
6. 不允许修改 StudyPhase 日期。
7. 不允许跳过当前阶段核心知识。
8. 每天自主学习总时间默认不超过 {daily_limit} 分钟。
9. 给出的任务必须来自当前 StudyTopic 或合法延期任务。
10. AI 的建议必须可解释。

你必须只输出严格 JSON，不要输出任何其他文字，不要使用 Markdown 代码块。"""


def build_planner_system_prompt(daily_limit: int = 180) -> str:
    """构造规划系统提示（填入每日时间上限）。"""
    return PLANNER_SYSTEM_PROMPT_TEMPLATE.format(daily_limit=daily_limit)


def build_planner_user_prompt(context: "object", long_term: "object | None" = None) -> str:
    """根据 PlanningContext 构造用户提示；可选附带长期学习上下文。

    :param long_term: LongTermContext 或已渲染好的摘要字符串；None 表示不带。
    """
    import json

    ctx_data = context.to_dict()
    lines = [
        "请根据以下上下文，为下一天（通常是明天）规划学习任务。",
        "",
        "上下文 JSON：",
        json.dumps(ctx_data, ensure_ascii=False, indent=2),
    ]
    long_term_section = build_long_term_context_section(long_term)
    if long_term_section:
        lines.extend(["", long_term_section])
    lines.extend(
        ["", PLANNER_OUTPUT_INSTRUCTION.format(daily_limit=context.current_daily_limit)]
    )
    return "\n".join(lines)


def build_long_term_context_section(long_term: "object | None") -> str:
    """把长期学习上下文转成一个 Prompt 段落；无上下文返回空串。"""
    if long_term is None:
        return ""
    if isinstance(long_term, str):
        return "\n\n" + long_term
    if isinstance(long_term, LongTermContext):
        return "\n\n" + make_long_term_summary(long_term)
    return ""


PLANNER_OUTPUT_INSTRUCTION = """
请严格按照以下 JSON 结构输出（作为 assistant 消息的纯文本，不要包裹 Markdown 代码块）：

{{
  "reasoning": "简短分析（为什么这样安排）",
  "recommended_tasks": [
    {{
      "topic_id": 1,
      "title": "Python 函数练习",
      "description": "...",
      "estimated_minutes": 45,
      "priority": 2
    }}
  ],
  "carry_over_tasks": [
    {{
      "task_id": 10,
      "reason": "为什么建议继续处理"
    }}
  ],
  "daily_minutes": 135,
  "adjustment": "相对前几天的调整说明"
}}

约束：
- recommended_tasks 数量 1~5
- estimated_minutes 必须 > 0
- daily_minutes <= {daily_limit}
- topic_id 必须属于当前 Phase 的 available_topics
- task_id 必须属于上下文中的历史未完成任务（unfinished/postponed）
- 不要推荐已经完成的主题
"""


# ============================================================
# AI 学习总结（周/月）Prompt
# ============================================================

SUMMARY_SYSTEM_PROMPT = """你是学习数据解读助手。

你的职责是解释用户的学习统计，找出问题、总结趋势、给出建议。
你不需要、也不应该重新计算任何统计数字——所有数值都以输入数据为准。
不要批评用户，保持客观、建设性、简洁。"""

WEEKLY_SUMMARY_INSTRUCTION = """
请根据以下本周学习统计（JSON）输出严格 JSON 总结：
{{
  "overview": "一句话概述本周学习情况",
  "strengths": ["做得好的地方1", "做得好的地方2"],
  "problems": ["本周主要问题1", "本周主要问题2"],
  "recommendations": ["具体建议1", "具体建议2"],
  "next_week_focus": ["下周重点关注1", "下周重点关注2"]
}}

要求：
- overview 不超过 100 字
- 每个数组 1~3 项，每项不超过 80 字
- 所有数字以输入统计为准，不要自己推算
- 只输出 JSON，不要输出其他文字
"""

MONTHLY_SUMMARY_INSTRUCTION = """
请根据以下本月学习统计（JSON）输出严格 JSON 总结：
{{
  "overview": "一句话概述本月学习总体情况",
  "progress": "对比月初到月末的进展描述",
  "strengths": ["优势1", "优势2"],
  "weaknesses": ["不足1", "不足2"],
  "recommendations": ["建议1", "建议2"],
  "next_month_focus": ["下月重点1", "下月重点2"]
}}

要求：
- overview 与 progress 各不超过 100 字
- 每个数组 1~3 项，每项不超过 80 字
- 所有数字以输入统计为准，不要自己推算
- 只输出 JSON，不要输出其他文字
"""


def build_weekly_summary_prompt(stats: dict) -> str:
    import json

    return (
        "请解读以下本周学习统计：\n\n"
        + json.dumps(stats, ensure_ascii=False, indent=2)
        + "\n\n"
        + WEEKLY_SUMMARY_INSTRUCTION
    )


def build_monthly_summary_prompt(stats: dict) -> str:
    import json

    return (
        "请解读以下本月学习统计：\n\n"
        + json.dumps(stats, ensure_ascii=False, indent=2)
        + "\n\n"
        + MONTHLY_SUMMARY_INSTRUCTION
    )
