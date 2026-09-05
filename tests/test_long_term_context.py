"""长期学习上下文测试。

覆盖：
- docs/career_context.json 是单源真相：可加载、含全部 7 个必需板块
- 加载/校验：缺失文件 / 非法 JSON / 缺关键字段 → 优雅返回 None
- 摘要渲染：包含职业目标 / 目标岗位 / JD 高频 / 技能路线 / 能力状态 / 项目状态
- Planner 注入：AIPlanner(long_term_context=...) 会把长期上下文写进 user prompt；
  不传时行为完全不变
- 端到端：真实 JSON 文件 → Planner user prompt 包含 JD 证据（9/10）与阶段路线
"""

from __future__ import annotations

import json

import pytest

from app.ai.long_term_context import (
    DEFAULT_CONTEXT_PATH,
    LongTermContext,
    load_long_term_context,
    make_long_term_summary,
)
from app.ai.planner import AIPlanner
from app.ai.planner_context import PlanningContext


def _sample_data() -> dict:
    return {
        "career_goal": "为硕士毕业后的大厂 AI/大模型岗位做准备",
        "target_roles": {
            "primary": ["大模型算法工程师"],
            "secondary": ["AI 系统 / 大模型推理工程师"],
        },
        "jd_evidence": {
            "companies": ["华为", "科大讯飞"],
            "tech_frequency": {
                "Python": {"count": 9, "total": 10, "priority": "最高"},
            },
            "conclusions": ["Python/PyTorch/LLM/Linux 是核心基础"],
        },
        "skill_roadmap": {
            "stages": [
                {"name": "阶段一：工程底座", "items": ["Python", "Git"]},
                {"name": "阶段二：深度学习与 LLM 基础", "items": ["PyTorch", "Transformer"]},
            ],
            "priority": {"must_master": ["Python"], "should_master": [], "understand_only": []},
        },
        "current_skill_state": {
            "mastered": ["pytest"],
            "weak": ["PyTorch"],
            "deferred": ["CUDA"],
        },
        "learning_principles": ["优先学习真实 JD 高频技能", "遵守前置依赖"],
        "project_state": {
            "name": "Study Agent",
            "repo": "planet798/Study-agent",
            "version": "v0.1.0",
        },
    }


class TestLoad:
    def test_default_file_exists_in_workspace(self):
        assert DEFAULT_CONTEXT_PATH.exists()

    def test_loads_real_context(self):
        ctx = load_long_term_context()
        assert ctx is not None
        assert "大模型" in ctx.career_goal
        assert ctx.target_roles["primary"]
        assert ctx.jd_evidence["tech_frequency"]
        assert ctx.skill_roadmap["stages"]
        assert ctx.current_skill_state["mastered"]
        assert ctx.learning_principles
        assert ctx.project_state["repo"] == "planet798/Study-agent"

    def test_missing_file_returns_none(self, tmp_path):
        assert load_long_term_context(tmp_path / "nope.json") is None

    def test_invalid_json_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        assert load_long_term_context(p) is None

    def test_missing_required_key_returns_none(self, tmp_path):
        data = _sample_data()
        del data["career_goal"]
        p = tmp_path / "bad2.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert load_long_term_context(p) is None

    def test_from_dict_and_to_dict_roundtrip(self):
        ctx = LongTermContext.from_dict(_sample_data())
        assert ctx.to_dict()["career_goal"] == _sample_data()["career_goal"]
        assert len(ctx.learning_principles) == 2


class TestSummary:
    def test_real_summary_contains_key_markers(self):
        ctx = load_long_term_context()
        assert ctx is not None
        summary = make_long_term_summary(ctx)
        for marker in (
            "职业目标", "目标岗位", "真实 JD 高频技能", "技能主路线",
            "阶段一", "阶段四", "当前已掌握", "当前薄弱", "当前暂缓", "项目状态",
        ):
            assert marker in summary, marker

    def test_summary_contains_jd_frequency_like_9_10(self):
        ctx = load_long_term_context()
        assert "9/10" in make_long_term_summary(ctx)

    def test_sample_summary(self):
        ctx = LongTermContext.from_dict(_sample_data())
        summary = make_long_term_summary(ctx)
        assert "阶段一" in summary and "阶段二" in summary


class TestPlannerInjection:
    class _FakeClient:
        def __init__(self, content):
            self._content = content
            self.calls = []

        def is_configured(self):
            return True

        def chat(self, system_prompt, user_prompt, **kwargs):
            self.calls.append((system_prompt, user_prompt))
            return self._content

    @staticmethod
    def _valid_plan_json() -> str:
        return json.dumps(
            {
                "reasoning": "根据长期路线安排",
                "recommended_tasks": [
                    {"topic_id": 1, "title": "t", "estimated_minutes": 30}
                ],
                "carry_over_tasks": [],
                "daily_minutes": 30,
                "adjustment": "a",
            },
            ensure_ascii=False,
        )

    def test_user_prompt_contains_long_term_section(self):
        client = self._FakeClient(self._valid_plan_json())
        planner = AIPlanner(
            client,
            long_term_context=load_long_term_context(),
        )
        planner.plan_next_day(PlanningContext(current_date="2026-09-05"))
        _, user_prompt = client.calls[0]
        assert "【长期学习上下文】" in user_prompt
        assert "职业目标" in user_prompt

    def test_without_long_term_unchanged(self):
        client = self._FakeClient(self._valid_plan_json())
        planner = AIPlanner(client)
        planner.plan_next_day(PlanningContext(current_date="2026-09-05"))
        _, user_prompt = client.calls[0]
        assert "【长期学习上下文】" not in user_prompt

    def test_real_file_feeds_planner_prompt_end_to_end(self):
        """真实 JSON -> Planner user prompt 包含 JD 证据与阶段路线。"""
        client = self._FakeClient(self._valid_plan_json())
        ctx = load_long_term_context()
        assert ctx is not None
        AIPlanner(client, long_term_context=ctx).plan_next_day(
            PlanningContext(current_date="2026-09-05")
        )
        _, user_prompt = client.calls[0]
        assert "9/10" in user_prompt       # JD 高频技能
        assert "阶段一" in user_prompt      # 技能路线
        assert "planet798/Study-agent" in user_prompt  # 项目状态
