"""Prompt 管理（registry 驱动）。

- 系统默认模板集中在 :mod:`app.ai.prompt_defaults`（single source of truth）；
- 本模块负责“运行时上下文（Runtime Context）”的动态构造，并统一通过
  :class:`app.ai.prompt_registry.PromptRegistry` 渲染最终 Prompt；
- 保留旧的 ``build_*`` 函数签名以兼容既有调用/测试（未注入 registry 时用默认）。

模板语法：``{{variable}}``（安全替换，绝不 eval / exec / format）。
"""

from __future__ import annotations

import json

from ..database.repository import Task
from ..database.schema import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM
from ..utils.date_utils import to_display
from .long_term_context import LongTermContext, make_long_term_summary
from .prompt_defaults import (
    ASSESSMENT_JUDGE_OUTPUT_INSTRUCTION,
    ASSESSMENT_JUDGE_SYSTEM,
    ASSESSMENT_OUTPUT_INSTRUCTION,
    ASSESSMENT_SYSTEM,
    ASSESSMENT_USER,
    JD_PARSE_SYSTEM,
    JD_PARSE_USER,
    PLANNER_OUTPUT_INSTRUCTION,
    PLANNER_SYSTEM,
    PLANNER_USER,
    RESUME_MATERIAL_SYSTEM,
    RESUME_MATERIAL_USER,
    ROUTE_BUILDER_SYSTEM,
    ROUTE_BUILDER_USER,
    ROUTE_SUGGEST_SYSTEM,
    ROUTE_SUGGEST_USER,
    SUMMARY_MONTHLY_USER,
    SUMMARY_SYSTEM,
    TASK_REVIEW_OUTPUT_FORMAT,
    TASK_REVIEW_SYSTEM,
    TASK_REVIEW_USER,
)
from .prompt_registry import PromptRegistry, default_prompt_registry, render_template
from .schemas import (
    ASSESSMENT_QUESTION_TYPES,
    ASSESSMENT_RESULT_LEVELS,
    ASSESSMENT_VERDICTS,
    MAX_ASSESSMENT_QUESTIONS,
    MAX_ASSESSMENT_POINTS,
)

# ---------- 兼容旧常量名（统一来自 prompt_defaults） ----------
SYSTEM_PROMPT = TASK_REVIEW_SYSTEM
PLANNER_SYSTEM_PROMPT_TEMPLATE = PLANNER_SYSTEM
ASSESSMENT_SYSTEM_PROMPT = ASSESSMENT_SYSTEM
ASSESSMENT_JUDGE_SYSTEM_PROMPT = ASSESSMENT_JUDGE_SYSTEM
SUMMARY_SYSTEM_PROMPT = SUMMARY_SYSTEM
MONTHLY_SUMMARY_INSTRUCTION = SUMMARY_MONTHLY_USER
ROUTE_BUILDER_SYSTEM_PROMPT = ROUTE_BUILDER_SYSTEM
ROUTE_SUGGEST_SYSTEM_PROMPT = ROUTE_SUGGEST_SYSTEM
JD_AI_SYSTEM = JD_PARSE_SYSTEM
_RESUME_SYSTEM_PROMPT = RESUME_MATERIAL_SYSTEM

_PRIORITY_TEXT = {
    PRIORITY_LOW: "低",
    PRIORITY_MEDIUM: "中",
    PRIORITY_HIGH: "高",
}

# JD 解析输出格式（运行时变量 output_format 的值）
JD_AI_FORMAT = (
    '{"direction": "...", "must": ["..."], "plus": ["..."], "intern": true/false}\n'
    "字段说明：direction 为岗位方向；must 为必备技能；plus 为加分技能；"
    "intern 是否实习岗位。must/plus 使用技能名称。"
)

# 简历素材输出格式（运行时变量 output_format 的值）
RESUME_OUTPUT_FORMAT = (
    '{"keywords": ["..."], "bullets": ["..."], "summary": "..."}'
)


def _registry(registry: PromptRegistry | None) -> PromptRegistry:
    return registry if registry is not None else default_prompt_registry()


def render_prompt(key: str, context: dict, registry: PromptRegistry | None = None) -> str:
    """统一入口：按 key 用 effective template（默认/override）渲染。"""
    return _registry(registry).render(key, context)


# ============================================================
# 任务复核（Task Review）
# ============================================================


