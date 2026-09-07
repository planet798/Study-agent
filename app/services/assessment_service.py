"""验收题生成服务。

职责（Phase 3C 边界内）：
- 根据 knowledge_points 行（name / description）生成客观验收题；
- 复用现有 AIClient.chat 与 app.ai.schemas 的结构化校验；
- 只负责“出题”，不判断答案、不更新掌握度、不写 assessment_attempts。

任何 AI 失败 / 非法结构统一抛 AIServiceError；空知识点名抛 ValueError。
"""

from __future__ import annotations

from ..ai.interface import AIClient, AIServiceError
from ..ai.prompts import ASSESSMENT_SYSTEM_PROMPT, build_assessment_prompt
from ..ai.schemas import (
    MAX_ASSESSMENT_QUESTIONS,
    AssessmentQuestionSet,
    parse_assessment_questions_from_json,
)


def _as_knowledge_dict(knowledge_point) -> dict:
    """把 dict / sqlite3.Row 统一为普通 dict。"""
    if isinstance(knowledge_point, dict):
        return knowledge_point
    try:
        return dict(knowledge_point)
    except (TypeError, ValueError):
        raise ValueError(
            "knowledge_point 必须是 dict 或可转换为 dict 的对象"
        ) from None


class AssessmentService:
    def __init__(self, client: AIClient):
        self.client = client

    def is_configured(self) -> bool:
        """AI 是否已配置（未配置时上层应给出明确提示而非崩溃）。"""
        return self.client.is_configured()

    def generate_questions(
        self,
        knowledge_point,
        num_questions: int = 4,
    ) -> AssessmentQuestionSet:
        """为给定知识点生成一组客观验收题。

        :param knowledge_point: dict（或可转 dict 的对象），至少包含 name，
           可选 description。
        :param num_questions: 目标题数，会被夹在 1~MAX_ASSESSMENT_QUESTIONS。
        :raises ValueError: 知识点名为空。
        :raises AIServiceError: AI 未配置 / 调用失败 / 返回非法结构。
        """
        data = _as_knowledge_dict(knowledge_point)
        name = (data.get("name") or "").strip() if isinstance(data, dict) else ""
        if not name:
            raise ValueError("知识点名不能为空")

        if not self.client.is_configured():
            raise AIServiceError("AI 未配置，无法生成验收题")

        target = max(1, min(int(num_questions), MAX_ASSESSMENT_QUESTIONS))
        description = str(data.get("description") or "").strip()
        user_prompt = build_assessment_prompt(name, description, target)

        try:
            content = self.client.chat(ASSESSMENT_SYSTEM_PROMPT, user_prompt)
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001 - 不泄漏底层异常
            raise AIServiceError(f"AI 出题失败: {e}") from e

        try:
            return parse_assessment_questions_from_json(content)
        except AIServiceError:
            raise  # 非法结构：绝不直接使用模型输出
