"""验收服务。

职责边界：
- Phase 3C：根据 knowledge_points 生成客观验收题（generate_questions）；
- Phase 3D：
  - start_assessment：生成题目并建一条 assessment_attempts 记录；
  - submit_answers：校验答案数量 -> 保存 answers_json -> AI 判题 ->
    写入 ai_result_json / weak_points_json / result_level / mastery_estimate，
    并回写 knowledge_points 的掌握度 / 最近验收时间 / 验收次数。

原则：
- mastery_estimate 只由 AI 基于真实作答证据推断，禁止用户自评；
- AI 判题失败绝不伪造掌握度：保留答案、标记失败、允许稍后重判；
- 不实现复习算法、不实现 UI、不接入 Planner。
"""

from __future__ import annotations

import json

from ..ai.interface import AIClient, AIServiceError
from ..ai.prompts import (
    ASSESSMENT_JUDGE_SYSTEM_PROMPT,
    ASSESSMENT_SYSTEM_PROMPT,
    build_assessment_judge_prompt,
    build_assessment_prompt,
)
from ..ai.schemas import (
    MAX_ASSESSMENT_QUESTIONS,
    AssessmentQuestionSet,
    AssessmentJudgment,
    parse_assessment_judgment_from_json,
    parse_assessment_questions_from_json,
)
from ..database.assessment_repository import AssessmentRepository
from ..utils.date_utils import now_iso
from .review_service import ReviewService

# 掌握度平滑策略：旧估计 70% + 本次估计 30%，防止单次结果直接覆盖成极端值
_PREVIOUS_WEIGHT = 0.7
_NEW_WEIGHT = 0.3