def build_task_review_vars(
    task: Task,
    reason: str,
    today: str | None = None,
) -> dict:
    """构造任务复核的运行时上下文变量。"""
    estimated = (
        f"{task.estimated_minutes} 分钟"
        if task.estimated_minutes > 0
        else "未设置"
    )
    return {
        "task_title": task.title,
        "task_description": task.description or "（无）",
        "task_category": task.category or "未分类",
        "task_estimated_minutes": estimated,
        "task_priority": _PRIORITY_TEXT.get(task.priority, "未知"),
        "task_scheduled_date": to_display(task.scheduled_date),
        "task_postpone_count": task.postpone_count,
        "reason": reason,
        "today_line": (
            f"今天是 {today}，你建议的日期应不早于今天。" if today else ""
        ),
        "output_instruction": TASK_REVIEW_OUTPUT_FORMAT,
    }


def build_user_prompt(
    task: Task,
    reason: str,
    today: str | None = None,
    registry: PromptRegistry | None = None,
) -> str:
    """根据任务 + 用户原因构造用户提示（向后兼容入口）。"""
    return render_prompt(
        "task_review.user", build_task_review_vars(task, reason, today), registry
    )


# ============================================================
# AI 动态规划（Daily Planner）
# ============================================================


def build_planner_system_vars(daily_limit: int = 180) -> dict:
    return {"daily_limit": int(daily_limit)}


def build_planner_system_prompt(
    daily_limit: int = 180, registry: PromptRegistry | None = None
) -> str:
    """构造规划系统提示（填入每日时间上限）。"""
    return render_prompt(
        "planner.system", build_planner_system_vars(daily_limit), registry
    )


def build_planner_user_vars(
    context: "object", long_term: "object | None" = None
) -> dict:
    """构造 Planner 用户提示的运行时上下文变量（动态、不硬编码路线）。"""
    ctx_data = context.to_dict()
    route_name = getattr(context, "route_name", "") or ""
    route_goal = getattr(context, "route_goal", "") or ""
    if route_name:
        route_section = (
            "\n\n"
            f"【学习路线】{route_name}\n"
            f"【路线目标】{route_goal or '（未填写）'}\n"
            "只允许从本路线的 available_topics 中选择，不得添加其它路线的主题。"
        )
    else:
        route_section = ""

    evidence = build_knowledge_evidence_section(context)
    skill = build_skill_priority_section(context)
    market = build_market_trend_section(context)
    activity = build_learning_activity_section(context)
    feedback = build_planner_feedback_section(context)
    long_term_section = build_long_term_context_section(long_term)

    daily_limit = getattr(context, "current_daily_limit", 180)
    output_instruction = render_template(
        PLANNER_OUTPUT_INSTRUCTION, {"daily_limit": daily_limit}
    )
    return {
        "route_section": route_section,
        "context_json": json.dumps(ctx_data, ensure_ascii=False, indent=2),
        "knowledge_evidence_section": ("\n\n" + evidence) if evidence else "",
        "skill_priority_section": ("\n\n" + skill) if skill else "",
        "market_trend_section": ("\n\n" + market) if market else "",
        "learning_activity_section": ("\n\n" + activity) if activity else "",
        "planner_feedback_section": ("\n\n" + feedback) if feedback else "",
        "long_term_section": long_term_section,
        "output_instruction": "\n\n" + output_instruction,
        "daily_limit": daily_limit,
    }


def build_planner_user_prompt(
    context: "object",
    long_term: "object | None" = None,
    registry: PromptRegistry | None = None,
) -> str:
    """根据 PlanningContext 构造用户提示（向后兼容入口）。"""
    return render_prompt(
        "planner.user", build_planner_user_vars(context, long_term), registry
    )


def build_knowledge_evidence_section(context: "object") -> str:
    """把 PlanningContext.knowledge_evidence 渲染为 Prompt 段；无证据返回空串。

    只有确实有验收证据的知识点会出现在这里；绝不伪造 mastery。
    """
    evidence = getattr(context, "knowledge_evidence", None)
    if not evidence:
        return ""
    lines = [
        "【知识掌握证据】（当前实际掌握情况的 AI 估计；动态证据，不是职业路线/阶段控制器）"
    ]
    for e in evidence:
        parts = [f"知识点:{e.name}"]
        if e.topic:
            parts.append(f"主题:{e.topic}")
        if e.mastery_estimate is not None:
            parts.append(f"mastery:{e.mastery_estimate:.2f}")
        else:
            parts.append("mastery:—")
        parts.append(f"最近验收:{e.recent_result_level or '—'}")
        if e.weak_points:
            parts.append(f"薄弱点:{'、'.join(e.weak_points)}")
        lines.append("- " + "；".join(parts))
    lines.append(
        "用途：只用于调整‘下一步学什么’的优先级与针对性；不要据此跳过整个阶段，"
        "不要重复已掌握内容。"
    )
    return "\n".join(lines)


