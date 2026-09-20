"""Assessment → Capability 确定性提取器（Phase 3）。

输入：一个已经完成 Judge 的 assessment_attempt。
输出：None 或 EXPLAIN(2) / IMPLEMENT(3)。

绝不再次调用 LLM；完全基于已持久化的 Judge evidence：
- questions_json: [{question, type, expected_points}]
- ai_result_json: {questions:[{question_index, verdict, reason}], ...}

映射：
- concept / code_reading / scenario 通过 → 最多 EXPLAIN
- debug：当前 schema 无法证明“用户写出了代码”（只能证明判题认为正确），
  保守只给 EXPLAIN；如果未来题目明确要求写代码，再单独建模
- coding 通过 → IMPLEMENT
- 只有 verdict == "correct" 才算“明确通过”；partial / incorrect 不算
"""

from __future__ import annotations

import json
from typing import Optional

from .capability import EXPLAIN, IMPLEMENT

PASS_VERDICT = "correct"

# 可支撑 EXPLAIN 的题型
EXPLAIN_TYPES = ("concept", "code_reading", "scenario", "debug")
# 可支撑 IMPLEMENT 的题型
IMPLEMENT_TYPES = ("coding",)


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def extract_assessment_capability(attempt: dict) -> Optional[dict]:
    """返回 {"level", "details"} 或 None（数据不足以下结论）。"""
    if not attempt:
        return None
    if attempt.get("judge_status") != "judged":
        return None

    questions = _loads(attempt.get("questions_json"), [])
    result = _loads(attempt.get("ai_result_json"), {})
    if not isinstance(questions, list) or not questions:
        return None
    if not isinstance(result, dict):
        return None
    judgments = result.get("questions")
    if not isinstance(judgments, list) or not judgments:
        return None

    type_by_index: dict[int, str] = {}
    for i, q in enumerate(questions):
        if isinstance(q, dict) and isinstance(q.get("type"), str):
            type_by_index[i] = q["type"]

    passed_types: list[str] = []
    per_question: list[dict] = []
    for j in judgments:
        if not isinstance(j, dict):
            continue
        idx = j.get("question_index")
        if isinstance(idx, bool) or not isinstance(idx, (int, float)):
            continue
        idx = int(idx)
        qtype = type_by_index.get(idx)
        verdict = j.get("verdict")
        per_question.append({"index": idx, "type": qtype, "verdict": verdict})
        if verdict == PASS_VERDICT and qtype:
            passed_types.append(qtype)

    if not passed_types:
        return None

    if any(t in IMPLEMENT_TYPES for t in passed_types):
        level = IMPLEMENT
    elif any(t in EXPLAIN_TYPES for t in passed_types):
        level = EXPLAIN
    else:
        return None

    details = {
        "supported_types": sorted(set(passed_types)),
        "result_level": result.get("result_level"),
        "mastery_estimate": result.get("mastery_estimate"),
        "questions": per_question,
    }
    return {"level": level, "details": details}
