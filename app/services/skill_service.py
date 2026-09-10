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


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class SkillService:
    """技能的确定性优先级计算 / 门禁 / 主题映射 / 种子加载。"""

    def __init__(
        self,
        skill_repo: SkillRepository,
        plan_repo=None,
        assessment_repo=None,
    ):
        self.skill_repo = skill_repo
        self.plan_repo = plan_repo
        self.assessment_repo = assessment_repo

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

    def recompute_all_priority_scores(self) -> list[dict]:
        """遍历 skills 重算 priority_score 并落库；返回排序明细。

        返回值按 score 降序：每个元素包含 component 明细与门禁信息，
        便于展示/测试“为什么是这个分数”。
        """
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
        jd_v = self.jd_factor(skill.get("jd_frequency"))
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
        for name in prereqs:
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
    ) -> list[dict]:
        """返回“门禁放行 + 状态可学习”的技能，按 score 降序。

        这是 Phase A 的确定性候选视图，不写 planner、不改每日任务。
        被阻塞（前置未满足）技能被排除（视为 deferred）。
        """
        status_by_name = self._default_status_by_name()
        mastery_by_name = self._default_mastery_by_name()
        candidates = []
        for skill in self.skill_repo.list_all():
            eff = self.effective_status(skill, status_by_name, mastery_by_name)
            if eff not in statuses:
                continue
            candidates.append(
                self.compute_skill_score(skill, mastery_by_name, status_by_name)
            )
        candidates.sort(key=lambda d: (-d["score"], d["name"]))
        if limit is not None:
            candidates = candidates[: max(0, limit)]
        return candidates

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
            "Ranking": ["Reranker 重排序", "RRF 排序融合"],
            "Agent": ["Agent 实现与多步编排"],
            "模型评估": ["Evaluation / Badcase / LLM-as-Judge"],
        }
        for skill_name in skill_names:
            for topic_name in keywords.get(skill_name, []):
                self.link_topic_by_name(skill_name, topic_name)

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