def build_learning_activity_section(context: "object") -> str:
    """把当前阶段 topic 的“下一步学习活动”渲染为 Prompt 段；无则返回空串。

    活动由 TopicLearningProfileService 确定性计算，AI 不得自由更改。
    """
    topics = getattr(context, "available_topics", None) or []
    rows = [
        (t.title, getattr(t, "next_activity_label", ""))
        for t in topics if getattr(t, "next_activity", "")
    ]
    if not rows:
        return ""
    lines = [
        "【学习活动】（由课程结构确定性决定，不得自由更改 activity 类型）"
    ]
    for title, label in rows[:20]:
        lines.append(f"- {title}：下一步活动 = {label}")
    lines.append(
        "使用要求：每个 topic 只能规划其“下一步活动”对应的任务；"
        "不得跳过未完成的必需活动，也不得自由更换 activity 类型。"
    )
    return "\n".join(lines)


def build_planner_feedback_section(context: "object") -> str:
    """Phase 6：把已排序的 Planner Feedback 渲染为 Prompt 段；无则返回空串。

    只传确定性信号：tier / project requirement / capability gap / next activity。
    不传 Output 内容、本地路径、repo URI、details_json（隐私/噪声）。
    """
    feedback = getattr(context, "planner_feedback", None) or []
    pool = set(getattr(context, "candidate_topic_ids", None) or [])
    if not feedback:
        return ""
    lines = [
        "【本轮优先级候选】（由系统确定性排序，你只能从 candidate 中选择）"
    ]
    for f in feedback[:20]:
        mark = "candidate" if f.topic_id in pool else "-"
        parts = [f"topic_id={f.topic_id}", f"tier={f.tier_label}", mark]
        if f.active_project_blocker_count:
            parts.append(
                f"项目阻塞×{f.active_project_blocker_count}"
                f"（最大能力缺口 {f.max_capability_gap}）"
            )
        if f.next_activity_label:
            parts.append(f"下一步活动={f.next_activity_label}")
        lines.append("- " + "；".join(parts))
        for req in f.project_requirements[:3]:
            lines.append(
                f"    · 项目：{req.get('project_name', '')}；"
                f"{req.get('current_capability', '')} → "
                f"{req.get('target_capability', '')}"
            )
    if pool:
        lines.append(
            "使用要求：本轮只能从标记为 candidate 的 topic_id 中选择（最高优先级），"
            "不得选择其它 tier 的 Topic，也不得创造新 Topic。"
        )
    else:
        lines.append(
            "使用要求：只能在 available_topics 中选择，不得创造新 Topic。"
        )
    return "\n".join(lines)


def build_market_trend_section(context: "object") -> str:
    """把 Step 6 的近期目标岗位技术趋势渲染为 Prompt 段；无则返回空串。

    明确说明：这是用户人工收集的目标岗位样本，不代表全行业需求。
    """
    trends = getattr(context, "market_trends", None)
    source = getattr(context, "market_source", "none")
    n30 = int(getattr(context, "market_sample_count_30d", 0) or 0)
    if not trends or source != "daily_summary":
        return ""
    lines = [
        "【近30天目标岗位技术趋势】（来自用户人工汇总的每日 JD 样本；"
        "仅代表其近期收集的目标岗位，不代表全行业需求）",
        f"近 30 天样本：{n30} 个目标实习岗位。",
    ]
    for t in list(trends)[:10]:
        lines.append(
            f"  - {t.skill}：{t.market_30d * 100:.0f}%"
            f"（{t.mention_30d} 次）"
        )
    lines.append(
        "使用要求：① 作为市场需求证据，与 mastery / weak_points 联合判断；"
        "② 不得越过 prerequisite；③ 不得跳阶段；④ 不重复已掌握基础内容；"
        "⑤ 高频但 blocked 的技能，应优先推动其必要前置技能；"
        "⑥ 仍只能从 available_topics 中选择。"
    )
    return "\n".join(lines)


