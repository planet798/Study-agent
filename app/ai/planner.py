"""AI 动态规划：把上下文交给 LLM，返回校验后的 DailyPlan。

复用现有 AIClient.openai-compatible.chat，不重复实现 HTTP。
只负责：构造 prompt -> 调用 -> 解析校验 -> 返回 DailyPlan。
不直接写数据库，不直接创建任务。
"""

from __future__ import annotations

from .interface import AIClient, AIServiceError
from .long_term_context import LongTermContext
from .planner_context import PlanningContext
from .prompt_registry import PromptRegistry
from .prompts import (
    build_planner_system_vars,
    build_planner_user_vars,
    render_prompt,
)
from .schemas import DailyPlan, parse_daily_plan_from_json


class AIPlanner:
    def __init__(
        self,
        client: AIClient,
        daily_limit: int = 180,
        long_term_context: LongTermContext | None = None,
        prompt_registry: PromptRegistry | None = None,
    ):
        self.client = client
        self.daily_limit = daily_limit
        # 长期学习上下文（职业目标/JD/技能路线/能力状态）；None 时不注入，保持旧行为
        self.long_term_context = long_term_context
        # 可选注入：生产环境传入 DB 支持的 PromptRegistry（支持用户覆盖）
        self.prompt_registry = prompt_registry

    def is_configured(self) -> bool:
        return self.client.is_configured()

    def plan_next_day(self, context: PlanningContext) -> DailyPlan:
        """根据上下文生成并校验下一天规划。任何失败抛 AIServiceError。"""
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法规划")

        system_prompt = render_prompt(
            "planner.system",
            build_planner_system_vars(self.daily_limit),
            self.prompt_registry,
        )
        user_prompt = render_prompt(
            "planner.user",
            build_planner_user_vars(context, self.long_term_context),
            self.prompt_registry,
        )
        try:
            content = self.client.chat(system_prompt, user_prompt)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 规划调用失败: {e}") from e

        try:
            return parse_daily_plan_from_json(content)
        except AIServiceError:
            raise  # 非法结构：抛统一异常，绝不完全信任模型输出
