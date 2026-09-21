"""Practice → Capability 规则定义（Phase 5）。

这里只放**确定性结构规则**，不做任何“外部验证”：
Study Agent 不会 clone repo / 跑 benchmark / 检查 checkpoint，
因此这里只判断“结构上是否足以构成用户确认的项目证据”。

重要文案原则：
- 我们不说“系统已验证”；我们表达“用户提供并确认真实产出”。
"""

from __future__ import annotations

from .practice import (
    is_qualifying_project_output_type,
    is_supporting_project_output_type,
    output_type_label,
)

# ---------- ineligible / rejection reason codes ----------

REASON_OK = ""
REASON_PROJECT_NOT_FOUND = "project_not_found"
REASON_PROJECT_NOT_COMPLETED = "project_not_completed"
REASON_PROJECT_ARCHIVED = "project_archived"
REASON_TOPIC_NOT_FOUND = "topic_not_found"
REASON_TOPIC_RELATION_MISSING = "topic_relation_missing"
REASON_ROUTE_MISMATCH = "route_mismatch"
REASON_KP_CONFLICT = "knowledge_point_identity_conflict"
REASON_KP_ROUTE_MISMATCH = "knowledge_point_route_mismatch"
REASON_NO_OUTPUTS = "no_outputs"
REASON_NO_QUALIFYING_OUTPUT = "no_qualifying_output"
REASON_OUTPUT_NOT_IN_PROJECT = "output_not_in_project"
REASON_USAGE_DESCRIPTION_REQUIRED = "usage_description_required"
REASON_CONFIRMATION_REQUIRED = "confirmation_required"
REASON_ALREADY_ACTIVE = "already_has_active_evidence"
REASON_EVIDENCE_NOT_FOUND = "evidence_not_found"

# ---------- 中文说明（UI 展示用，不做“已验证”措辞） ----------

REASON_LABELS: dict[str, str] = {
    REASON_PROJECT_NOT_FOUND: "项目不存在",
    REASON_PROJECT_NOT_COMPLETED: "项目尚未标记为已完成",
    REASON_PROJECT_ARCHIVED: "已归档项目不能新增能力证据",
    REASON_TOPIC_NOT_FOUND: "Topic 不存在或未绑定路线",
    REASON_TOPIC_RELATION_MISSING: "该 Topic 尚未关联到项目",
    REASON_ROUTE_MISMATCH: "Topic 所属路线不在项目关联路线中",
    REASON_KP_CONFLICT: "知识点身份冲突：同路线已存在同名知识点，请人工处理",
    REASON_KP_ROUTE_MISMATCH: "知识点所属路线与 Topic 路线不一致",
    REASON_NO_OUTPUTS: "至少需要选择一个项目成果",
    REASON_NO_QUALIFYING_OUTPUT: "至少需要一个可作为主要证据的项目成果",
    REASON_OUTPUT_NOT_IN_PROJECT: "所选成果不属于该项目",
    REASON_USAGE_DESCRIPTION_REQUIRED: "必须填写项目使用说明",
    REASON_CONFIRMATION_REQUIRED: "必须显式确认该 Topic 确实在此项目中实际使用",
    REASON_ALREADY_ACTIVE: "该 Topic 已有生效的项目使用证据",
    REASON_EVIDENCE_NOT_FOUND: "项目使用证据不存在",
}


def reason_label(reason: str | None) -> str:
    if not reason:
        return ""
    return REASON_LABELS.get(reason, reason)


# ---------- output 结构有效性 ----------


def _has_uri(output: dict) -> bool:
    return bool((output.get("uri") or "").strip())


def _has_details(output: dict) -> bool:
    details = output.get("details")
    if isinstance(details, dict) and details:
        return True
    # 仓库层已解析 details_json → details；兜底原始字段
    raw = output.get("details_json")
    if isinstance(raw, str):
        raw = raw.strip()
        return raw not in ("", "{}", "null")
    return False


def output_artifact_reason(output: dict) -> str:
    """返回 '' 表示结构有效；否则返回不可用原因（中文）。"""
    otype = output.get("output_type")
    if is_qualifying_project_output_type(otype):
        if otype in ("repository", "code", "demo"):
            if not _has_uri(output):
                return f"{output_type_label(otype)} 需要有效的 URL / 链接"
            return ""
        if otype == "checkpoint":
            if not (_has_uri(output) or _has_details(output)):
                return "Checkpoint 需要链接或结构化信息（如模型/路径/指标）"
            return ""
        # result / benchmark / paper_reproduction
        if not (_has_details(output) or _has_uri(output)):
            return f"{output_type_label(otype)} 需要结构化结果或链接"
        return ""
    if is_supporting_project_output_type(otype):
        return ""  # 辅助材料自身有效，但不能单独支撑 PROJECT
    return "未知成果类型"


def is_valid_project_artifact(output: dict) -> bool:
    return output_artifact_reason(output) == ""


def is_qualifying_output(output: dict) -> bool:
    """可作为主要证据：类型 qualifying 且结构有效。"""
    if not is_qualifying_project_output_type(output.get("output_type")):
        return False
    return is_valid_project_artifact(output)


def output_evidence_role(output: dict) -> str:
    """UI 展示：主要证据 / 辅助材料 / 结构不足。"""
    if is_qualifying_project_output_type(output.get("output_type")):
        if is_valid_project_artifact(output):
            return "主要证据"
        return "结构不足"
    if is_supporting_project_output_type(output.get("output_type")):
        return "辅助材料"
    return "未知"
