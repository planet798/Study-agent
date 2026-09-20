"""AI 学习总结生成器（月）。

复用 AIClient.chat，不重复实现 HTTP。
统计数字由本地 StatsService 计算后传入，AI 只负责解释数据。
任何失败抛 AIServiceError，上层 fallback 到纯本地统计。
"""

from __future__ import annotations

from .interface import AIClient, AIServiceError
from .prompt_registry import PromptRegistry
from .prompts import (
    build_monthly_summary_vars,
    render_prompt,
)
from .schemas import (
    MonthlySummary,
    parse_monthly_from_json,
)


class AISummaryGenerator:
    def __init__(
        self, client: AIClient, prompt_registry: PromptRegistry | None = None
    ):
        self.client = client
        self.prompt_registry = prompt_registry

    def is_configured(self) -> bool:
        return self.client.is_configured()

    def generate_monthly(self, stats: dict) -> MonthlySummary:
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置")
        try:
            content = self.client.chat(
                render_prompt("summary.monthly.system", {}, self.prompt_registry),
                render_prompt(
                    "summary.monthly.user",
                    build_monthly_summary_vars(stats),
                    self.prompt_registry,
                ),
            )
            return parse_monthly_from_json(content)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 月总结失败: {e}") from e