_JUDGE_STATUS_PENDING = "pending"
_JUDGE_STATUS_JUDGED = "judged"
_JUDGE_STATUS_FAILED = "failed"


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
    def __init__(
        self,
        client: AIClient,
        assessment_repo: AssessmentRepository | None = None,
        review_service: ReviewService | None = None,
        outcome_service=None,
    ):
        self.client = client
        self.assessment_repo = assessment_repo
        # 可选注入：判题成功后联动复习调度（Phase 4）
        self.review_service = review_service
        # 可选注入：验收后沉淀学习成果（Phase D hook；不注入行为不变）
        self.outcome_service = outcome_service

    def is_configured(self) -> bool:
        """AI 是否已配置（未配置时上层应给出明确提示而非崩溃）。"""
        return self.client.is_configured()

    def _require_repo(self) -> AssessmentRepository:
        if self.assessment_repo is None:
            raise RuntimeError("AssessmentService 未注入 assessment_repo")
        return self.assessment_repo

    # ================= 出题（Phase 3C 延续） =================

    def generate_questions(
        self,
        knowledge_point,
        num_questions: int = 4,
    ) -> AssessmentQuestionSet:
        """为给定知识点生成一组客观验收题（不落库、不判题）。"""
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

    # ================= 验收闭环 =================

    def start_assessment(
        self,
        knowledge_point_id: int,
        task_id: int | None = None,
        num_questions: int = 4,
    ) -> dict:
        """生成验收题并创建一条 pending 的 assessment_attempts 记录。

        :return: attempt dict（含 id / knowledge_point_id / task_id /
            questions_json / judge_status）；额外附 questions 字段便于展示。
        """
        repo = self._require_repo()
        kp = repo.get_knowledge_point(knowledge_point_id)
        if kp is None:
            raise ValueError(f"知识点不存在: id={knowledge_point_id}")

        question_set = self.generate_questions(kp, num_questions=num_questions)
        attempt = repo.create_attempt(
            knowledge_point_id=knowledge_point_id,
            questions_json=question_set.questions_json(),
            task_id=task_id,
        )
        attempt["questions"] = list(question_set.questions)
        return attempt

    def submit_answers(
        self, attempt_id: int, answers, today: str | None = None
    ) -> dict:
        """提交用户答案并（尽力）AI 判题，最后回写知识点掌握度。

        流程：
        1. 校验答案数量与题目数量一致、答案非空字符串；
        2. 保存 answers_json（保留题目，不覆盖 questions_json）；
        3. 判题成功 -> 写 ai_result_json / weak_points_json / result_level /
           mastery_estimate，并更新 knowledge_points；
        4. 判题失败 -> 保留答案，标记 judge_status='failed'，写 judge_error，
           绝不伪 master。

        :return: 更新后的 attempt dict。
        """
        repo = self._require_repo()
        attempt = repo.get_attempt(attempt_id)
        if attempt is None:
            raise ValueError(f"验收记录不存在: id={attempt_id}")

        questions = self._questions_from_attempt(attempt)
        cleaned_answers = self._normalize_answers(answers, len(questions))
        repo.update_attempt(
            attempt_id,
            answers_json=json.dumps(cleaned_answers, ensure_ascii=False),
        )

        # 判题
        if not self.client.is_configured():
            repo.update_attempt(
                attempt_id,
                judge_status=_JUDGE_STATUS_FAILED,
                judge_error="AI 未配置，无法判题",
            )
            return repo.get_attempt(attempt_id)

        try:
            judgment = self._judge(questions, cleaned_answers)
        except AIServiceError as e:
            repo.update_attempt(
                attempt_id,
                judge_status=_JUDGE_STATUS_FAILED,
                judge_error=str(e),
            )
            return repo.get_attempt(attempt_id)

        self._save_judgment(attempt_id, judgment)
        self._update_knowledge_point_mastery(
            attempt["knowledge_point_id"], judgment.mastery_estimate
        )
        # 判题成功后联动复习调度（若注入了 ReviewService）
        if self.review_service is not None:
            self.review_service.record_assessment_result(
                repo.get_attempt(attempt_id), today=today
            )
        # Phase D：验收后沉淀学习成果/掌握证据（若注入了 outcome_service）
        if self.outcome_service is not None:
            try:
                judged_attempt = repo.get_attempt(attempt_id)
                kp = repo.get_knowledge_point(attempt["knowledge_point_id"])
                self.outcome_service.generate_from_assessment(
                    judged_attempt, kp=kp, date=today or ""
                )
            except Exception:  # noqa: BLE001 - 成果沉淀失败不影响验收流程
                pass
        return repo.get_attempt(attempt_id)

    # ---------- 内部 ----------

    @staticmethod
    def _questions_from_attempt(attempt: dict) -> list[dict]:
        try:
            questions = json.loads(attempt["questions_json"] or "[]")
        except json.JSONDecodeError as e:
            raise AIServiceError(f"验收记录中的题目 JSON 非法：{e}") from e
        if not isinstance(questions, list) or not questions:
            raise AIServiceError("验收记录中没有合法题目")
        return questions

    @staticmethod
    def _normalize_answers(answers, expected_count: int) -> list[str]:
        if not isinstance(answers, (list, tuple)):
            raise ValueError("answers 必须是数组")
        if len(answers) != expected_count:
            raise ValueError(
                f"答案数量({len(answers)})与题目数量({expected_count})不一致"
            )
        cleaned: list[str] = []
        for i, ans in enumerate(answers):
            if not isinstance(ans, str) or not ans.strip():
                raise ValueError(f"第 {i + 1} 题答案不能为空")
            cleaned.append(ans.strip())
        return cleaned

    def _judge(
        self, questions: list[dict], answers: list[str]
    ) -> AssessmentJudgment:
        """调用 AI 判题并校验结果；任何失败抛 AIServiceError。"""
        user_prompt = build_assessment_judge_prompt(questions, answers)
        try:
            content = self.client.chat(
                ASSESSMENT_JUDGE_SYSTEM_PROMPT, user_prompt
            )
        except AIServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AIServiceError(f"AI 判题失败: {e}") from e

        judgment = parse_assessment_judgment_from_json(content)
        # 逐题对应：判题数量必须与题目数量一致
        if len(judgment.question_judgments) != len(questions):
            raise AIServiceError(
                "判题结果题目数与实际题目数不一致"
            )
        return judgment

    def _save_judgment(self, attempt_id: int, judgment: AssessmentJudgment) -> None:
        self._require_repo().update_attempt(
            attempt_id,
            ai_result_json=judgment.to_json(),
            weak_points_json=json.dumps(
                list(judgment.weak_points), ensure_ascii=False
            ),
            result_level=judgment.result_level,
            mastery_estimate=judgment.mastery_estimate,
            judge_status=_JUDGE_STATUS_JUDGED,
            judge_error="",
        )

    def _update_knowledge_point_mastery(
        self, knowledge_point_id: int, judged_estimate: float
    ) -> None:
        """把本次判题证据回写到知识点（简单、可解释的平滑策略）。"""
        repo = self._require_repo()
        kp = repo.get_knowledge_point(knowledge_point_id)
        if kp is None:
            return

        previous = float(kp["mastery_estimate"] or 0.0)
        has_previous = kp["last_assessed_at"] is not None
        if has_previous:
            new_mastery = round(
                _PREVIOUS_WEIGHT * previous + _NEW_WEIGHT * judged_estimate, 4
            )
        else:
            # 首次验收：直接采用本次证据推断
            new_mastery = round(judged_estimate, 4)

        repo.update_knowledge_point(
            knowledge_point_id,
            mastery_estimate=new_mastery,
            last_assessed_at=now_iso(),
            review_count=int(kp["review_count"] or 0) + 1,
        )
