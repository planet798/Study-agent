"""AI 学习路线草稿 / 路线建议（Phase F）。

纯 AI 调用层：只接收纯数据 context，返回纯 dataclass；**不碰数据库**。
因此可以安全地放在 QThread worker 中执行（AI client 只是 HTTP/config）。

AI 永远不直接写库：草稿与建议都必须经用户 Preview / 确认后，
由主线程用 RoutePlanService / JdSummaryService 持久化。
"""

from __future__ import annotations

from ..ai.interface import AIClient, AIServiceError
from ..ai.prompts import (
    ROUTE_BUILDER_SYSTEM_PROMPT,
    ROUTE_SUGGEST_SYSTEM_PROMPT,
    build_route_builder_prompt,
    build_route_suggest_prompt,
)
from ..ai.schemas import (
    AIRouteDraft,
    AIRouteSuggestion,
    parse_route_draft_from_json,
    parse_route_suggestion_from_json,
)


class AIRouteBuilderService:
    def __init__(self, client: AIClient):
        self.client = client

    def is_configured(self) -> bool:
        return self.client.is_configured()

    def build_draft(
        self,
        context: dict,
        route_skills: list | None = None,
        market: dict | None = None,
    ) -> AIRouteDraft:
        """根据纯数据 context 生成路线草稿；失败抛 AIServiceError。"""
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法生成学习路线")
        user_prompt = build_route_builder_prompt(
            context, route_skills=route_skills, market=market
        )
        try:
            content = self.client.chat(
                ROUTE_BUILDER_SYSTEM_PROMPT, user_prompt
            )
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 生成学习路线失败: {e}") from e
        return parse_route_draft_from_json(content)

    def suggest_routes(
        self, candidate_name: str, routes: list[dict]
    ) -> AIRouteSuggestion:
        """建议候选技能应关联的现有路线；只返回 routes 中真实存在的 name。"""
        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法生成路线建议")
        user_prompt = build_route_suggest_prompt(candidate_name, routes)
        try:
            content = self.client.chat(
                ROUTE_SUGGEST_SYSTEM_PROMPT, user_prompt
            )
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 路线建议失败: {e}") from e
        suggestion = parse_route_suggestion_from_json(content)
        allowed = {r["name"] for r in routes}
        return AIRouteSuggestion(
            suggested_route_names=tuple(
                n for n in suggestion.suggested_route_names if n in allowed
            ),
            reason=suggestion.reason,
        )
