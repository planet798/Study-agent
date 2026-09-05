"""AI 学习总结生成器（周 / 月）。

复用 AIClient.chat，不重复实现 HTTP。
统计数字由本地 StatsService 计算后传入，AI 只负责解释数据。
任何失败抛 AIServiceError，上层 fallback 到纯本地统计。
"""

from __future__ import annotations

from .interface import AIClient, AIServiceError
from .prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_monthly_summary_prompt,
    build_weekly_summary_prompt,
)
from .schemas import (
    MonthlySummary,
    WeeklySummary,
    parse_monthly_from_json,
    parse_weekly_from_json,
)


class AISummaryGenerator:
    def __init__(self, client: AIClient):
        self.client = client

    def is_configured(self) -> bool:
        return self.client.is_configured()

    def generate_weekly(self, stats: dict) -> WeeklySummary:
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置")
        try:
            content = self.client.chat(
                SUMMARY_SYSTEM_PROMPT, build_weekly_summary_prompt(stats)
            )
            return parse_weekly_from_json(content)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 周总结失败: {e}") from e

    def generate_monthly(self, stats: dict) -> MonthlySummary:
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置")
        try:
            content = self.client.chat(
                SUMMARY_SYSTEM_PROMPT, build_monthly_summary_prompt(stats)
            )
            return parse_monthly_from_json(content)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 月总结失败: {e}") from e
