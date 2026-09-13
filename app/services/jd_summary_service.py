"""每日 JD 技术汇总服务（Step 4）。

职责（只做“市场样本”的数据层，不碰 Planner / priority / UI）：
- parse_summary_text : 解析人工汇总文本（"Python 13" / "Python：13" /
  "Python must=10 plus=2" / "Python 13/15"）
- preview_summary    : 写库前校验 + 技能标准化 + 生成预览（不写库）
- save_summary       : 原子写入（同日同目标 → update + replace stats）
- get_summary / list_recent_summaries
- compute_skill_trends : 14/30 天近期市场频率
- get_unmatched_skills

边界：
- 数字全部来自用户输入，绝不猜 mention_count / sample_count，
  也不调用 AI 决定频率或映射。
- 技能标准化只用**明确别名**（精确名 / 别名词典），无法可靠映射 → unmatched。
- Step 4 的趋势只基于 jd_daily_summaries；历史 individual JD 完整保留但不并入
  频率，避免同一批岗位双重计数。
"""

from __future__ import annotations

import re

from ..database.jd_summary_repository import JdDailySummaryRepository
from ..database.skill_repository import SkillRepository
from ..utils.date_utils import add_days

# 目标类型
TARGET_TYPES = ("internship", "campus", "fulltime")
DEFAULT_TARGET_TYPE = "internship"

# 明确别名（小写键 -> 标准技能名）。只放“无歧义”的缩写 / 中英别名。
_EXPLICIT_ALIASES: dict[str, str] = {
    "hf": "Hugging Face",
    "huggingface": "Hugging Face",
    "hugging face": "Hugging Face",
    "recsys": "推荐系统基础",
    "rec system": "推荐系统基础",
    "推荐系统": "推荐系统基础",
    "推荐系统基础": "推荐系统基础",
    "召回": "Recall",
    "recall": "Recall",
    "排序": "Ranking",
    "rank": "Ranking",
    "ranking": "Ranking",
    "llm": "LLM 基础",
    "大模型": "LLM 基础",
    "大语言模型": "LLM 基础",
    "rerank": "Rerank",
    "重排": "Rerank",
    "精排": "Rerank",
    "ctr": "CTR",
    "点击率": "CTR",
    "moe": "MoE",
    "sft": "SFT",
    "lora": "LoRA / QLoRA",
    "qlora": "LoRA / QLoRA",
    "peft": "PEFT",
    "rag": "RAG",
    "检索增强": "RAG",
    "embedding": "Embedding",
    "向量检索": "Embedding",
    "agent": "Agent",
    "智能体": "Agent",
    "c++": "C++",
    "cpp": "C++",
    "sql": "SQL",
    "linux": "Linux",
    "git": "Git",
    "docker": "Docker",
    "python": "Python",
    "pytorch": "PyTorch",
    "transformer": "Transformer",
}

_MUST_RE = re.compile(r"(?i)\bmust\s*=\s*(\d+)\b")
_PLUS_RE = re.compile(r"(?i)\bplus\s*=\s*(\d+)\b")
_BULLET_RE = re.compile(r"^[-*•]\s*")
_COLON_RE = re.compile(r"^(?P<name>.+?)\s*[:：]\s*(?P<num>\d+)\s*(?:/\s*\d+)?\s*$")
_SPACE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<num>\d+)\s*(?:/\s*\d+)?\s*$")


def _norm_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