def build_skill_priority_section(context: "object") -> str:
    """把 Phase C 的 技能优先级 / JD 缺口 / 前置阻塞 / 每周预览 渲染为 Prompt 段。

    skill_priorities / jd_gap 等都为空时不输出（保持旧行为）。
    """
    sp = getattr(context, "skill_priorities", None)
    gap = getattr(context, "jd_gap_skills", None)
    blocked = getattr(context, "prerequisite_blocked", None)
    weekly = getattr(context, "weekly_focus", None)
    if not any([sp, gap, blocked, weekly]):
        return ""
    lines = [
        "【技能优先级 / JD 缺口】（SkillService 依据 技能池 + 近期市场需求/历史单条 JD"
        " + 掌握证据 + 前置门禁 计算；只决定当前阶段内“下一步”的相对优先级）"
    ]
    if sp:
        lines.append("- 近期技能优先级（near = 近期目标岗位样本需求）：")
        for i, s in enumerate(list(sp)[:8], 1):
            mkt = ""
            if getattr(s, "market_30d", None) is not None:
                mkt = f"，近30天目标岗位 {s.market_30d * 100:.0f}%"
            stage = getattr(s, "stage_alignment", "unknown")
            stage_txt = {"current": "，当前阶段相关",
                         "next": "，下一阶段",
                         "far": "，较后阶段"}.get(stage, "")
            lines.append(
                f"  {i}. {s.skill}（{s.tier}级，score={s.score:.3f}{mkt}{stage_txt}）"
                f"：{s.reason}"
            )
    if gap:
        lines.append("- JD 缺口（近期目标岗位需求高但未掌握）：")
        for g in list(gap)[:10]:
            mastery_txt = (
                f"{g.mastery:.2f}" if g.mastery is not None else "无验收"
            )
            flag = "（前置阻塞，不得直接安排，先补前置）" if g.blocked else ""
            mkt = ""
            if getattr(g, "market_30d", None) is not None:
                mkt = f"近30天 {g.market_30d * 100:.0f}% / "
            lines.append(
                f"  - {g.skill}：{mkt}must×{g.jd_must_count} / plus×{g.jd_plus_count}，"
                f"mastery={mastery_txt}{flag}"
            )
    if blocked:
        lines.append("- 前置未满足（不得越级安排）：")
        for b in list(blocked)[:10]:
            lines.append(f"  - {b.skill}（缺：{'、'.join(b.missing) or '未知'}）")
    if weekly:
        lines.append("- 未来 1~2 周学习形状（纯规则预览，不写任务）：")
        for w in list(weekly)[:7]:
            line = "、".join(w.skills) if w.skills else "—"
            lines.append(f"  - {w.date}: {line}")
    lines.append(
        "职责与边界：career_context=长期职业方向；study_plan=当前阶段；"
        "skill_priorities / JD 缺口=近期优先级；knowledge_evidence=实际掌握情况。"
        "Planner 只能决定“当前阶段下一步优先学什么”，并从 available_topics 中选取："
        "不得跳阶段、不得越级安排前置未满足的技能、不要重复已掌握内容。"
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


# ============================================================
# AI 验收题生成（Assessment）
# ============================================================


def build_assessment_generate_vars(
    knowledge_point_name: str,
    description: str = "",
    num_questions: int = 4,
    activity_kind: str | None = None,
) -> dict:
    types = " / ".join(ASSESSMENT_QUESTION_TYPES)
    output_instruction = render_template(
        ASSESSMENT_OUTPUT_INSTRUCTION,
        {
            "max_questions": MAX_ASSESSMENT_QUESTIONS,
            "types": ", ".join(ASSESSMENT_QUESTION_TYPES),
            "max_points": MAX_ASSESSMENT_POINTS,
        },
    )
    activity_context = ""
    if activity_kind:
        from ..services.learning_activity import ACTIVITY_LABELS

        label = ACTIVITY_LABELS.get(activity_kind, activity_kind)
        hints = {
            "theory": "可侧重概念解释与机制理解",
            "code_reading": "可侧重代码理解与实现细节",
            "experiment": "可侧重实验现象、参数影响与原因分析",
            "interview": "可侧重面试型解释与推导",
            "practice": "可侧重综合实践的设计与取舍",
        }
        activity_context = (
            f"- 本次学习方式：{label}（{hints.get(activity_kind, '')}）"
        )
    return {
        "knowledge_point_name": knowledge_point_name,
        "knowledge_point_description": (
            f"- 描述：{description}" if description else ""
        ),
        "num_questions": int(num_questions),
        "types": types,
        "max_questions": MAX_ASSESSMENT_QUESTIONS,
        "max_points": MAX_ASSESSMENT_POINTS,
        "activity_context": activity_context,
        "output_instruction": "\n\n" + output_instruction,
    }


def build_assessment_prompt(
    knowledge_point_name: str,
    description: str = "",
    num_questions: int = 4,
    registry: PromptRegistry | None = None,
    activity_kind: str | None = None,
) -> str:
    """根据知识点构造验收题 Prompt（向后兼容入口）。"""
    return render_prompt(
        "assessment.generate.user",
        build_assessment_generate_vars(
            knowledge_point_name, description, num_questions, activity_kind
        ),
        registry,
    )


def build_assessment_judge_vars(questions: list[dict], answers: list[str]) -> dict:
    output_instruction = render_template(
        ASSESSMENT_JUDGE_OUTPUT_INSTRUCTION,
        {
            "verdicts": ", ".join(ASSESSMENT_VERDICTS),
            "levels": ", ".join(ASSESSMENT_RESULT_LEVELS),
        },
    )
    return {
        "questions_json": json.dumps(questions, ensure_ascii=False, indent=2),
        "answers_json": json.dumps(answers, ensure_ascii=False, indent=2),
        "verdicts": ", ".join(ASSESSMENT_VERDICTS),
        "levels": ", ".join(ASSESSMENT_RESULT_LEVELS),
        "output_instruction": output_instruction,
    }


def build_assessment_judge_prompt(
    questions: list[dict],
    answers: list[str],
    registry: PromptRegistry | None = None,
) -> str:
    """根据题目 + 用户答案构造判题 Prompt（向后兼容入口）。"""
    return render_prompt(
        "assessment.judge.user",
        build_assessment_judge_vars(questions, answers),
        registry,
    )


# ============================================================
# AI 学习总结（月）
# ============================================================


def build_monthly_summary_vars(stats: dict) -> dict:
    return {"stats_json": json.dumps(stats, ensure_ascii=False, indent=2)}


def build_monthly_summary_prompt(
    stats: dict, registry: PromptRegistry | None = None
) -> str:
    return render_prompt(
        "summary.monthly.user", build_monthly_summary_vars(stats), registry
    )


# ============================================================
# AI 学习路线草稿（Route Builder）
# ============================================================


def build_route_builder_vars(
    context: dict, route_skills: list | None = None, market: dict | None = None
) -> dict:
    route_skills_section = ""
    if route_skills:
        route_skills_section = (
            "\n\n【该路线关注的技能（仅作重点参考，不要求每个都生成 Topic）】\n"
            + json.dumps(route_skills, ensure_ascii=False, indent=2)
        )
    market_section = ""
    if market:
        market_section = (
            "\n\n【近期目标岗位样本信号（仅用于调整重点，不代表全行业，"
            "不允许跳过基础依赖）】\n"
            + json.dumps(market, ensure_ascii=False, indent=2)
        )
    return {
        "route_context_json": json.dumps(context, ensure_ascii=False, indent=2),
        "route_skills_section": route_skills_section,
        "market_section": market_section,
    }


def build_route_builder_prompt(
    context: dict,
    route_skills: list | None = None,
    market: dict | None = None,
    registry: PromptRegistry | None = None,
) -> str:
    return render_prompt(
        "route_builder.user",
        build_route_builder_vars(context, route_skills, market),
        registry,
    )


def build_route_suggest_vars(candidate_name: str, routes: list) -> dict:
    return {
        "candidate_name": candidate_name,
        "routes_json": json.dumps(routes, ensure_ascii=False, indent=2),
    }


def build_route_suggest_prompt(
    candidate_name: str, routes: list, registry: PromptRegistry | None = None
) -> str:
    return render_prompt(
        "route_suggestion.user",
        build_route_suggest_vars(candidate_name, routes),
        registry,
    )


# ============================================================
# JD 解析
# ============================================================


def build_jd_parse_vars(raw_text: str) -> dict:
    return {"jd_text": raw_text, "output_format": JD_AI_FORMAT}


def build_jd_parse_prompt(
    raw_text: str, registry: PromptRegistry | None = None
) -> str:
    return render_prompt("jd_parse.user", build_jd_parse_vars(raw_text), registry)


# ============================================================
# 简历素材
# ============================================================


def build_resume_material_vars(outcomes: list[dict]) -> dict:
    safe = []
    for o in outcomes:
        safe.append({
            "title": o.get("title"),
            "content": o.get("content"),
            "kind": o.get("kind"),
            "tech_stack": o.get("tech_stack"),
            "dataset": o.get("dataset"),
            "metrics": o.get("metrics"),
            "github_url": o.get("github_url"),
            "resume_keywords": o.get("resume_keywords"),
        })
    return {
        "outcomes_json": json.dumps(safe, ensure_ascii=False, indent=2),
        "output_format": RESUME_OUTPUT_FORMAT,
    }


def build_resume_material_prompt(
    outcomes: list[dict], registry: PromptRegistry | None = None
) -> str:
    return render_prompt(
        "resume_material.user", build_resume_material_vars(outcomes), registry
    )
