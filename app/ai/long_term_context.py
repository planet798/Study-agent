"""长期学习上下文：职业目标 / JD 分析 / 技能路线 / 当前能力状态。

单源真相：docs/career_context.json（一个文件，不散落到多个 Python 文件）。

角色分工：
- LEARNING_LOG.md   → 学习历史记录（做了什么、学会了什么）
- career_context.json → 长期依据（为什么学、目标岗位、真实 JD、技能路线、当前状态）
- 每日任务          → 动态规划结果（不硬编码，由 AI Planner 结合本上下文生成）

本模块只负责：加载 / 校验 / 生成给 Prompt 用的摘要。
任何加载失败（文件缺失 / JSON 非法 / 结构不对）都返回 None，
Planner 保持"无长期上下文"的旧行为，绝不因上下文问题崩溃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTEXT_PATH = PROJECT_ROOT / "docs" / "career_context.json"

# 必须存在的顶层字段
_REQUIRED_KEYS = (
    "career_goal",
    "target_roles",
    "jd_evidence",
    "skill_roadmap",
    "current_skill_state",
    "learning_principles",
    "project_state",
)


def _require_str(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"career_context.{key} 必须是非空字符串")
    return value.strip()


def _require_dict(value: object, key: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"career_context.{key} 必须是对象")
    return value


def _require_list(value: object, key: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"career_context.{key} 必须是数组")
    return value


@dataclass(frozen=True)
class LongTermContext:
    """通过校验的长期学习上下文。"""

    career_goal: str
    target_roles: dict
    jd_evidence: dict
    skill_roadmap: dict
    current_skill_state: dict
    learning_principles: tuple[str, ...]
    project_state: dict
    updated: str = ""
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "LongTermContext":
        """从 dict 构建并校验；结构非法抛 ValueError。"""
        for key in _REQUIRED_KEYS:
            if key not in data:
                raise ValueError(f"career_context 缺少字段：{key}")
        career_goal = _require_str(data["career_goal"], "career_goal")
        target_roles = _require_dict(data["target_roles"], "target_roles")
        jd_evidence = _require_dict(data["jd_evidence"], "jd_evidence")
        skill_roadmap = _require_dict(data["skill_roadmap"], "skill_roadmap")
        current_state = _require_dict(
            data["current_skill_state"], "current_skill_state"
        )
        principles = tuple(
            _require_str(p, "learning_principles") for p in _require_list(
                data["learning_principles"], "learning_principles"
            )
        )
        if not principles:
            raise ValueError("career_context.learning_principles 不能为空")
        project_state = _require_dict(data["project_state"], "project_state")
        updated = data.get("updated", "")
        updated = updated.strip() if isinstance(updated, str) else ""
        return cls(
            career_goal=career_goal,
            target_roles=target_roles,
            jd_evidence=jd_evidence,
            skill_roadmap=skill_roadmap,
            current_skill_state=current_state,
            learning_principles=principles,
            project_state=project_state,
            updated=updated,
            schema_version=int(data.get("schema_version", 1)),
        )

    def to_dict(self) -> dict:
        return {
            "career_goal": self.career_goal,
            "target_roles": self.target_roles,
            "jd_evidence": self.jd_evidence,
            "skill_roadmap": self.skill_roadmap,
            "current_skill_state": self.current_skill_state,
            "learning_principles": list(self.learning_principles),
            "project_state": self.project_state,
            "updated": self.updated,
            "schema_version": self.schema_version,
        }


def load_long_term_context(
    path: str | Path | None = None,
) -> LongTermContext | None:
    """加载长期学习上下文。

    :param path: JSON 路径；缺省用 docs/career_context.json。
    :return: 校验通过的 LongTermContext；文件缺失 / JSON 非法 / 结构不对 → None。
    """
    ctx_path = Path(path) if path is not None else DEFAULT_CONTEXT_PATH
    if not ctx_path.exists():
        return None
    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        return LongTermContext.from_dict(data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _fmt_list(items: Any, joiner: str = "、") -> str:
    if not isinstance(items, (list, tuple)) or not items:
        return "（无）"
    return joiner.join(str(x) for x in items)


def _fmt_topic_freq(topic: dict) -> str:
    try:
        c = topic.get("count", "?")
        t = topic.get("total", "?")
        pri = topic.get("priority", "")
        suffix = f"（{pri}）" if pri else ""
        return f"{c}/{t}{suffix}"
    except (TypeError, AttributeError):
        return "?"


def make_long_term_summary(ctx: LongTermContext) -> str:
    """把长期学习上下文渲染成紧凑的、可直接进 Prompt 的摘要。"""
    roles = ctx.target_roles
    primary = _fmt_list(roles.get("primary"))
    secondary = _fmt_list(roles.get("secondary"))

    # JD 高频技能 -> "Python 9/10（最高）；PyTorch 7/10（最高）；..."
    freq = ctx.jd_evidence.get("tech_frequency", {})
    freq_lines = "；".join(
        f"{name} {_fmt_topic_freq(info)}" for name, info in freq.items()
    )

    jd_conclusions = _fmt_list(
        ctx.jd_evidence.get("conclusions", []), joiner="；"
    )

    # 技能主路线：每个阶段一行（"阶段名：A → B → C"）
    roadmap_lines = []
    for stage in ctx.skill_roadmap.get("stages", []):
        if not isinstance(stage, dict):
            continue
        name = stage.get("name", "阶段")
        items = " → ".join(str(x) for x in stage.get("items", []))
        roadmap_lines.append(f"- {name}：{items}")
    roadmap = "\n".join(roadmap_lines)

    state = ctx.current_skill_state
    mastered = _fmt_list(state.get("mastered"))
    weak = _fmt_list(state.get("weak"))
    deferred = _fmt_list(state.get("deferred"))

    # 只提炼前几条关键原则，避免 prompt 过长
    key_principles = _fmt_list(ctx.learning_principles[:4], joiner="；")

    proj = ctx.project_state
    proj_lines = "、".join(
        str(x) for x in proj.get("implemented", []) if isinstance(x, str)
    )

    return (
        "【长期学习上下文】（依据 docs/career_context.json，学习路线以本段为唯一依据）\n"
        f"- 职业目标：{ctx.career_goal}\n"
        f"- 目标岗位（主要）：{primary}\n"
        f"- 目标岗位（备选/可发展）：{secondary}\n"
        f"- 真实 JD 高频技能（N 个 JD 中出现次数）：{freq_lines}\n"
        f"- JD 结论：{jd_conclusions}\n"
        f"- 技能主路线（按依赖推进，不跨级）：\n{roadmap}\n"
        f"- 当前已掌握：{mastered}\n"
        f"- 当前薄弱/待系统学习：{weak}\n"
        f"- 当前暂缓：{deferred}\n"
        f"- 关键学习原则：{key_principles}\n"
        f"- 项目状态：{proj.get('name', 'Study Agent')} 长期主项目"
        f"{'（repo=' + str(proj.get('repo', '')) + '）' if proj.get('repo') else ''}，"
        f"当前版本 {proj.get('version', '?')}，已实现：{proj_lines}"
    )
