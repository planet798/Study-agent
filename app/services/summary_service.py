"""总结服务：把本地统计 + AI 解释 + 缓存整合起来。

流程：
1. 本地计算统计（StatsService，作为事实）；
2. 检查缓存：若同一期间、stats_json 未变化且有 AI 结论 → 直接复用；
3. 无有效缓存 → 若 AI 可用则生成 AI 总结并缓存，否则仅本地统计。

当前产品只保留**月总结**（周总结已移除）。
"""

from __future__ import annotations

import json

from ..ai.interface import AIServiceError
from ..ai.summary import AISummaryGenerator
from ..database.study_plan_repository import SummaryCacheRepository
from ..utils.date_utils import month_range
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
        route_progress_service=None,
    ):
        self.stats_service = stats_service
        self.cache_repo = cache_repo
        self.ai_generator = ai_generator
        # Phase E：月总结路线维度（可选；不注入则保持旧行为）
        self.route_progress_service = route_progress_service

    # ================= 月总结 =================

    def get_monthly_summary(self, year: int, month: int) -> dict:
        start, end = month_range(year, month)
        stats = self.stats_service.get_monthly_stats(year, month)
        # Phase E：加入 route 维度，纳入 fingerprint → 学习记录变化时缓存失效
        stats["route_stats"] = self._route_stats(start, end)
        return self._resolve("monthly_summaries", start, end, stats)

    def _route_stats(self, start: str, end: str) -> list[dict]:
        if self.route_progress_service is None:
            return []
        try:
            return self.route_progress_service.monthly_route_stats(start, end)
        except Exception:  # noqa: BLE001 - 路线统计失败不影响月总结
            return []

    # ---------- 通用解析 ----------

    def _resolve(
        self,
        table: str,
        start: str,
        end: str,
        stats: dict,
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
    """把 MonthlySummary 序列化为 JSON。"""
    import dataclasses

    return json.dumps(dataclasses.asdict(obj), ensure_ascii=False)
