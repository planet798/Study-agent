"""任务 AI 复核服务。

职责：
- 把「任务信息 + 未完成原因」交给 AI 判断；
- 对结果做 schema 校验，非法结构统一抛 AIServiceError；
- 通过依赖注入使用 AIClient 接口，不依赖具体 DeepSeek 实现，
  未来可无缝替换为 OpenAI / Gemini / USTC / 其他 OpenAI-compatible。
- 业务规则：本服务只做"复核建议"，不改变数据库状态流转，
  延期与否最终由 GUI/用户本地决定。
"""

from __future__ import annotations

from ..ai.interface import AIClient, AIServiceError
from ..ai.prompts import SYSTEM_PROMPT, build_user_prompt
from ..ai.schemas import TaskReview, parse_review_from_json
from ..database.repository import Task


class TaskReviewService:
    def __init__(self, client: AIClient):
        self.client = client

    def is_configured(self) -> bool:
        """AI 是否已配置（未配置时 GUI 应显示"AI 未配置"而非报错）。"""
        return self.client.is_configured()

    def review_task(
        self,
        task: Task,
        reason: str,
        today: str | None = None,
    ) -> TaskReview:
        """对任务的未完成原因做 AI 复核，返回校验后的结构化结果。"""
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法进行复核")
        if not reason or not reason.strip():
            raise AIServiceError("未完成原因不能为空")

        user_prompt = build_user_prompt(task, reason, today)
        try:
            content = self.client.chat(SYSTEM_PROMPT, user_prompt)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            # 任何未经包装的异常都不允许泄漏（防止 GUI 崩溃）
            raise AIServiceError(f"AI 调用失败: {e}") from e

        try:
            return parse_review_from_json(content)
        except AIServiceError:
            raise  # 非法结构：抛统一异常，绝不直接使用
