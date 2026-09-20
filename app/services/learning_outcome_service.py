"""学习成果沉淀 + 简历素材（Phase D）。

数据流：
  Task 完成 / Assessment 验收 / 用户手动登记
      ↓
  learning_outcomes（复用 v6 表，v8 增加 task_id / source_attempt_id 幂等来源）
      ↓
  Resume Material（基于真实事实，AI 只做语言组织，绝不新增事实）

关键原则：
- kind 区分：topic / note = 学习证据；experiment / project = 项目成果候选；
  assessment 掌握度 ≠ 项目成果。
- 自动生成只能来自真实数据：task.title/description/topic_id/kp、验收结果、
  weak_points、skill、真实 Git commit、用户填写。
- dataset / metrics / github_url 无真实数据一律为空，绝不编造。
- assessment ok / poor 不生成“已掌握成果”。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..ai.prompt_registry import PromptRegistry
from ..ai.prompts import (
    build_resume_material_vars,
    render_prompt,
)
from ..database.skill_repository import LearningOutcomeRepository

# 允许的 kind
KIND_LEARNING = ("topic", "note")       # 学习证据
KIND_PROJECT = ("experiment", "project")  # 实践/项目成果候选
ALLOWED_KINDS = KIND_LEARNING + KIND_PROJECT

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_FABRICATED_MARKER = re.compile(
    r"(提升|增加|提高|降低|优化了|达到了|准确率|召回率|收益|提速)", re.IGNORECASE
)
_NUMERIC = re.compile(r"\d+(?:\.\d+)?\s*(?:%|倍|ms|s|分|次|条|个|点)")
_URL = re.compile(r"https?://\S+")


def _parse_weak_points(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _token_of(title: str) -> list[str]:
    """从标题粗抽出可作为简历关键词的英文/代码 token。"""
    out = []
    for w in re.split(r"[^A-Za-z0-9_+#./-]+", title or ""):
        w = w.strip()
        if len(w) >= 2 and w not in out:
            out.append(w)
    return out


class LearningOutcomeService:
    """学习成果的创建 / 幂等 / 简历素材。"""

    def __init__(
        self,
        outcome_repo: LearningOutcomeRepository,
        project_root: str | Path | None = None,
        ai_client=None,
        prompt_registry: PromptRegistry | None = None,
        capability_service=None,
    ):
        self.outcome_repo = outcome_repo
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        # 可选：简历素材的 AI 语言组织；None / 失败 / 非法输出都走确定性模板
        self.ai_client = ai_client
        # 可选注入：生产环境传入 DB 支持的 PromptRegistry（支持用户覆盖）
        self.prompt_registry = prompt_registry
        # Phase 3：experiment outcome 保存后提取 EXPERIMENT evidence
        self.capability_service = capability_service

    # ================= Git 证据 =================

    def current_git_commit(self, root: str | Path | None = None) -> str | None:
        """读取仓库当前 HEAD commit（只作“当时版本证据”，不推断功能）。"""
        cwd = Path(root) if root is not None else self.project_root
        try:
            out = subprocess.run(
                ["git", "-C", str(cwd), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        if out.returncode != 0:
            return None
        commit = (out.stdout or "").strip()
        return commit if commit else None

    # ================= 创建 =================

    def create_outcome(
        self,
        *,
        date: str = "",
        kind: str = "note",
        title: str,
        content: str = "",
        tech_stack: list[str] | None = None,
        dataset: str = "",
        metrics: dict | None = None,
        git_commit: str | None = None,
        github_url: str = "",
        resume_keywords: list[str] | None = None,
        linked_kp_id: int | None = None,
        linked_topic_id: int | None = None,
        task_id: int | None = None,
        source_attempt_id: int | None = None,
        derive_git: bool = True,
    ) -> dict:
        """基础创建（带校验）；有伸手数据不编造。"""
        kind = (kind or "note").strip()
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"非法 kind: {kind!r}，只能是 {ALLOWED_KINDS}")
        title = (title or "").strip()
        if not title:
            raise ValueError("成果标题不能为空")
        if metrics is not None and not isinstance(metrics, dict):
            raise ValueError("metrics 必须是 dict")
        tech_stack = [
            str(x) for x in (tech_stack or []) if str(x).strip()
        ]
        resume_keywords = [
            str(x) for x in (resume_keywords or []) if str(x).strip()
        ]
        git_commit = (
            str(git_commit).strip()
            if git_commit is not None else
            (self.current_git_commit() if derive_git else "")
        )
        outcome = self.outcome_repo.create(
            date=date,
            kind=kind,
            title=title,
            content=(content or "").strip(),
            tech_stack=tech_stack,
            dataset=(dataset or "").strip(),
            metrics=metrics or {},
            git_commit=git_commit or "",
            github_url=(github_url or "").strip(),
            resume_keywords=resume_keywords,
            linked_kp_id=linked_kp_id,
            linked_topic_id=linked_topic_id,
            task_id=task_id,
            source_attempt_id=source_attempt_id,
        )
        # Phase 3：experiment 成果满足严格证据规则时提取 EXPERIMENT
        if (
            self.capability_service is not None
            and outcome is not None
            and outcome.get("kind") == "experiment"
        ):
            try:
                self.capability_service.sync_from_experiment_outcome(
                    outcome["id"]
                )
            except Exception:  # noqa: BLE001 - evidence 失败不影响成果保存
                pass
        return outcome

    def create_manual_outcome(
        self,
        *,
        date: str = "",
        kind: str = "note",
        title: str,
        content: str = "",
        tech_stack: list[str] | None = None,
        dataset: str = "",
        metrics: dict | None = None,
        git_commit: str | None = None,
        github_url: str = "",
        resume_keywords: list[str] | None = None,
        linked_kp_id: int | None = None,
        linked_topic_id: int | None = None,
        task_id: int | None = None,
        derive_git: bool = True,
    ) -> dict:
        """用户主动登记（字段为用户提供，做基本校验，不补齐）。

        experiment 类型**不做 git 自动推导**：实验产物必须是用户显式提供的
        真实产物（metrics/git/url/dataset），不能拿 Study Agent 仓库 commit 充数。
        """
        if kind == "experiment":
            derive_git = False
        return self.create_outcome(
            date=date, kind=kind, title=title, content=content,
            tech_stack=tech_stack, dataset=dataset, metrics=metrics,
            git_commit=git_commit, github_url=github_url,
            resume_keywords=resume_keywords, linked_kp_id=linked_kp_id,
            linked_topic_id=linked_topic_id, task_id=task_id,
            derive_git=derive_git,
        )

    # ================= Task 完成 Hook =================

    def generate_from_task(
        self, task, date: str = "", force_update: bool = True
    ) -> dict | None:
        """任务完成后沉淀学习成果（幂等：同 task 只保留一条，可更新）。"""
        if task is None or getattr(task, "id", None) is None:
            return None
        existing = self.outcome_repo.get_by_task_id(task.id)
        git = self.current_git_commit()
        if existing is not None:
            if force_update:
                self.outcome_repo.update(
                    existing["id"],
                    title=task.title,
                    content=task.description or "",
                    git_commit=git or existing.get("git_commit", ""),
                )
            return self.outcome_repo.get(existing["id"])
        return self.create_outcome(
            date=date or task.scheduled_date or "",
            kind="topic",  # 学习证据，不自动升级为“项目成果”
            title=task.title,
            content=task.description or "",
            tech_stack=[],
            resume_keywords=_token_of(task.title),
            linked_topic_id=task.topic_id,
            linked_kp_id=task.knowledge_point_id,
            task_id=task.id,
            git_commit=git or None,
        )

    # ================= Assessment Hook =================

    def generate_from_assessment(
        self,
        attempt: dict,
        kp: dict | None = None,
        date: str = "",
    ) -> dict | None:
        """验收后沉淀掌握证据（good/excellent 学习成果，ok 记录掌握，poor 薄弱）。"""
        if not attempt or not attempt.get("id"):
            return None
        level = attempt.get("result_level")
        if level not in ("good", "excellent", "ok", "poor"):
            return None  # pending / failed 不生成
        mastery = attempt.get("mastery_estimate")
        weak = _parse_weak_points(attempt.get("weak_points_json"))
        kp_id = attempt.get("knowledge_point_id")
        kp_name = (kp or {}).get("name") or f"知识点#{kp_id}"
        existing = self.outcome_repo.get_by_source_attempt_id(attempt["id"])

        if level in ("good", "excellent"):
            kind, title_tpl = "topic", f"{kp_name} 验收达成"
            content = (
                f"客观验收结果 {level}；mastery 估计 {mastery:.2f}"
                if mastery is not None else f"客观验收结果 {level}"
            )
        elif level == "ok":
            kind, title_tpl = "note", f"{kp_name} 掌握情况"
            content = (
                f"验收结果 ok（掌握情况记录）；mastery 估计 {mastery:.2f}"
                if mastery is not None else "验收结果 ok（掌握情况记录）"
            )
        else:  # poor
            kind, title_tpl = "note", f"{kp_name} 薄弱记录"
            weak_txt = "、".join(weak) if weak else "待后续巩固"
            content = f"验收结果 poor，薄弱点：{weak_txt}"

        metrics = {"mastery_estimate": round(float(mastery), 4)} \
            if mastery is not None else {}
        if level == "poor":
            metrics["result_level"] = "poor"
        for tag in weak:
            metrics.setdefault("weak_points", [])
            metrics["weak_points"].append(tag)

        if existing is not None:
            self.outcome_repo.update(
                existing["id"],
                title=title_tpl,
                content=content,
                metrics=metrics,
                linked_kp_id=kp_id or existing.get("linked_kp_id"),
            )
            return self.outcome_repo.get(existing["id"])

        return self.create_outcome(
            date=date,
            kind=kind,
            title=title_tpl,
            content=content,
            metrics=metrics,
            linked_kp_id=kp_id,
            linked_topic_id=(kp or {}).get("topic_id"),
            source_attempt_id=attempt["id"],
        )

    # ================= 查询 =================

    def list_by_date(self, date: str) -> list[dict]:
        return self.outcome_repo.list_all(date=date)

    def list_recent(self, limit: int = 10) -> list[dict]:
        return self.outcome_repo.list_all()[: max(0, limit)]

    # ================= 简历素材 =================

    def build_resume_material(
        self,
        outcomes: list[dict] | None = None,
        ai_client=None,
    ) -> dict:
        """基于真实 outcomes 生成简历素材（关键词 / bullets / 技术总结）。

        AI 只允许“语言组织 + 压缩”，且必须通过事实白名单过滤；
        AI 不存在/失败/试图新增事实 → 使用确定性模板。
        """
        outcomes = (
            [o for o in outcomes if o]
            if outcomes is not None else
            self.outcome_repo.list_all()
        )
        project_like = [o for o in outcomes
                        if o.get("kind") in KIND_PROJECT]
        learning = [o for o in outcomes
                    if o.get("kind") in KIND_LEARNING]

        deterministic_keywords = self._aggregate_keywords(outcomes)
        deterministic_bullets = self._deterministic_bullets(project_like)
        deterministic_summary = self._deterministic_summary(
            project_like, learning
        )

        client = ai_client or self.ai_client
        if client is not None and client.is_configured() and (outcomes):
            try:
                raw = client.chat(
                    render_prompt(
                        "resume_material.system", {}, self.prompt_registry
                    ),
                    render_prompt(
                        "resume_material.user",
                        build_resume_material_vars(outcomes),
                        self.prompt_registry,
                    ),
                )
                ai = self._validate_ai_resume(raw)
                if ai is not None:
                    src = self._source_text(outcomes)
                    kw = [w for w in ai.get("keywords", [])
                          if self._safe_fact(w, src)]
                    bul = [b for b in ai.get("bullets", [])
                           if self._safe_bullet(b, src)]
                    summary = ai.get("summary", "")
                    if not self._safe_bullet(summary, src):
                        summary = ""
                    return {
                        "resume_keywords": (kw or deterministic_keywords),
                        "candidate_bullets": (bul or deterministic_bullets),
                        "technical_summary": (
                            summary or deterministic_summary
                        ),
                        "source_facts": self._source_fact_fields(outcomes),
                        "ai_augmented": True,
                    }
            except Exception:  # noqa: BLE001 - AI 失败走确定性模板
                pass

        return {
            "resume_keywords": deterministic_keywords,
            "candidate_bullets": deterministic_bullets,
            "technical_summary": deterministic_summary,
            "source_facts": self._source_fact_fields(outcomes),
            "ai_augmented": False,
        }

    # ---------- 简历：确定性 -------------

    @staticmethod
    def _aggregate_keywords(outcomes: list[dict]) -> list[str]:
        out: list[str] = []
        for o in outcomes:
            for kw in (o.get("resume_keywords") or []):
                if str(kw).strip() and str(kw) not in out:
                    out.append(str(kw))
            for kw in (o.get("tech_stack") or []):
                if str(kw).strip() and str(kw) not in out:
                    out.append(str(kw))
        return out

    @classmethod
    def _deterministic_bullets(cls, project_like: list[dict]) -> list[str]:
        bullets = []
        for o in project_like:
            title = (o.get("title") or "").strip() or "未命名成果"
            content = (o.get("content") or "").strip()
            tech = "、".join(o.get("tech_stack") or [])
            seg = f"完成「{title}」"
            if content:
                seg += f"：{content}"
            if tech:
                seg += f"（技术栈：{tech}）"
            metrics = o.get("metrics") or {}
            if metrics:
                seg += "；关键指标：" + "、".join(
                    f"{k}={v}" for k, v in metrics.items()
                )
            if o.get("dataset"):
                seg += f"；数据集：{o['dataset']}"
            if o.get("github_url"):
                seg += f"；代码：{o['github_url']}"
            bullets.append(seg)
        return bullets

    @staticmethod
    def _deterministic_summary(
        project_like: list[dict], learning: list[dict]
    ) -> str:
        tops = [o.get("title") for o in project_like if o.get("title")]
        if not project_like:
            if learning:
                return "当前主要是学习与验证记录；尚无已完成的‘项目/实验’类成果。"
            return "暂无已记录的学习成果。"
        return "已完成{}。".format("、".join(tops[:5]))

    # ---------- 简历：来源事实 -------------

    @classmethod
    def _source_text(cls, outcomes: list[dict]) -> str:
        return " ".join(cls._source_fact_fields(outcomes))

    @staticmethod
    def _source_fact_fields(outcomes: list[dict]) -> "list[str]":
        parts: list[str] = []
        for o in outcomes:
            for key in ("title", "content", "dataset", "github_url"):
                if o.get(key):
                    parts.append(str(o[key]))
            for x in (o.get("tech_stack") or []):
                parts.append(str(x))
            for x in (o.get("resume_keywords") or []):
                parts.append(str(x))
            for k, v in (o.get("metrics") or {}).items():
                parts.append(f"{k}={v}")
                parts.append(str(v))
        return parts

    @staticmethod
    def _safe_fact(text: str, src: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        urls = _URL.findall(text)
        if urls and not all(u in src for u in urls):
            return False
        return True

    @classmethod
    def _safe_bullet(cls, text: str, src: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        urls = _URL.findall(text)
        if urls and not all(u in src for u in urls):
            return False
        for num in _NUMERIC.findall(text):
            if num.strip() not in src:
                return False
        # 禁止“提升/达到准确率”等无源断言
        if _FABRICATED_MARKER.search(text):
            return False
        return True

    @classmethod
    def _validate_ai_resume(cls, content: str) -> dict | None:
        try:
            data = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("keywords", []), list) or \
                not isinstance(data.get("bullets", []), list):
            return None
        if not isinstance(data.get("summary", ""), str):
            return None
        return {
            "keywords": [str(x) for x in data["keywords"]],
            "bullets": [str(x) for x in data["bullets"]],
            "summary": data["summary"].strip(),
        }

