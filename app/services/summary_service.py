"""总结服务：把本地统计 + AI 解释 + 缓存整合起来。

流程：
1. 本地计算统计（StatsService，作为事实）；
2. 检查缓存：若同一期间、stats_json 未变化且有 AI 结论 → 直接复用；
3. 无有效缓存 → 若 AI 可用则生成 AI 总结并缓存，否则仅本地统计。
"""

from __future__ import annotations

import json

from ..ai.interface import AIServiceError
from ..ai.summary import AISummaryGenerator
from ..database.study_plan_repository import SummaryCacheRepository
from ..utils.date_utils import month_range, week_end, week_start
from .stats_service import StatsService

_JSON = "json"


def _stats_fingerprint(stats: dict) -> str:
    """统计快照的规范序列化，用于判断统计数据是否变化。"""
    return json.dumps(stats, ensure_ascii=False, sort_keys=True, default=str)


class SummaryService:
    def __init__(
        self,
        stats_service: StatsService,
        cache_repo: SummaryCacheRepository,
        ai_generator: AISummaryGenerator | None = None,
    ):
        self.stats_service = stats_service
        self.cache_repo = cache_repo
        self.ai_generator = ai_generator

    # ================= 周总结 =================

    def get_weekly_summary(self, anchor_date: str) -> dict:
        start, end = week_start(anchor_date), week_end(anchor_date)
        stats = self.stats_service.get_weekly_stats(start, end)
        return self._resolve("weekly_summaries", start, end, stats, "weekly")

    # ================= 月总结 =================

    def get_monthly_summary(self, year: int, month: int) -> dict:
        start, end = month_range(year, month)
        stats = self.stats_service.get_monthly_stats(year, month)
        return self._resolve("monthly_summaries", start, end, stats, "monthly")

    # ---------- 通用解析 ----------

    def _resolve(
        self,
        table: str,
        start: str,
        end: str,
        stats: dict,
        kind: str,
    ) -> dict:
        fingerprint = _stats_fingerprint(stats)
        cached = self.cache_repo.get(table, start, end)

        # 缓存命中：统计没变，直接用缓存（含 AI 结论，若有）
        if cached is not None and cached["stats_json"] == fingerprint:
            return {
                "start": start,
                "end": end,
                "stats": stats,
                "ai_summary": cached.get("ai_summary_json") or None,
                "source": cached.get("source", "local"),
                "cached": True,
            }

        # 缓存未命中或统计已变化：重新生成并保存
        ai_summary_json = ""
        source = "local"
        if self.ai_generator is not None and self.ai_generator.is_configured():
            try:
                if kind == "weekly":
                    ai_summary = self.ai_generator.generate_weekly(stats)
                else:
                    ai_summary = self.ai_generator.generate_monthly(stats)
                ai_summary_json = _dataclass_to_json(ai_summary)
                source = "ai"
            except AIServiceError:
                # AI 失败：仅本地统计，仍正常返回
                ai_summary_json = ""
                source = "local"

        self.cache_repo.upsert(
            table, start, end,
            stats_json=fingerprint,
            ai_summary_json=ai_summary_json,
            source=source,
        )
        return {
            "start": start,
            "end": end,
            "stats": stats,
            "ai_summary": ai_summary_json or None,
            "source": source,
            "cached": False,
        }


def _dataclass_to_json(obj) -> str:
    """把 WeeklySummary / MonthlySummary 序列化为 JSON。"""
    import dataclasses

    return json.dumps(dataclasses.asdict(obj), ensure_ascii=False)
