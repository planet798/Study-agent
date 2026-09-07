"""课外探索服务（Phase 6）。

职责：
- 从经过人工验证的资源池 docs/exploration_resources.json 中，根据当前学习上下文推荐
  课外内容（GitHub / LeetCode / 官方文档）；
- 推荐不创建 tasks、不推进 study phase/topic、不影响次日 Planner；
- 资源 URL 一律来自资源文件，AI 只允许从已验证资源中筛选/解释，
  任何情况下都不输出资源文件之外的 URL；
- 提供 URL 格式校验、类型校验，以及可选的可达性检查（默认不做网络请求）。

验证策略：
- 加载时：格式/类型非法的资源被直接剔除；
- 推荐时：若注入了 resource_checker，进一步剔除不可访问的资源。
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Callable

from ..ai.interface import AIClient, AIServiceError

# 允许的资源类型
ALLOWED_RESOURCE_TYPES = ("github", "leetcode", "docs")

# 资源文件（单一事实来源，只放已验证的真实 URL）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_FILE = PROJECT_ROOT / "docs" / "exploration_resources.json"

# 匹配权重：薄弱点最高，其次知识点/主题/阶段
_MATCH_WEIGHT = {
    "weak_points": 3.0,
    "knowledge_points": 2.0,
    "topics": 1.0,
    "phase": 0.5,
}


class ExplorationService:
    def __init__(
        self,
        resources_path: str | Path | None = None,
        resource_checker: Callable[[str], bool] | None = None,
    ):
        """初始化并加载已验证资源。

        :param resources_path: 资源 JSON 路径（缺省 docs/exploration_resources.json）
        :param resource_checker: 可选的可达性检查函数 url->bool；
            默认不检查（不发起网络请求，避免离线环境失败）。
        """
        self.resources_path = Path(resources_path) if resources_path else RESOURCE_FILE
        self.resource_checker = resource_checker
        self.load_error: str | None = None
        self.resources = self.load_resources(self.resources_path)

    # ================= 加载与校验 =================

    def load_resources(self, path: Path) -> list[dict]:
        """读取资源文件，校验并剔除非法资源。"""
        self.load_error = None
        if not path.exists():
            self.load_error = f"资源文件不存在: {path}"
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.load_error = f"资源文件解析失败: {e}"
            return []
        raw = data.get("resources", []) if isinstance(data, dict) else []
        valid: list[dict] = []
        for r in raw:
            ok, _ = self.validate_resource(r)
            if ok:
                valid.append(r)
        return valid

    @staticmethod
    def valid_url(url: str) -> bool:
        """严格校验 http/https URL 格式。"""
        if not isinstance(url, str) or not url.strip():
            return False
        try:
            parts = urllib.parse.urlparse(url.strip())
        except ValueError:
            return False
        return (
            parts.scheme in ("http", "https")
            and bool(parts.netloc)
            and "." in parts.netloc
        )

    @classmethod
    def validate_resource(cls, resource) -> tuple[bool, str]:
        """校验单个资源；返回 (是否合法, 错误信息)。"""
        if not isinstance(resource, dict):
            return False, "资源必须是对象"
        rid = resource.get("id") or ""
        if not isinstance(rid, str) or not rid.strip():
            return False, "缺少 id"
        title = resource.get("title") or ""
        if not isinstance(title, str) or not title.strip():
            return False, "缺少 title"
        rtype = resource.get("type") or ""
        if rtype not in ALLOWED_RESOURCE_TYPES:
            return False, f"非法 type: {rtype}"
        url = resource.get("url") or ""
        if not cls.valid_url(url):
            return False, f"非法 URL: {url!r}"
        return True, ""

    # ================= 上下文 =================

    @staticmethod
    def build_context(
        today: str,
        study_plan_service=None,
        assessment_repo=None,
    ) -> dict:
        """从现有服务组装推荐上下文（不改变任何学习状态）。"""
        ctx: dict = {"phase": [], "topics": [], "knowledge_points": [],
                     "weak_points": []}

        if study_plan_service is not None:
            phase = study_plan_service.get_current_phase(today)
            if phase is not None:
                ctx["phase"] = [phase.name]
                ctx["topics"] = [t.name for t in phase.topics]

        if assessment_repo is not None:
            for kp in assessment_repo.list_knowledge_points():
                ctx["knowledge_points"].append(kp["name"])
                if kp.get("last_assessed_at") and (
                    kp.get("mastery_estimate") or 0.0
                ) < 0.5:
                    ctx["weak_points"].append(kp["name"])
        return ctx

    # ================= 推荐 =================

    def _reachable(self, resources: list[dict]) -> list[dict]:
        if self.resource_checker is None:
            return resources
        return [r for r in resources if self.resource_checker(r["url"])]

    @staticmethod
    def _match(term: str, resource: dict) -> bool:
        """当前学习词是否与资源相关（标签/标题做子串匹配，忽略大小写）。"""
        term = term.lower()
        if term in (resource.get("title") or "").lower():
            return True
        for tag in resource.get("tags") or []:
            tag = str(tag).lower()
            if term in tag or tag in term:
                return True
        return False

    def recommend(
        self,
        context: dict | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """基于当前学习上下文推荐已验证资源（规则匹配 + 具体理由）。"""
        ctx = context or {}
        terms: list[tuple[str, str]] = []
        for source in ("phase", "topics", "knowledge_points", "weak_points"):
            for item in ctx.get(source) or []:
                if isinstance(item, str) and item.strip():
                    terms.append((item.strip(), source))

        resources = self._reachable(self.resources)
        scored: list[tuple[float, dict, list[str]]] = []
        for r in resources:
            score = 0.0
            matched: list[str] = []
            for term, source in terms:
                if self._match(term, r):
                    score += _MATCH_WEIGHT.get(source, 1.0)
                    if term not in matched:
                        matched.append(term)
            if score > 0 and matched:
                scored.append((score, r, matched))

        scored.sort(
            key=lambda x: (
                -x[0],
                -int(x[1].get("priority", 1)),
                x[1]["id"],
            )
        )

        out: list[dict] = []
        for score, r, matched in scored[: max(1, min(limit, 10))]:
            primary = matched[0]
            base = r.get("why") or f"与「{primary}」直接相关的已验证资源"
            reason = (
                f"{base}（关联：{'、'.join(dict.fromkeys(matched[:2]))}）"
            )
            out.append(
                {
                    "resource_id": r["id"],
                    "title": r["title"],
                    "type": r["type"],
                    "url": r["url"],
                    "reason": reason,
                    "minutes": int(r.get("minutes") or 0),
                    "matched": matched[:3],
                }
            )
        return out

    # ================= 可选 AI 筛选/解释（URL 仍只来自资源文件） =================

    def recommend_with_ai(
        self,
        context: dict | None,
        ai_client: AIClient | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """先用规则筛出已验证候选，再让 AI 做取舍/解释。

        安全约束：AI 只被允许引用给定候选的 resource_id；最终 url 一律
        从资源文件映射，即使 AI 文本里出现伪造 URL 也永远不会进入结果。
        AI 不可用 / 失败 / 非法选择 -> 回退到规则推荐。
        """
        matched = self.recommend(context, limit=limit)
        if not matched or ai_client is None or not ai_client.is_configured():
            return matched

        try:
            selections = self._ai_select(ai_client, matched)
        except AIServiceError:
            return matched

        result: list[dict] = []
        for sel in selections:
            res = next(
                (m for m in matched if m["resource_id"] == sel["resource_id"]),
                None,
            )
            if res is None:
                continue  # 未知 id：绝不采纳（URL 只能来自已验证资源）
            result.append(
                {
                    **res,
                    "reason": sel["reason"],
                    "ai_explained": True,
                }
            )
        return result[:limit] if result else matched

    def _ai_select(self, ai_client: AIClient, matched: list[dict]) -> list[dict]:
        """调用 AI 从候选资源中选择并解释；任何问题抛 AIServiceError。"""
        candidates = [
            {"resource_id": m["resource_id"], "title": m["title"],
             "type": m["type"], "url": m["url"], "why": m["reason"]}
            for m in matched
        ]
        system = (
            "你是学习课外资源推荐助手。你只能从给出的已验证资源中挑选并解释，"
            "必须只输出严格 JSON，不能输出资源列表以外的任何 URL。"
        )
        user = (
            "请从以下已验证资源中挑选最相关的 1~3 条并给出具体理由"
            "（理由要对应具体学习内容，不要泛泛而谈）。\n\n"
            f"候选：\n{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
            "输出严格 JSON：\n"
            '{"selections": [{"resource_id": "候选id", "reason": "具体理由"}]}\n'
        )
        try:
            content = ai_client.chat(system, user)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 资源推荐失败: {e}") from e

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise AIServiceError(f"AI 资源推荐不是合法 JSON：{e}") from e

        if not isinstance(data, dict) or not isinstance(data.get("selections"), list):
            raise AIServiceError("AI 资源推荐缺少 selections 数组")

        out: list[dict] = []
        for item in data["selections"]:
            if not isinstance(item, dict):
                raise AIServiceError("selection 必须是对象")
            rid = item.get("resource_id")
            reason = item.get("reason")
            if not isinstance(rid, str) or not rid:
                raise AIServiceError("selection 缺少 resource_id")
            if not isinstance(reason, str) or not reason.strip():
                raise AIServiceError("selection 缺少 reason")
            out.append({"resource_id": rid, "reason": reason.strip()})
        return out
