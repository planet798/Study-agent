"""近期市场需求信号。

把「每日 JD 技术汇总」的**近 30 天**趋势，转成一个确定、可解释、范围 0~1 的
“技能市场信号”，供 SkillService 作为 priority 的市场需求因子。

设计：
- 唯一来源：jd_daily_summaries（人工收集的目标岗位样本）。
- market_signal = frequency_30d
                = skill_mentions_30d / sample_count_30d。
- 30 天窗口已足够平滑（用户每天录入的是人工筛选后的目标实习 JD），逻辑直观。
- 30 天没有样本 → source="none"（调用方回退到 individual JD）。
- 不做指数衰减（滚动窗口本身就是时间衰减）；不使用 AI。
"""

from __future__ import annotations

DEFAULT_TARGET = "internship"
DEFAULT_WINDOW_DAYS = 30


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class MarketSignal:
    """调用 JdSummaryService 的近 30 天趋势，产出技能市场信号。"""

    def __init__(self, jd_summary_service=None):
        self.jd_summary_service = jd_summary_service

    def compute(
        self, end_date: str, target_type: str = DEFAULT_TARGET
    ) -> dict:
        out = {
            "source": "none",
            "target_type": target_type,
            "end_date": end_date,
            "window_days": DEFAULT_WINDOW_DAYS,
            "sample_count_30d": 0,
            "skills": {},
        }
        if self.jd_summary_service is None:
            return out
        try:
            t30 = self.jd_summary_service.compute_skill_trends(
                end_date, DEFAULT_WINDOW_DAYS, target_type
            )
        except Exception:  # noqa: BLE001 - 市场数据异常不影响主流程
            return out

        n30 = int(t30.get("sample_count") or 0)
        out["sample_count_30d"] = n30
        if n30 <= 0:
            return out  # 无样本 → fallback individual JD

        out["source"] = "daily_summary"
        for r in t30.get("skills") or []:
            name = r["name"]
            freq30 = float(r.get("frequency") or 0.0)
            out["skills"][name] = {
                "freq30": round(freq30, 4),
                "mention_30d": int(r.get("mention_count") or 0),
                "signal": round(_clamp(freq30), 4),
            }
        return out
