"""JD 入库与“JD → 技能优先级”闭环（Phase B）。

职责：
- add_jd            : 入库（原文完整保存 + 解析 + 幂等去重）
- parse_jd          : 规则解析 + 可选 AI 解析（AI 失败回退规则，不伪造）
- recompute_skill_frequencies : 从 jds 全量重算 skills.jd_frequency
- preview_impact    : 干跑影响分析（不写库）
- preview_weekly_priorities : 未来 1~2 周纯规则预览（不建任务/不改计划阶段）

边界：
- 只影响“短期优先级”（skills），绝不修改 career_context 长期路线、
  study_plan / phase / topic、Planner 每日任务生成。
- mastery 只来自 assessment_repo；AI 解析是 optional enhancement。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..ai.prompt_registry import PromptRegistry
from ..ai.prompts import (
    JD_AI_FORMAT,
    JD_AI_SYSTEM,
    build_jd_parse_vars,
    render_prompt,
)
from ..database.skill_repository import JdRepository, SkillRepository
from ..utils.date_utils import add_days
from .skill_service import SkillService

# ---------------- 判定用常量 ----------------

# 技能别名词典：key = 已有 skill 名，value = 触发词（小写英文 + 中文原文）
_SKILL_KEYWORDS: dict[str, list[str]] = {
    "Python": ["python"],
    "PyTorch": ["pytorch"],
    "Transformer": ["transformer"],
    "LLM 基础": ["llm", "大模型", "大语言模型", "语言模型"],
    "Hugging Face": ["hugging face", "huggingface", "transformers"],
    "SFT": ["sft", "指令微调", "监督微调"],
    "LoRA / QLoRA": ["lora", "qlora", "peft"],
    "Embedding": ["embedding", "语义向量", "向量化"],
    "RAG": ["rag", "检索增强"],
    "推荐系统基础": ["推荐系统", "推荐算法", "推荐"],
    "Recall": ["召回", "recall"],
    "Ranking": ["排序", "rank"],
    "CTR": ["ctr", "点击率", "预估点击"],
    "Rerank": ["重排", "rerank", "精排"],
    "用户画像": ["用户画像", "画像"],
    "Agent": ["agent", "智能体"],
    "模型评估": ["评测", "评估", "评价体系"],
    "SQL": ["sql"],
    "Linux": ["linux"],
    "Git": ["git"],
    "VLM": ["vlm", "多模态", "视觉语言"],
    "vLLM": ["vllm"],
    "Docker": ["docker"],
    "C++": ["c++", "cpp"],
    "CUDA": ["cuda"],
    "分布式训练底层": ["分布式训练", "ddp", "deepspeed", "zero"],
}

# 方向关键词
_DIRECTION_KEYWORDS: dict[str, list[str]] = {
    "recommendation": ["推荐", "召回", "排序", "点击率", "ctr", "用户画像", "feed"],
    "search": ["搜索", "search", "query", "检索"],
    "ads": ["广告", "ads", "投放"],
    "llm": ["大模型", "llm", "语言模型", "gpt", "rag", "agent", "智能体"],
    "agent": ["agent", "智能体", "工具调用", "tool calling"],
    "nlp": ["nlp", "自然语言", "分词", "ner"],
    "ml": ["机器学习", "ml", "特征工程"],
    "dl": ["深度学习", "神经网络"],
}

# plus 标记（该语段内出现的技能视为加分项）
_PLUS_MARKERS = ("优先", "加分", "更佳", "优势")
# 语段切分（保留空格/换行；逗号、句号、分号、顿号切句）
_SEGMENT_SPLIT_RE = re.compile(r"[。！？；\n，,]")
_INTERN_RE = re.compile(r"实习|intern", re.IGNORECASE)

DEFAULT_CAREER_CONTEXT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "career_context.json"
)

# ---------------- AI 解析（optional enhancement） ----------------

def build_default_parse_ai(ai_client, prompt_registry: PromptRegistry | None = None):
    """用 ai_client.chat 构造默认 AI 解析函数（JD 原文 -> JSON 文本）。

    Prompt 统一经 PromptRegistry（支持用户在 AI 设置中覆盖）。
    """

    def _parse(raw_text: str) -> str:
        return ai_client.chat(
            render_prompt("jd_parse.system", {}, prompt_registry),
            render_prompt(
                "jd_parse.user", build_jd_parse_vars(raw_text), prompt_registry
            ),
        )

    return _parse


def _ascii_only(s: str) -> bool:
    return bool(s) and s.isascii()


def _word_contains(alias: str, text_lower: str) -> bool:
    """别名是否出现在文本：纯 ASCII 用词边界，含中文用子串。"""
    alias_l = alias.lower().strip()
    if not alias_l:
        return False
    if _ascii_only(alias_l):
        return re.search(rf"\b{re.escape(alias_l)}\b", text_lower) is not None
    return alias_l in text_lower


class JdService:
    """收 JD -> 规则/AI 解析 -> 技能映射 -> 更新 frequency -> 影响分析。"""

    def __init__(
        self,
        jd_repo: JdRepository,
        skill_repo: SkillRepository,
        skill_service: SkillService,
        ai_client=None,
        parse_ai=None,
    ):
        self.jd_repo = jd_repo
        self.skill_repo = skill_repo
        self.skill_service = skill_service
        self.ai_client = ai_client
        # 可注入自定义 AI 调用（测试用）；缺省 None（此时只走规则）。
        self.parse_ai = parse_ai

    # ================= 技能识别 =================

    def _existing_skill_names(self) -> list[str]:
        return [s["name"] for s in self.skill_repo.list_all()]

    def _match_skills(self, raw_text: str) -> list[str]:
        """只匹配“当前 skills 中已存在”的技能名（顺序稳定）。"""
        text_lower = raw_text.lower()
        found = []
        for name in self._existing_skill_names():
            kws = _SKILL_KEYWORDS.get(name, [name])
            if any(_word_contains(kw, text_lower) for kw in kws):
                found.append(name)
        return found

    def _normalize_names(self, names: list[str]) -> tuple[list[str], list[str]]:
        """把 AI 给的技能名映射回已有技能；返回 (matched, unmatched)。

        映射顺序：完全一致 / 别名识别 / 去掉空白后一致；未匹配不强行绑定。
        """
        matched: list[str] = []
        unmatched: list[str] = []
        existing = self._existing_skill_names()
        for raw in (names or []):
            raw = str(raw).strip()
            if not raw:
                continue
            hit = None
            if raw in existing:
                hit = raw
            else:
                low = raw.lower().replace(" ", "")
                for name in existing:
                    if low == name.lower().replace(" ", ""):
                        hit = name
                        break
                if hit is None:
                    matched_now = self._match_skills(raw)
                    if matched_now:
                        hit = matched_now[0]
            if hit is not None and hit not in matched:
                matched.append(hit)
            elif hit is None:
                unmatched.append(raw)
        return matched, unmatched

    # ================= 解析 =================

    def parse_jd(self, raw_text: str, use_ai: bool = True) -> dict:
        """解析一条 JD 文本。

        :return: {
            direction, intern,
            must: [已匹配技能名], plus: [已匹配技能名],
            method: "rules"|"ai"|"rules_fallback",
            raw: {"direction", "must", "plus"} 作为规则/AI 原始结果（审计）
        }
        """
        raw_text = raw_text or ""
        rules = self._parse_by_rules(raw_text)

        if not use_ai or self.parse_ai is None:
            return rules

        try:
            content = self.parse_ai(raw_text)
            ai_data = self._validate_ai_output(content)
        except Exception:  # noqa: BLE001 - AI 失败不阻断，回退规则
            return {**rules, "method": "rules_fallback"}

        if ai_data is None:
            return {**rules, "method": "rules_fallback"}

        ai_must, _ = self._normalize_names(ai_data.get("must", []))
        ai_plus, _ = self._normalize_names(ai_data.get("plus", []))
        # AI 全没匹配到已有技能但规则匹配到了 → 用规则（更可靠）
        if not ai_must and not ai_plus and (rules["must"] or rules["plus"]):
            return {**rules, "method": "rules_fallback"}

        direction = str(ai_data.get("direction") or rules["direction"])
        return {
            "direction": direction,
            "intern": bool(ai_data.get("intern", rules["intern"])),
            "must": ai_must,
            "plus": ai_plus,
            "method": "ai",
            "raw": {"direction": direction,
                    "must": ai_data.get("must", []),
                    "plus": ai_data.get("plus", [])},
        }

    def _parse_by_rules(self, raw_text: str) -> dict:
        text_lower = raw_text.lower()
        direction = self._detect_direction(text_lower)
        intern = bool(_INTERN_RE.search(raw_text))

        must: list[str] = []
        plus: list[str] = []
        segments = [s for s in _SEGMENT_SPLIT_RE.split(raw_text) if s.strip()]

        for name in self._existing_skill_names():
            kws = _SKILL_KEYWORDS.get(name, [name])
            seg_plus = False
            seg_must = False
            for seg in segments:
                if not any(_word_contains(kw, seg.lower()) for kw in kws):
                    continue
                if any(m in seg for m in _PLUS_MARKERS):
                    seg_plus = True
                else:
                    seg_must = True  # 出现在无加分标记的语段
            if not (seg_plus or seg_must):
                continue
            # 同一 JD / 同一技能：must 优先于 plus
            if seg_must:
                must.append(name)
            else:
                plus.append(name)

        order = self._existing_skill_names()
        must.sort(key=lambda x: order.index(x))
        plus.sort(key=lambda x: order.index(x))
        return {
            "direction": direction,
            "intern": intern,
            "must": must,
            "plus": plus,
            "method": "rules",
            "raw": {"direction": direction, "must": list(must),
                    "plus": list(plus)},
        }

    @staticmethod
    def _detect_direction(text_lower: str) -> str:
        best, best_n = "", 0
        for direct, kws in _DIRECTION_KEYWORDS.items():
            n = sum(1 for k in kws if k in text_lower)
            if n > best_n:
                best, best_n = direct, n
        return best

    @classmethod
    def _validate_ai_output(cls, content: str) -> dict | None:
        """严格校验 AI 结构化输出；非法返回 None（调用方回退规则）。"""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        for key in ("direction", "must", "plus", "intern"):
            if key not in data:
                return None
        if not isinstance(data["direction"], str):
            return None
        if not isinstance(data["must"], list) or not isinstance(data["plus"], list):
            return None
        if not all(isinstance(x, str) for x in data["must"] + data["plus"]):
            return None
        if not isinstance(data["intern"], (bool, int)):
            return None
        return {
            "direction": data["direction"].strip(),
            "must": data["must"],
            "plus": data["plus"],
            "intern": bool(data["intern"]),
        }

    # ================= JD 入库 =================

    @staticmethod
    def content_hash(raw_text: str) -> str:
        norm = re.sub(r"\s+", " ", (raw_text or "").strip()).lower()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    def add_jd(
        self,
        raw_text: str,
        company: str = "",
        title: str = "",
        use_ai: bool = True,
    ) -> dict:
        """入库一条 JD（原文 + 解析 + 幂等去重），并刷新频率与优先级。

        :return: {"id", "inserted", "idempotent", "parsed", "impact"}
        """
        raw_text = raw_text or ""
        h = self.content_hash(raw_text)
        existing = self.jd_repo.get_by_content_hash(h)
        if existing is not None:
            return {
                "id": existing["id"],
                "inserted": False,
                "idempotent": True,
                "parsed": existing["parsed"],
                "impact": self.preview_impact(raw_text, use_ai=use_ai),
            }

        parsed = self.parse_jd(raw_text, use_ai=use_ai)

        # 写入前快照“无此 JD”的基线（before 用）
        baseline_scores = self._current_scores()
        baseline_freq = self._baseline_frequencies()

        jd = self.jd_repo.create(
            company=company,
            title=title,
            direction=parsed["direction"],
            raw_text=raw_text,
            parsed=parsed,
            content_hash=h,
        )
        self.recompute_skill_frequencies()
        self.skill_service.recompute_all_priority_scores()

        return {
            "id": jd["id"],
            "inserted": True,
            "idempotent": False,
            "parsed": parsed,
            "impact": self._assemble_impact(
                parsed, baseline_scores, baseline_freq, applied=True
            ),
        }

    def add_jd_from_file(
        self,
        path: str | Path,
        company: str = "",
        title: str = "",
        use_ai: bool = True,
    ) -> dict:
        """从文件读取 JD 文本后入库。"""
        text = Path(path).read_text(encoding="utf-8")
        return self.add_jd(text, company=company, title=title, use_ai=use_ai)

    # ================= frequency =================

    def recompute_skill_frequencies(self) -> dict[str, dict]:
        """从 jds 全量重算 skills.jd_frequency（可删除 JD 后重新计算）。

        同一 JD 对同一技能最多计数一次（parsed.must/plus 去重）。
        :return: {skill_name: {"must", "plus", "total_jds"}}
        """
        jds = self.jd_repo.list_all()
        total = len(jds)
        must_cnt: dict[str, int] = {}
        plus_cnt: dict[str, int] = {}
        for jd in jds:
            p = jd.get("parsed") or {}
            for m in set(p.get("must") or []):
                must_cnt[m] = must_cnt.get(m, 0) + 1
            for p_ in set(p.get("plus") or []):
                plus_cnt[p_] = plus_cnt.get(p_, 0) + 1

        result: dict[str, dict] = {}
        for skill in self.skill_repo.list_all():
            name = skill["name"]
            freq = {
                "must": must_cnt.get(name, 0),
                "plus": plus_cnt.get(name, 0),
                "total_jds": total,
            }
            self.skill_repo.update(skill["id"], jd_frequency=freq)
            result[name] = freq
        return result

    def _baseline_frequencies(self) -> dict[str, dict]:
        return {
            s["name"]: dict(s.get("jd_frequency") or {})
            for s in self.skill_repo.list_all()
        }

    def _frequencies_after_adding(
        self, baseline: dict[str, dict], must: list[str], plus: list[str]
    ) -> dict[str, dict]:
        """纯计算：在 baseline 基础上再加一条 JD 后的频率。不写库。"""
        out: dict[str, dict] = {}
        for skill in self.skill_repo.list_all():
            name = skill["name"]
            f = dict(baseline.get(name, {"must": 0, "plus": 0, "total_jds": 0}))
            if name in must:
                f["must"] += 1
            if name in plus:
                f["plus"] += 1
            f["total_jds"] = f.get("total_jds", 0) + 1
            out[name] = f
        return out

    def _current_scores(self) -> dict[str, float]:
        return {
            s["name"]: s["priority_score"] for s in self.skill_repo.list_all()
        }

    # ================= 影响分析 =================

    def preview_impact(self, raw_text: str, use_ai: bool = True) -> dict:
        """干跑：不改库，输出该 JD 的影响分析。"""
        parsed = self.parse_jd(raw_text, use_ai=use_ai)
        return self._assemble_impact(
            parsed,
            self._current_scores(),
            self._baseline_frequencies(),
            applied=False,
        )

    def _assemble_impact(
        self,
        parsed: dict,
        baseline_scores: dict[str, float],
        baseline_freq: dict[str, dict],
        applied: bool,
    ) -> dict:
        must = [m for m in parsed.get("must", []) if self.skill_repo.get_by_name(m)]
        plus = [p for p in parsed.get("plus", []) if self.skill_repo.get_by_name(p)]
        after_freq = self._frequencies_after_adding(baseline_freq, must, plus)

        skills_by_name = {s["name"]: s for s in self.skill_repo.list_all()}
        affected: list[dict] = []
        jd_gap: list[dict] = []
        blocked: list[dict] = []
        for name in must + plus:
            skill = skills_by_name.get(name)
            if skill is None:
                continue
            before = float(baseline_scores.get(name, 0.0))
            modified = {**skill, "jd_frequency": after_freq.get(name, {})}
            detail = self.skill_service.compute_skill_score(modified)
            role = "must" if name in must else "plus"
            entry = {
                "skill": name,
                "tier": skill["tier"],
                "role": role,
                "freq_before": dict(baseline_freq.get(
                    name, {"must": 0, "plus": 0, "total_jds": 0})),
                "freq_after": after_freq.get(name, {}),
                "priority_before": round(before, 6),
                "priority_after": detail["score"],
                "mastery_estimate": detail["mastery_estimate"],
                "gate": detail["gate"],
                "explanation": _explain(name, skill["tier"], role,
                                        detail["gate"]),
            }
            affected.append(entry)
            if detail["gate"] == "blocked":
                blocked.append(entry)
            elif skill["status"] in ("not_started", "learning"):
                jd_gap.append(entry)

        # 未来 1~2 周重点关注：未掌握 + 门禁放行，按优先级变化排序
        focus = [e for e in jd_gap if e["gate"] == "ok"]
        focus.sort(key=lambda e: (
            e["gate"] != "ok",
            -e["priority_after"],
            e["skill"],
        ))
        # 低优先级（B/C）不建议抢占主线
        not_rush = [e["skill"] for e in affected if e["tier"] in ("B", "C")]

        return {
            "parsed": parsed,
            "affected_skills": affected,
            "jd_gap_skills": jd_gap,
            "prerequisite_blocked": blocked,
            "weekly_focus": focus,
            "not_rush": not_rush,
            "applied": applied,
        }

    # ================= 未来 1~2 周预览 =================

    def preview_weekly_priorities(
        self, today: str, days: int = 7, top_each_day: int = 3
    ) -> dict:
        """未来 days 天纯规则预览。

        不创建 tasks、不修改 study_plan / phase / topic、不推进阶段。
        :return: {
            today, window_days,
            daily_focus: [{date, skills, note}],
            overall_priority: [{rank, skill, tier, score, jd_frequency, gate}],
            do_not_prioritize: [低优先技能]
        }
        """
        candidates = self.skill_service.select_active_candidates()
        n = len(candidates)
        daily_focus = []
        for i in range(days):
            date = add_days(today, 1 + i)
            picked = [candidates[(j + i) % n]["name"]
                      for j in range(min(top_each_day, n))] if n else []
            daily_focus.append({
                "date": date,
                "skills": picked,
                "note": "优先：{}".format("、".join(picked)) if picked
                        else "无可用候选",
            })

        overall = [
            {
                "rank": i + 1,
                "skill": d["name"],
                "tier": d["tier"],
                "score": d["score"],
                "jd_frequency": d["jd_frequency"],
                "gate": d["gate"],
            }
            for i, d in enumerate(candidates)
        ]
        do_not_prioritize = [
            s["name"] for s in self.skill_repo.list_all()
            if s["status"] in ("deferred", "mastered") or s["tier"] == "C"
        ]
        return {
            "today": today,
            "window_days": days,
            "daily_focus": daily_focus,
            "overall_priority": overall,
            "do_not_prioritize": do_not_prioritize,
        }


def _explain(name: str, tier: str, role: str, gate: str) -> str:
    why = f"{name}：{tier}级"
    why += " + 本 JD 为 must（必备）" if role == "must" else " + 本 JD 为 plus（加分）"
    if gate == "blocked":
        why += " + 前置技能未满足 → 不越级提前学习"
    else:
        why += " + 门禁放行"
    return why
