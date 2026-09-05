"""AI 动态规划：把上下文交给 LLM，返回校验后的 DailyPlan。

复用现有 AIClient.openai-compatible.chat，不重复实现 HTTP。
只负责：构造 prompt -> 调用 -> 解析校验 -> 返回 DailyPlan。
不直接写数据库，不直接创建任务。
"""

from __future__ import annotations

from .interface import AIClient, AIServiceError
from .planner_context import PlanningContext
from .prompts import build_planner_system_prompt, build_planner_user_prompt
from .schemas import DailyPlan, parse_daily_plan_from_json


class AIPlanner:
    def __init__(self, client: AIClient, daily_limit: int = 180):
        self.client = client
        self.daily_limit = daily_limit

    def is_configured(self) -> bool:
        return self.client.is_configured()

    def plan_next_day(self, context: PlanningContext) -> DailyPlan:
        """根据上下文生成并校验下一天规划。任何失败抛 AIServiceError。"""
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法规划")

        system_prompt = build_planner_system_prompt(self.daily_limit)
        user_prompt = build_planner_user_prompt(context)
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
