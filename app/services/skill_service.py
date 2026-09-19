"""技能优先级计算（Phase A：纯确定性规则，不接 Planner）。

score(skill) =
    0.45 * tier_weight
  + 0.30 * jd_factor
  + 0.15 * active_needed
  + 0.10 * connector_bonus

- tier_weight : S=4 / A=3 / B=2 / C=1
- jd_factor   : 来自 skills.jd_frequency（{must, plus, total_jds}），
                无 JD 证据时中性回退（不引入虚构数）
- active_needed: 由 status + 现有验收/掌握证据共同决定；
                 没有 mastery 证据时不推断“不会”
- connector_bonus: 对 Embedding / Recall / Ranking / Rerank 这类
                共享能力提供有限 bonus（0.10 权重封顶）

前置依赖门禁（prerequisite gate）：
- 前置技能未达标（状态非 mastered 且无高掌握证据）→ 技能被阻塞
- 被阻塞技能不进入 active 候选，视为 deferred / blocked
- 不修改 get_current_phase / 不修改 Planner 的每日生成结果

边界：
- mastery 只来自 assessment_repo（knowledge_points.mastery_estimate），
  绝不把“没有证据”推断成“不会”。
- 本文件不写 planner / prompt / PlanningContext。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..database.skill_repository import (
    SkillRepository,
    STATUSES,
    TIERS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAREER_CONTEXT_PATH = PROJECT_ROOT / "docs" / "career_context.json"

# ---------------- 权重与常量 ----------------
W_TIER = 0.45
W_JD = 0.30
W_ACTIVE = 0.15
W_CONNECTOR = 0.10

TIER_WEIGHT = {"S": 4.0, "A": 3.0, "B": 2.0, "C": 1.0}

# 无 JD 证据时的中性回退值（不额外提权、也不额外降权）
JD_FALLBACK_FACTOR = 0.0

# abs 掌握证据阈值
MASTERY_HIGH = 0.85
MASTERY_LOW = 0.4

# status -> active_needed 基准
STATUS_ACTIVE_BASE = {
    "not_started": 0.8,
    "learning": 0.7,
    "mastered": 0.0,
    "deferred": 0.0,
}

# 共享能力（推荐/搜索/RAG 共同连接点）；名称不区分大小写匹配
CONNECTOR_DEFAULT = ("Embedding", "Recall", "Ranking", "Rerank")

# ---- Step 6：近期市场需求信号 ----
DEFAULT_MARKET_TARGET = "internship"
# 前置需求传播：Ranking 高频且 blocked → 向直接前置传递有限需求
PREREQ_PROP_DECAY = 0.5      # 每深一层衰减一半
PREREQ_PROP_MAX_DEPTH = 2    # 只传播两层（直接前置 + 其前置）
PREREQ_PROP_CAP = 0.5        # 单个技能最多获得的传播加成
# 当前阶段适配度（排序偏好；不属于 score 公式，避免改变既有尺度）
STAGE_RANK = {"current": 2, "next": 1, "far": 0, "unknown": 0}

# 课程缺口：正式技能近30天频率达到该值且无 linked topic 时标记
CURRICULUM_GAP_MIN_FREQUENCY = 0.10


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class SkillService:
    """技能的确定性优先级计算 / 门禁 / 主题映射 / 种子加载。"""

    def __init__(
        self,
        skill_repo: SkillRepository,
        plan_repo=None,
        assessment_repo=None,
        market_signal=None,
        current_phase_provider=None,
        route_repo=None,
    ):
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.assessment_repo = assessment_repo
        # Phase F：route-specific curriculum gap 需要 route_skills / route topics
        self.route_repo = route_repo
        # Step 6：近期市场需求（Daily Summary 优先，individual JD fallback）
        self.market_signal = market_signal
        # 可选：
        # 返回当前 phase 的回调（仅用于 stage_alignment 排序偏好）
        self.current_phase_provider = current_phase_provider
        self._market: dict | None = None
        self._prereq_boost: dict[str, float] = {}
        # 前置“材料已覆盖”：linked_topics 全部有 done 任务的技能（≠ 掌握）
        self._coverage: set[str] | None = None

    # ================= 前置“材料覆盖”（防卡死；≠ 掌握） =================

    def refresh_coverage(self) -> set[str]:
        """重算“材料已覆盖”的技能集合。

        定义：一个技能的 linked_topics 全部存在 done 任务→其材料已被安排并完成。
        用途：前置门禁的**解锁**条件之一（“学过前置才能学下一课”）；
        **绝不等于掌握**：不写 mastery、不影响 active_needed / Review / Assessment /
        已掌握跳过逻辑。前置主题尚未完成时仍为 blocked（硬约束不变）。
        """
        done_topics: set[int] = set()
        if self.plan_repo is not None:
            try:
                done_topics = {
                    int(r[0]) for r in self.plan_repo.conn.execute(
                        "SELECT DISTINCT topic_id FROM tasks "
                        "WHERE status = 'done' AND topic_id IS NOT NULL"
                    ).fetchall()
                }
            except Exception:  # noqa: BLE001
                done_topics = set()
        coverage: set[str] = set()
        for s in self.skill_repo.list_all():
            linked = [int(x) for x in (s.get("linked_topics") or [])]
            if linked and all(t in done_topics for t in linked):
                coverage.add(s["name"])
        self._coverage = coverage
        return coverage

    def _coverage_set(self) -> set[str]:
        if self._coverage is None:
            return self.refresh_coverage()
        return self._coverage

    # ================= Step 6：近期市场需求 =================

    def refresh_market(
        self,
        end_date: str | None = None,
        target_type: str = DEFAULT_MARKET_TARGET,
    ) -> dict | None:
        """重新计算近期市场信号（含前置需求传播）。

        无 market_signal 注入时置空 → market_factor 回退 individual JD。
        """
        if self.market_signal is None:
            self._market = None
            self._prereq_boost = {}
            return None
        if end_date is None:
            from ..utils.date_utils import today as _today

            end_date = _today()
        market = self.market_signal.compute(end_date, target_type)
        self._market = market
        self._prereq_boost = self._compute_prereq_boost(market)
        self.refresh_coverage()
        return market

    def market(self) -> dict | None:
        return self._market

    @staticmethod
    def _market_signal_value(market: dict | None, name: str) -> float:
        if not market or market.get("source") != "daily_summary":
            return 0.0
        rec = (market.get("skills") or {}).get(name)
        return float(rec.get("signal") or 0.0) if rec else 0.0

    def _compute_prereq_boost(self, market: dict | None) -> dict[str, float]:
        """高频但被阻塞的技能 → 向其前置链传递有限需求（确定性、有限深度）。

        - 只从“有市场信号且当前被阻塞”的技能出发；
        - 直接前置得 signal*0.5，再上一层 *0.5（最多两层）；
        - 同一技能取最大值（不累加）并 capped，绝不无限扩散。
        """
        boost: dict[str, float] = {}
        if not market or market.get("source") != "daily_summary":
            return boost
        skills = {s["name"]: s for s in self.skill_repo.list_all()}
        status_by_name = self._default_status_by_name()
        mastery_by_name = self._default_mastery_by_name()
        for name, skill in skills.items():
            sig = self._market_signal_value(market, name)
            if sig <= 0:
                continue
            if not self.is_blocked(skill, status_by_name, mastery_by_name):
                continue
            layer = [
                (p, sig * PREREQ_PROP_DECAY)
                for p in (skill.get("prerequisites") or [])
            ]
            for depth in range(PREREQ_PROP_MAX_DEPTH):
                nxt: list[tuple[str, float]] = []
                for pname, val in layer:
                    capped = min(val, PREREQ_PROP_CAP)
                    if capped > boost.get(pname, 0.0):
                        boost[pname] = capped
                    if depth + 1 < PREREQ_PROP_MAX_DEPTH:
                        ps = skills.get(pname)
                        if ps:
                            for pp in (ps.get("prerequisites") or []):
                                nxt.append((pp, val * PREREQ_PROP_DECAY))
                layer = nxt
        return boost

    def market_factor(self, skill: dict) -> float:
        """近期市场需求因子（0~1）。

        - 有 Daily Summary：market_signal = 近 30 天频率 + 有限的前置需求传播；
        - 无 Daily Summary：回退到旧 individual JD 的 jd_factor。
        """
        market = self._market
        if market and market.get("source") == "daily_summary":
            base = self._market_signal_value(market, skill.get("name"))
            return _clamp(base + self._prereq_boost.get(skill.get("name"), 0.0))
        return self.jd_factor(skill.get("jd_frequency"))

    def curriculum_gap_skills(
        self,
        route_id: int | None = None,
        min_frequency: float = CURRICULUM_GAP_MIN_FREQUENCY,
    ) -> list[dict]:
        """市场上高频、但课程体系还未覆盖的技能（课程缺口）。

        - route_id=None（旧行为）：只要该技能全局没有任何 linked topic 就算缺口；
        - route_id 指定（Phase F）：只看该 route 已绑定的 route_skills，且要求
          该技能在本 route 的 topics 中没有覆盖（不能用全局 linked_topics 判断，
          否则“在别的路线有 topic”会错误地掩盖本路线缺口）；
        - 只用于 UI 展示“课程缺口”，Planner 不得凭空生成课程/任务。
        """
        market = self._market or {}
        if market.get("source") != "daily_summary":
            return []
        status_by_name = self._default_status_by_name()
        mastery_by_name = self._default_mastery_by_name()
        route_topic_ids: set[int] = set()
        if route_id is not None:
            if self.route_repo is not None:
                try:
                    skill_ids = self.route_repo.list_skill_ids(int(route_id))
                except Exception:  # noqa: BLE001
                    skill_ids = []
                skills = []
                for sid in skill_ids:
                    s = self.skill_repo.get(sid)
                    if s is not None:
                        skills.append(s)
            else:
                skills = []
            if self.plan_repo is not None:
                try:
                    route_topic_ids = {
                        t.id for t in self.plan_repo.list_topics_by_route(
                            int(route_id)
                        )
                    }
                except Exception:  # noqa: BLE001
                    route_topic_ids = set()
        else:
            skills = self.skill_repo.list_all()
        gaps: list[dict] = []
        for skill in skills:
            name = skill["name"]
            rec = (market.get("skills") or {}).get(name)
            if not rec:
                continue
            freq = float(rec.get("freq30") or 0.0)
            if freq < min_frequency:
                continue
            if self.effective_status(
                skill, status_by_name, mastery_by_name
            ) == "mastered":
                continue
            try:
                linked = set(self.topics_for_skill(name) or [])
            except Exception:  # noqa: BLE001
                linked = set()
            if route_id is not None:
                if linked & route_topic_ids:
                    continue  # 本 route 已覆盖
            elif linked:
                continue  # 全局已有 topic
            gaps.append({
                "skill": name,
                "frequency_30d": round(freq, 4),
                "mention_30d": int(rec.get("mention_30d") or 0),
                "route_id": route_id,
            })
        gaps.sort(key=lambda g: (-g["frequency_30d"], g["skill"]))
        return gaps

    @staticmethod
    def _stage_rank_from_label(label: str) -> int:
        return int(STAGE_RANK.get(label, 0))

    # ================= 确定性计算 =================

    @staticmethod
    def tier_weight(tier: str) -> float:
        tier = (tier or "").strip().upper()
        return float(TIER_WEIGHT.get(tier, 1.0))

    @staticmethod
    def jd_factor(jd_frequency: dict | None) -> float:
        """由 JD 频率得到 0~1 因子；无 JD 证据时中性回退 0.0。"""
        jd = jd_frequency or {}
        total = int(jd.get("total_jds") or 0)
        must = int(jd.get("must") or 0)
        plus = int(jd.get("plus") or 0)
        if total <= 0:
            return JD_FALLBACK_FACTOR
        must_norm = _clamp(must / total)
        plus_norm = _clamp(plus / total)
        return _clamp(0.8 * must_norm + 0.4 * plus_norm)

    @staticmethod
    def active_needed(
        status: str,
        mastery_estimate: float | None = None,
        prerequisite_satisfied: bool = True,
    ) -> float:
        """依据 status + 现掌握证据（可缺省）计算“当下需要学习”强度。

        规则：
        - not_started + 前置满足 → 高；learning → 中高；
          mastered / deferred → 低（不重复、不让位）
        - 有 mastery 证据：>=0.85 高掌握 → 降权避免重复；
          <=0.4 明确薄弱 → 升权（巩固）
        - mastery 缺失（None）→ 不推断“不会”，保持 status 基准
        - 前置未满足 → 压低（配合门禁，不提升为当下学习内容）
        """
        base = float(STATUS_ACTIVE_BASE.get(status, 0.5))
        if mastery_estimate is not None:
            m = float(mastery_estimate)
            if m >= MASTERY_HIGH:
                base = min(base, 0.15)
            elif m <= MASTERY_LOW:
                base = max(base, 0.9)
        if not prerequisite_satisfied:
            base = min(base, 0.3)
        return _clamp(base)

    @classmethod
    def connector_value(cls, name: str, shared_connector: bool = False) -> float:
        """共享能力 bonus（0 或 1；权重 0.10 负责“有限”）。"""
        if shared_connector:
            return 1.0
        n = (name or "").strip().lower()
        return 1.0 if any(
            n == c.lower() for c in CONNECTOR_DEFAULT
        ) else 0.0

    # ---------- 全量重算 ----------

    def recompute_all_priority_scores(self, end_date: str | None = None) -> list[dict]:
        """遍历 skills 重算 priority_score 并落库；返回排序明细。

        返回值按 score 降序：每个元素包含 component 明细与门禁信息，
        便于展示/测试“为什么是这个分数”。
        """
        self.refresh_market(end_date)
        self.refresh_coverage()
        skills = self.skill_repo.list_all()
        status_by_name = self._default_status_by_name()
        mastery_by_name = self._default_mastery_by_name()

        out = []
        for skill in skills:
            detail = self.compute_skill_score(
                skill, mastery_by_name, status_by_name
            )
            self.skill_repo.update(
                skill["id"], priority_score=detail["score"]
            )
            out.append(detail)

        out.sort(key=lambda d: (-d["score"], -self.tier_weight(d["tier"]),
                                d["name"]))
        return out

    def compute_skill_score(
        self,
        skill: dict,
        mastery_by_name: dict[str, float | None] | None = None,
        status_by_name: dict[str, str] | None = None,
    ) -> dict:
        """计算单个技能的分数明细（不写库）。

        未传入 mastery/status 映射时，自动去仓库读取（保证方法自洽）。
        """
        mastery_by_name = (
            mastery_by_name if mastery_by_name is not None
            else self._default_mastery_by_name()
        )
        status_by_name = (
            status_by_name if status_by_name is not None
            else self._default_status_by_name()
        )

        tier = skill["tier"]
        tier_v = self.tier_weight(tier)
        jd_v = self.market_factor(skill)
        conn_v = self.connector_value(
            skill["name"], bool(skill.get("shared_connector"))
        )

        missing = self.missing_prerequisites(
            skill, status_by_name, mastery_by_name
        )
        blocked = bool(missing)
        prereq_ok = not blocked
        mastery = mastery_by_name.get(skill["name"])

        # 前置未满足：不提升为当前学习内容（门禁 + 压低 active）
        active_v = self.active_needed(
            skill["status"], mastery, prerequisite_satisfied=prereq_ok
        )

        score = (
            W_TIER * tier_v
            + W_JD * jd_v
            + W_ACTIVE * active_v
            + W_CONNECTOR * conn_v
        )
        return {
            "name": skill["name"],
            "tier": tier,
            "status": skill["status"],
            "score": round(score, 6),
            "tier_term": round(W_TIER * tier_v, 6),
            "jd_term": round(W_JD * jd_v, 6),
            "market_term": round(W_JD * jd_v, 6),
            "market_factor": round(jd_v, 6),
            "active_term": round(W_ACTIVE * active_v, 6),
            "connector_term": round(W_CONNECTOR * conn_v, 6),
            "jd_frequency": skill.get("jd_frequency", {}),
            "mastery_estimate": mastery,
            "gate": "blocked" if blocked else "ok",
            "missing_prerequisites": missing,
        }

    # ---------- 前置依赖门禁 ----------

    def _default_status_by_name(self) -> dict[str, str]:
        """从仓库读取全量技能状态（门禁用）。"""
        return {s["name"]: s["status"] for s in self.skill_repo.list_all()}

    def _default_mastery_by_name(self) -> dict[str, float | None]:
        return self._mastery_by_skill_name(self.skill_repo.list_all())

    def missing_prerequisites(
        self,
        skill: dict,
        status_by_name: dict[str, str] | None = None,
        mastery_by_name: dict[str, float | None] | None = None,
    ) -> list[str]:
        """返回未达标的前置技能（满足 = 状态 mastered 或有高掌握证据）。"""
        prereqs = skill.get("prerequisites") or []
        if not prereqs:
            return []
        status_by_name = (
            status_by_name if status_by_name is not None
            else self._default_status_by_name()
        )
        mastery_by_name = (
            mastery_by_name if mastery_by_name is not None
            else self._default_mastery_by_name()
        )
        missing = []
        coverage = self._coverage_set()
        for name in prereqs:
            if name in coverage:
                # 前置材料已完成（≠ 掌握）：允许继续学下一课，不写 mastery
                continue
            st = status_by_name.get(name)
            if st == "mastered":
                continue
            m = mastery_by_name.get(name)
            if m is not None and float(m) >= MASTERY_HIGH:
                continue
            missing.append(name)
        return missing

    def is_blocked(
        self,
        skill: dict,
        status_by_name: dict[str, str] | None = None,
        mastery_by_name: dict[str, float | None] | None = None,
    ) -> bool:
        return bool(self.missing_prerequisites(
            skill, status_by_name, mastery_by_name
        ))

    def effective_status(
        self,
        skill: dict,
        status_by_name: dict[str, str] | None = None,
        mastery_by_name: dict[str, float | None] | None = None,
    ) -> str:
        """门禁生效后的展示状态：被阻塞技能视为 deferred。

        不写库（不覆盖用户维护的 status），只影响候选选择。
        """
        if self.is_blocked(skill, status_by_name, mastery_by_name):
            return "deferred"
        return skill["status"]

    def select_active_candidates(
        self,
        statuses: tuple[str, ...] = ("not_started", "learning"),
        limit: int | None = None,
        current_phase=None,
    ) -> list[dict]:
        """返回“门禁放行 + 状态可学习”的技能，按 score 降序。

        这是 Phase A 的确定性候选视图，不写 planner、不改每日任务。
        被阻塞（前置未满足）技能被排除（视为 deferred）。

        Step 6：加入 current-stage alignment 作为**排序偏好**（不改 score）：
        当前阶段直接相关 > 下一阶段较近 > 很远/未知。这样“gate ok +
        A 级”很远期技能（如 SQL）不会仅因分数就排到最近重点前三。
        """
        status_by_name = self._default_status_by_name()
        mastery_by_name = self._default_mastery_by_name()
        self.refresh_coverage()
        candidates = []
        for skill in self.skill_repo.list_all():
            eff = self.effective_status(skill, status_by_name, mastery_by_name)
            if eff not in statuses:
                continue
            detail = self.compute_skill_score(
                skill, mastery_by_name, status_by_name
            )
            label, _ = self.stage_alignment(skill["name"], current_phase)
            detail["stage_alignment"] = label
            candidates.append(detail)
        candidates.sort(key=lambda d: (
            -self._stage_rank_from_label(d.get("stage_alignment", "unknown")),
            -d["score"],
            d["name"],
        ))
        if limit is not None:
            candidates = candidates[: max(0, limit)]
        return candidates

    # ---------- current-stage alignment（仅排序偏好，不改 score） ----------

    def _resolve_current_phase(self, current_phase=None):
        if current_phase is not None:
            return current_phase
        if self.current_phase_provider is not None:
            try:
                return self.current_phase_provider()
            except Exception:  # noqa: BLE001
                return None
        return None

    def stage_alignment(self, skill_name: str, current_phase=None):
        """返回 (label, factor)。

        label ∈ {current, next, far, unknown}；factor 仅供展示。
        current = 当前 phase 有对应 topic；next = 下一阶段；其它 = far/unknown。
        """
        if self.plan_repo is None:
            return "unknown", 0.0
        try:
            linked = set(self.topics_for_skill(skill_name) or [])
        except Exception:  # noqa: BLE001
            return "unknown", 0.0
        if not linked:
            return "unknown", 0.0
        phase = self._resolve_current_phase(current_phase)
        if phase is None:
            return "unknown", 0.0
        phase_topics = list(getattr(phase, "topics", None) or [])
        if not phase_topics:
            # 传入的 phase 可能未展开 topics；主动补全（保证确定性）
            try:
                phase_topics = self.plan_repo.list_topics(phase.id)
            except Exception:  # noqa: BLE001
                phase_topics = []
        if linked & {t.id for t in phase_topics}:
            return "current", 1.0
        # 下一阶段：按 start_date 排序中紧邻 current 的那一个
        try:
            plan = self.plan_repo.get_active_plan()
            phases = self.plan_repo.list_phases(plan.id) if plan else []
        except Exception:  # noqa: BLE001
            phases = []
        after = [p for p in phases if p.start_date > (phase.end_date or "")]
        if after:
            nxt = min(after, key=lambda p: p.start_date)
            nxt_topics = list(getattr(nxt, "topics", None) or [])
            if not nxt_topics:
                try:
                    nxt_topics = self.plan_repo.list_topics(nxt.id)
                except Exception:  # noqa: BLE001
                    nxt_topics = []
            if linked & {t.id for t in nxt_topics}:
                return "next", 0.6
        return "far", 0.25

    def explain_skill(
        self, skill: dict, mastery_by_name=None, status_by_name=None,
        current_phase=None,
    ) -> dict:
        """可解释的技能优先级（含近期市场 / 前置传播 / 阶段适配）。"""
        detail = self.compute_skill_score(
            skill, mastery_by_name, status_by_name
        )
        market = self._market or {}
        name = skill["name"]
        rec = None
        if market.get("source") == "daily_summary":
            rec = (market.get("skills") or {}).get(name)
        label, _ = self.stage_alignment(name, current_phase)
        mastery = detail.get("mastery_estimate")
        detail.update({
            "market_source": market.get("source", "none"),
            "market_30d": (rec or {}).get("freq30"),
            "market_signal": self._market_signal_value(market, name),
            "sample_count_30d": market.get("sample_count_30d", 0),
            "prerequisite_demand_boost": round(
                self._prereq_boost.get(name, 0.0), 6
            ),
            "stage_alignment": label,
            "weak": (mastery is not None and float(mastery) <= MASTERY_LOW),
            "blocked": detail.get("gate") == "blocked",
        })
        detail["reasons"] = self._explain_reasons(detail)
        return detail

    @staticmethod
    def _explain_reasons(detail: dict) -> list[str]:
        reasons = [f"{detail['tier']}级"]
        if detail.get("market_source") == "daily_summary":
            m30 = detail.get("market_30d")
            if m30 is not None:
                reasons.append(f"近30天目标岗位需求 {m30 * 100:.0f}%")
        elif detail.get("market_factor"):
            reasons.append("使用历史单条 JD 需求")
        if detail.get("prerequisite_demand_boost"):
            reasons.append("为高频下游技能补前置")
        if detail.get("stage_alignment") == "current":
            reasons.append("当前阶段直接相关")
        elif detail.get("stage_alignment") == "far":
            reasons.append("属于较后阶段")
        if detail.get("weak"):
            reasons.append("存在薄弱验收证据")
        if detail.get("blocked"):
            reasons.append("前置未满足")
        if detail.get("status") == "mastered":
            reasons.append("已掌握（不重复基础）")
        return reasons

    # ---------- mastery 证据 ----------

    def _mastery_for_skill(self, skill: dict) -> float | None:
        """单个技能的掌握证据（仅来自 knowledge_points，缺省 None）。"""
        if self.assessment_repo is None:
            return None
        ref = (skill.get("mastery_ref") or "").strip()
        if not ref.startswith("kp:"):
            return None
        try:
            kp = self.assessment_repo.get_knowledge_point(int(ref[3:]))
        except (TypeError, ValueError):
            return None
        if kp is None or kp.get("mastery_estimate") is None:
            return None
        return float(kp["mastery_estimate"])

    def _mastery_by_skill_name(
        self, skills: list[dict]
    ) -> dict[str, float | None]:
        """按技能名字读到掌握证据（仅来自 assessment_repo，缺省 None）。"""
        return {s["name"]: self._mastery_for_skill(s) for s in skills}

    # ---------- 与 study_topics 的映射 ----------

    def _all_topics_by_name(self) -> dict[str, int]:
        """从 plan_repo 收集全部主题名 -> id（跨所有计划/阶段）。"""
        if self.plan_repo is None:
            return {}
        out: dict[str, int] = {}
        for plan in self.plan_repo.list_plans():
            full = self.plan_repo.get_plan_with_phases(plan.id)
            if full is None:
                continue
            for ph in full.phases:
                for t in ph.topics:
                    out.setdefault(t.name, t.id)
        return out

    def link_topic_by_name(self, skill_name: str, topic_name: str) -> bool:
        """把“主题名”解析成 topic_id 并追加到技能映射；找不到返回 False。

        显式、确定的映射：不依赖“名字碰巧一样”的隐式匹配之外的任何猜测。
        """
        mapping = self._all_topics_by_name()
        topic_id = mapping.get(topic_name)
        skill = self.skill_repo.get_by_name(skill_name)
        if topic_id is None or skill is None:
            return False
        current = list(skill.get("linked_topics") or [])
        if topic_id not in current:
            current.append(int(topic_id))
            self.skill_repo.update(skill["id"], linked_topics=current)
        return True

    def link_all_from_map(self, skill_to_topic_names: dict) -> dict[str, bool]:
        """批量建立 skill → topic 映射；返回 {技能名: 是否全部关联成功}。

        :param skill_to_topic_names: {skill_name: [topic_name, ...]}
        """
        result = {}
        for skill_name, topic_names in (skill_to_topic_names or {}).items():
            all_ok = True
            for tn in topic_names:
                all_ok = self.link_topic_by_name(skill_name, tn) and all_ok
            result[skill_name] = all_ok
        return result

    def topics_for_skill(self, skill_name: str) -> list[int]:
        skill = self.skill_repo.get_by_name(skill_name)
        return list(skill.get("linked_topics") or []) if skill else []

    def skills_for_topic(self, topic_id: int) -> list[str]:
        """反向：某主题被哪些技能服务（一个 topic 可服务多个 skill）。"""
        out = []
        for skill in self.skill_repo.list_all():
            if int(topic_id) in (skill.get("linked_topics") or []):
                out.append(skill["name"])
        return out

    # ---------- 种子加载（确定性） ----------

    def seed_from_career_context(
        self,
        data: dict | None = None,
        path: str | Path | None = None,
        link_topics: bool = True,
    ) -> list[str]:
        """从 career_context.json 的 skill_pool / skill_dependencies / 状态
        幂等写入 skills，必要时解析 study_topic 映射。

        幂等：重复调用不重复建、不覆盖 status / mastery_ref / jd_frequency /
        priority_score。
        :return: 写入/更新的技能名列表
        """
        data = data or self._load_career_context(path)
        if not data:
            return []
        pool = data.get("skill_pool") or {}
        dependencies = data.get("skill_dependencies") or {}
        connectors = set(
            str(x) for x in (data.get("connectors") or {}).get("shared", [])
        )
        state = data.get("current_skill_state") or {}

        # 状态初值映射（仅新建时使用）
        def _initial_status(name: str) -> str:
            if name in (state.get("mastered") or []):
                return "mastered"
            if name in (state.get("deferred") or []):
                return "deferred"
            if name in (state.get("learning") or []):
                return "learning"
            if name in (state.get("weak") or []):
                return "not_started"  # 未开始/需要学：高激活度
            return "not_started"

        seeded: list[str] = []
        for tier_key in ("S", "A", "B", "C"):
            names = pool.get(tier_key) or []
            for name in names:
                name = (name or "").strip()
                if not name:
                    continue
                prereqs = [str(x) for x in (dependencies.get(name) or [])]
                connector = (name.lower() in {c.lower() for c in connectors})
                category = "connector" if connector else "core"
                self.skill_repo.upsert_by_name(
                    name=name,
                    tier=tier_key,
                    category=category,
                    prerequisites=prereqs,
                    shared_connector=connector,
                    status=_initial_status(name),
                )
                seeded.append(name)

        if self.plan_repo is not None:
            self._seed_links_from_keywords(seeded)
        return seeded

    def _seed_links_from_keywords(self, skill_names: list[str]) -> None:
        """用 skill→关键词 规则建立 topic 映射（只链到已存在的主题）。

        只链“同阶段可直达”的技能；阶段内后置主题（如 Tokenizer / HF
        Transformers）故意不链到依赖它的技能（LLM 基础 / Hugging Face），
        否则会被前置门禁当成“未达标”而一直不生成，导致当前阶段卡死。
        """
        keywords = {
            "Python": ["Python 语法与基础练习", "Python 面向对象与常用库"],
            "Linux": ["Linux 常用命令与工具链"],
            "Git": ["Git 版本控制与协作流程"],
            "PyTorch": [
                "PyTorch 张量与自动求导（Tensor / autograd）",
                "Dataset 与 DataLoader",
                "nn.Module 与模型搭建",
                "最小线性回归训练闭环（y=2x+1）",
            ],
            "Transformer": ["Transformer：Attention / MHA / FFN"],
            "Embedding": ["Embedding 与向量检索"],
            "RAG": ["RAG 全流程搭建"],
            "Ranking": [
                "Reranker 重排序",
                "RRF 排序融合",
                "Ranking 基础",
            ],
            "Agent": ["Agent 实现与多步编排"],
            "模型评估": ["Evaluation / Badcase / LLM-as-Judge"],
            # ---- 补齐：已有真实 topic、语义明确，但之前漏链的技能 ----
            # 这些 topic 以前故意不链（旧门禁只认 mastery 会卡死）；
            # 现在门禁已支持“材料已覆盖”（done）解锁，链上是安全且正确的。
            "LLM 基础": [
                "Tokenizer 与分词",
                "RoPE 位置编码",
                "KV Cache",
                "generate / sampling 解码策略",
                "Qwen / LLaMA 架构：GQA / SwiGLU",
            ],
            "Hugging Face": ["Hugging Face Transformers"],
            "SFT": ["SFT 指令微调"],
            "LoRA / QLoRA": ["LoRA / QLoRA"],
            "Docker": ["Docker"],
            "vLLM": ["vLLM 与 PagedAttention"],
            "VLM": ["VLM / 多模态基础"],
            "分布式训练底层": ["DDP / ZeRO / DeepSpeed（先理解）"],
            "CUDA": ["C/C++ / CUDA（方向确定后深入）"],
            "复杂推理优化": ["推理优化基础"],
            # ---- 阶段四：推荐 / 搜索系统基础（搜广推主线） ----
            # 注意：每个 topic 只归属一个“覆盖 owner”技能，避免 shared topic
            # 造成 推荐系统基础 -> Recall -> Ranking -> CTR 的循环死锁。
            "SQL": ["SQL 数据分析基础"],
            "推荐系统基础": ["推荐系统整体架构", "协同过滤基础"],
            "Recall": [
                "Embedding Recall / 向量召回",
                "双塔召回 Two-Tower",
                "多路召回与 Candidate Generation",
            ],
            "CTR": ["CTR 预估基础", "Wide & Deep / DeepFM 基础"],
            "Rerank": ["Rerank / 重排基础"],
            "用户画像": ["用户画像与特征工程"],
            "LLM + Recommendation": ["LLM + Recommendation 基础"],
        }
        for skill_name in skill_names:
            for topic_name in keywords.get(skill_name, []):
                self.link_topic_by_name(skill_name, topic_name)

    def sync_skills_from_career_context(
        self, data: dict | None = None, path: str | Path | None = None
    ) -> dict:
        """幂等同步 career_context.skill_pool 到 skills（存量库补缺）。

        通用机制（不为单个技能写特例）：career_context.skill_pool 是正式 skill
        定义来源；缺哪个 skill → 补哪个。
        - 已存在 → 只更新非受保护字段（不覆盖 status / mastery_ref /
          jd_frequency / priority_score）；
        - 不删除 DB 中已有 skill；不建 assessment；不改 task。

        :return: {"seeded": [...], "total": int, "added": [...]}
        """
        before = {s["name"] for s in self.skill_repo.list_all()}
        seeded = self.seed_from_career_context(data=data, path=path)
        after = {s["name"] for s in self.skill_repo.list_all()}
        return {
            "seeded": seeded,
            "total": len(after),
            "added": sorted(after - before),
        }

    def sync_skill_topic_links(self) -> dict:
        """幂等补齐全部 skill→topic 映射（存量库修复用）。

        - 缺失 link → 补；已有 link → 跳过；**不删除任何历史 link**；
        - 不重复 insert；不改 skill status；不改 mastery；不建 assessment；不改 task。
        每次启动可安全调用。

        :return: {"added": {skill: [新增 topic_id...]}, "added_count": int}
        """
        before = {
            s["name"]: set(s.get("linked_topics") or [])
            for s in self.skill_repo.list_all()
        }
        if not before:
            return {"added": {}, "added_count": 0}
        self._seed_links_from_keywords(list(before))
        added: dict[str, list[int]] = {}
        for s in self.skill_repo.list_all():
            new_ids = set(s.get("linked_topics") or []) - before.get(s["name"], set())
            if new_ids:
                added[s["name"]] = sorted(int(x) for x in new_ids)
        return {"added": added,
                "added_count": sum(len(v) for v in added.values())}

    # ---------- 工具 ----------

    @staticmethod
    def _load_career_context(path: str | Path | None) -> dict | None:
        p = Path(path) if path is not None else DEFAULT_CAREER_CONTEXT_PATH
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