class JdSummaryService:
    def __init__(
        self,
        summary_repo: JdDailySummaryRepository,
        skill_repo: SkillRepository,
    ):
        self.summary_repo = summary_repo
        self.skill_repo = skill_repo

    # ================= 技能标准化 =================

    def _existing_by_norm(self) -> dict[str, dict]:
        """标准技能名（规范化后）-> skill。"""
        out: dict[str, dict] = {}
        for s in self.skill_repo.list_all():
            out.setdefault(_norm_key(s["name"]), s)
        return out

    def normalize_skill(self, raw_name: str, existing: dict | None = None):
        """把用户写的技能名映射到已有 skill；无法可靠映射返回 None。

        仅使用：① 与已有技能名的精确匹配（忽略大小写/空格）；② 明确别名词典。
        禁止模糊子串猜测。
        """
        raw = (raw_name or "").strip()
        if not raw:
            return None
        existing = existing if existing is not None else self._existing_by_norm()
        key = _norm_key(raw)
        if key in existing:
            return existing[key]
        target = _EXPLICIT_ALIASES.get(key)
        if target is None:
            return None
        return existing.get(_norm_key(target))

    # ================= 解析 =================

    def parse_summary_text(self, text: str):
        """解析汇总文本，返回 (entries, errors)。

        entry: {raw_skill_name, mention_count, must_count, plus_count, line}
        """
        entries: list[dict] = []
        errors: list[str] = []
        for lineno, raw_line in enumerate((text or "").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            line = _BULLET_RE.sub("", line).strip()

            m_must = _MUST_RE.search(line)
            m_plus = _PLUS_RE.search(line)
            must = int(m_must.group(1)) if m_must else None
            plus = int(m_plus.group(1)) if m_plus else None
            rest = _MUST_RE.sub("", line)
            rest = _PLUS_RE.sub("", rest).strip()
            rest = rest.strip(" |,，;；").strip()

            name = None
            mention = None
            m = _COLON_RE.match(rest) or _SPACE_RE.match(rest)
            if m:
                name = m.group("name").strip()
                mention = int(m.group("num"))
            elif rest:
                name = rest

            if not name:
                errors.append(f"第 {lineno} 行无法解析: {raw_line!r}")
                continue
            if mention is None:
                if must is None and plus is None:
                    errors.append(f"第 {lineno} 行缺少数量: {raw_line!r}")
                    continue
                mention = (must or 0) + (plus or 0)
            if mention < 0 or (must or 0) < 0 or (plus or 0) < 0:
                errors.append(f"第 {lineno} 行数量为负: {raw_line!r}")
                continue
            entries.append({
                "raw_skill_name": name,
                "mention_count": mention,
                "must_count": must or 0,
                "plus_count": plus or 0,
                "line": lineno,
            })
        return entries, errors

    @staticmethod
    def _merge_duplicates(entries: list[dict]) -> list[dict]:
        """同一原始技能名重复出现时合并（累加），避免出现两行统计。"""
        merged: dict[str, dict] = {}
        order: list[str] = []
        for e in entries:
            key = _norm_key(e["raw_skill_name"])
            if key not in merged:
                merged[key] = dict(e)
                order.append(key)
            else:
                t = merged[key]
                t["mention_count"] += e["mention_count"]
                t["must_count"] += e["must_count"]
                t["plus_count"] += e["plus_count"]
        return [merged[k] for k in order]

    # ================= 预览 / 校验 =================

    def preview_summary(
        self,
        text: str,
        sample_count: int,
        target_type: str = DEFAULT_TARGET_TYPE,
        summary_date: str | None = None,
    ) -> dict:
        """写库前校验 + 标准化，返回预览（绝不写库）。"""
        errors: list[str] = []
        try:
            sample_count = int(sample_count)
        except (TypeError, ValueError):
            sample_count = 0
        if sample_count <= 0:
            errors.append("sample_count 必须是正整数")

        if target_type not in TARGET_TYPES:
            errors.append(f"未知 target_type: {target_type}")

        entries, parse_errors = self.parse_summary_text(text)
        errors.extend(parse_errors)
        entries = self._merge_duplicates(entries)

        existing = self._existing_by_norm()
        matched: list[dict] = []
        unmatched: list[dict] = []
        for e in entries:
            if e["must_count"] + e["plus_count"] > e["mention_count"]:
                errors.append(
                    f"{e['raw_skill_name']}: must+plus"
                    f"({e['must_count']}+{e['plus_count']}) 超过 mention_count"
                    f"({e['mention_count']})"
                )
            if sample_count > 0 and e["mention_count"] > sample_count:
                errors.append(
                    f"{e['raw_skill_name']}: mention_count"
                    f"({e['mention_count']}) 超过 sample_count({sample_count})"
                )
            skill = self.normalize_skill(e["raw_skill_name"], existing)
            freq = (e["mention_count"] / sample_count) if sample_count else 0.0
            row = {
                "raw_skill_name": e["raw_skill_name"],
                "skill_id": skill["id"] if skill else None,
                "name": skill["name"] if skill else e["raw_skill_name"],
                "mention_count": e["mention_count"],
                "must_count": e["must_count"],
                "plus_count": e["plus_count"],
                "frequency": round(freq, 4),
            }
            (matched if skill else unmatched).append(row)

        matched.sort(key=lambda r: (-r["mention_count"], r["name"]))
        unmatched.sort(key=lambda r: (-r["mention_count"], r["raw_skill_name"]))
        return {
            "summary_date": summary_date,
            "target_type": target_type,
            "sample_count": sample_count,
            "matched": matched,
            "unmatched": unmatched,
            "errors": errors,
            "valid": not errors,
        }

    # ================= 保存 =================

    def save_summary(
        self,
        summary_date: str,
        text: str,
        sample_count: int,
        target_type: str = DEFAULT_TARGET_TYPE,
        note: str = "",
    ) -> dict:
        """校验并原子保存；有错误直接抛 ValueError（不静默截断/丢弃）。"""
        preview = self.preview_summary(
            text, sample_count, target_type, summary_date
        )
        if not preview["valid"]:
            raise ValueError("; ".join(preview["errors"]))

        stats = [
            {
                "skill_id": r["skill_id"],
                "raw_skill_name": r["raw_skill_name"],
                "mention_count": r["mention_count"],
                "must_count": r["must_count"],
                "plus_count": r["plus_count"],
            }
            for r in preview["matched"] + preview["unmatched"]
        ]
        return self.summary_repo.upsert_summary(
            summary_date=summary_date,
            target_type=target_type,
            sample_count=preview["sample_count"],
            raw_text=text or "",
            note=note or "",
            stats=stats,
        )

    # ================= 读取 =================

    def get_summary(self, summary_date: str, target_type: str = DEFAULT_TARGET_TYPE):
        return self.summary_repo.get_summary(summary_date, target_type)

    def list_recent_summaries(
        self,
        target_type: str = DEFAULT_TARGET_TYPE,
        end_date: str | None = None,
        window_days: int = 30,
    ) -> list[dict]:
        end_date = end_date or self._today()
        start = add_days(end_date, -(int(window_days) - 1))
        return self.summary_repo.list_summaries(
            start_date=start, end_date=end_date, target_type=target_type
        )

    # ================= 趋势 =================

    def compute_skill_trends(
        self,
        end_date: str,
        window_days: int = 14,
        target_type: str = DEFAULT_TARGET_TYPE,
    ) -> dict:
        """窗口 [end_date-(N-1), end_date] 内的近期市场频率。

        frequency = skill_mentions / window_sample_count
        只统计 jd_daily_summaries（不并入历史 individual JD）。
        """
        window_days = max(1, int(window_days))
        start_date = add_days(end_date, -(window_days - 1))
        rows = self.summary_repo.list_stats_rows(
            start_date=start_date, end_date=end_date, target_type=target_type
        )
        sample_count = 0
        # sample_count 是“汇总级别”的：按 summary 去重累加（不按 stats 行重复加）
        seen_summaries: dict[int, int] = {}
        for r in rows:
            seen_summaries.setdefault(r["summary_id"], int(r["sample_count"] or 0))
        sample_count = sum(seen_summaries.values())

        skills_by_id = {s["id"]: s["name"] for s in self.skill_repo.list_all()}
        agg: dict = {}
        unmatched: dict = {}
        for r in rows:
            if r.get("skill_id"):
                key = ("skill", r["skill_id"])
                bucket = agg
            else:
                key = ("raw", _norm_key(r.get("raw_skill_name") or ""))
                bucket = unmatched
            rec = bucket.setdefault(key, {
                "skill_id": r.get("skill_id"),
                "name": skills_by_id.get(r["skill_id"]) or r.get("raw_skill_name"),
                "mention_count": 0,
                "must_count": 0,
                "plus_count": 0,
            })
            rec["mention_count"] += int(r.get("mention_count") or 0)
            rec["must_count"] += int(r.get("must_count") or 0)
            rec["plus_count"] += int(r.get("plus_count") or 0)

        def _finalize(bucket):
            out = []
            for rec in bucket.values():
                freq = (rec["mention_count"] / sample_count
                        if sample_count else 0.0)
                out.append({**rec, "frequency": round(freq, 4)})
            out.sort(key=lambda x: (
                -x["mention_count"], -x["frequency"], x["name"] or ""
            ))
            return out

        return {
            "window_days": window_days,
            "start_date": start_date,
            "end_date": end_date,
            "target_type": target_type,
            "sample_count": sample_count,
            "skills": _finalize(agg),
            "unmatched": _finalize(unmatched),
        }

    def get_unmatched_skills(
        self,
        end_date: str,
        window_days: int = 14,
        target_type: str = DEFAULT_TARGET_TYPE,
    ) -> list[dict]:
        return self.compute_skill_trends(
            end_date, window_days, target_type
        )["unmatched"]

    # ================= 内部 =================

    @staticmethod
    def _today() -> str:
        from ..utils.date_utils import today as _t

        return _t()
