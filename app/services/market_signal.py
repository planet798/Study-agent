"""近期市场需求信号（Step 6）。

把「每日 JD 技术汇总」的 14/30 天趋势，转成一个确定、可解释、范围 0~1 的
“技能市场信号”，供 SkillService 作为 priority 的市场需求因子。

设计：
- 主来源：jd_daily_summaries（人工收集的目标岗位样本）。
- 1) 14 天反映最近变化，30 天提供平滑参考。
- 2) 样本量决定可信度：confidence14 = n14 / (n14 + K)，K=20。
      n14 越大越信 14 天；n14 很小时主要由 30 天平滑。
- 3) signal = confidence14 * freq14 + (1 - confidence14) * freq30。
- 4) 14/30 天都没有样本 → source="none"（调用方回退到 individual JD）。
- 不做指数衰减（滚动窗口本身就是时间衰减）；不使用 AI。
"""

from __future__ import annotations

DEFAULT_TARGET = "internship"

# 14 天样本可信度：confidence = n / (n + K)
CONFIDENCE_K = 20


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def confidence(n14: int) -> float:
    """14 天样本量的可信度（0~1，单调递增）。"""
    n14 = max(0, int(n14))
    return n14 / (n14 + CONFIDENCE_K)


def combine_signal(
    freq14: float, freq30: float, n14: int, n30: int
) -> float:
    """由 14/30 天频率与样本量得到市场信号（0~1）。"""
    if n14 <= 0 and n30 <= 0:
        return 0.0
    conf = confidence(n14)
    return _clamp(conf * float(freq14) + (1 - conf) * float(freq30))


class MarketSignal:
    """调用 JdSummaryService 的 14/30 天趋势，产出技能市场信号。"""

    def __init__(self, summary_service=None):
        self.summary_service = summary_service

    def compute(
        self, end_date: str, target_type: str = DEFAULT_TARGET
    ) -> dict:
        out = {
            "source": "none",
            "target_type": target_type,
            "end_date": end_date,
            "sample_count_14d": 0,
            "sample_count_30d": 0,
            "skills": {},
        }
        if self.summary_service is None:
            return out
        try:
            t14 = self.summary_service.compute_skill_trends(
                end_date, 14, target_type
            )
            t30 = self.summary_service.compute_skill_trends(
                end_date, 30, target_type
            )
        except Exception:  # noqa: BLE001 - 市场数据异常不影响主流程
            return out

        n14 = int(t14.get("sample_count") or 0)
        n30 = int(t30.get("sample_count") or 0)
        out["sample_count_14d"] = n14
        out["sample_count_30d"] = n30
        if n14 <= 0 and n30 <= 0:
            return out  # 无样本 → fallback individual JD

        out["source"] = "daily_summary"
        f14 = {r["name"]: r for r in t14.get("skills") or []}
        f30 = {r["name"]: r for r in t30.get("skills") or []}
        for name in set(f14) | set(f30):
            r14 = f14.get(name) or {}
            r30 = f30.get(name) or {}
            freq14 = float(r14.get("frequency") or 0.0)
            freq30 = float(r30.get("frequency") or 0.0)
            out["skills"][name] = {
                "freq14": round(freq14, 4),
                "freq30": round(freq30, 4),
                "mention_14d": int(r14.get("mention_count") or 0),
                "mention_30d": int(r30.get("mention_count") or 0),
                "signal": round(combine_signal(freq14, freq30, n14, n30), 4),
            }
        return out
