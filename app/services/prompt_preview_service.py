"""Prompt 最终预览服务：基于真实当前数据构造 Runtime Context。

职责：
- 为每个 Prompt key 组装运行时变量（变量值来自真实 DB / 服务；无真实数据时
  用明确标注的“示例数据”，绝不伪装成真实数据）；
- 通过 PromptRegistry 使用“当前生效模板（默认或用户覆盖）”渲染最终 Prompt；
- 支持 Planner 预览按路线选择，验证“Prompt 是否还写着旧路线”。

严格区分三层：
    A. Prompt Template（用户可编辑）
    B. Runtime Context（系统动态生成，只读）
    C. Final Rendered Prompt（实际发送给模型）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional

from ..ai.prompt_defaults import PromptDefinition
from ..ai.prompt_registry import PromptPreview, PromptRegistry
from ..ai.prompts import (
    build_assessment_generate_vars,
    build_assessment_judge_vars,
    build_jd_parse_vars,
    build_monthly_summary_vars,
    build_planner_system_vars,
    build_planner_user_vars,
    build_resume_material_vars,
    build_route_builder_vars,
    build_route_suggest_vars,
    build_task_review_vars,
)

# ------------------ 示例数据（无真实数据时使用，明确标注） ------------------

SAMPLE_MARK = "（示例数据）"

SAMPLE_TASK = {
    "title": f"完成 Python 装饰器练习{SAMPLE_MARK}",
    "description": "实现一个带参数的装饰器并写测试",
    "category": "学习",
    "estimated_minutes": 45,
    "priority": "中",
    "scheduled_date": "（示例日期）",
    "postpone_count": 1,
    "reason": "临时有课程实验",
}

SAMPLE_KNOWLEDGE_POINT = {
    "name": f"Python 装饰器{SAMPLE_MARK}",
    "description": "闭包、functools.wraps、带参数装饰器",
}

SAMPLE_QUESTIONS = [
    {"question": f"解释装饰器的执行时机{SAMPLE_MARK}", "type": "concept",
     "expected_points": 2},
    {"question": "写一个带参数装饰器", "type": "coding", "expected_points": 3},
]
SAMPLE_ANSWERS = ["装饰器在函数定义时执行，返回新函数。", "略。"]

SAMPLE_JD_TEXT = (
    f"{SAMPLE_MARK} 招聘算法实习生，要求熟悉 Python、PyTorch，"
    "了解 Transformer / LLM 基础，有 RAG 经验优先。"
)

SAMPLE_ROUTE_CONTEXT = {
    "route_name": f"（示例路线）{SAMPLE_MARK}",
    "goal": "掌握推荐系统与 LLM 应用基础",
    "background": "有 Python 基础",
    "focus": "推荐 / LLM",
    "depth": "能完成小项目",
}

SAMPLE_OUTCOMES = [
    {
        "title": f"电影推荐 Demo{SAMPLE_MARK}",
        "content": "实现基于协同过滤的电影推荐。",
        "kind": "project",
        "tech_stack": '["Python", "pandas"]',
        "dataset": "",
        "metrics": "{}",
        "github_url": "",
        "resume_keywords": '["推荐系统"]',
    }
]


@dataclass
class ConversationPreview:
    """一个功能真实的消息结构预览（system + user + 运行时上下文）。"""

    prompt_key: str
    system: Optional[PromptPreview] = None
    user: Optional[PromptPreview] = None
    context: dict = field(default_factory=dict)

    @property
    def definition(self) -> PromptDefinition:
        src = self.system or self.user
        assert src is not None
        return src.definition


def _role(key: str) -> str:
    return "system" if key.endswith(".system") else "user"


def _swap_role(key: str) -> str:
    if key.endswith(".system"):
        return key[: -len(".system")] + ".user"
    if key.endswith(".user"):
        return key[: -len(".user")] + ".system"
    return key


class PromptPreviewService:
    """为 UI 构造真实运行时上下文 + 渲染最终 Prompt。"""

    def __init__(
        self,
        registry: PromptRegistry,
        *,
        today_provider=None,
        daily_planner_service=None,
        scheduler=None,
        route_repo=None,
        summary_service=None,
        assessment_repo=None,
        outcome_service=None,
        task_repo=None,
        jd_repo=None,
    ):
        self.registry = registry
        self.today_provider = today_provider or (lambda: _date.today().isoformat())
        self.daily_planner_service = daily_planner_service
        self.scheduler = scheduler
        self.route_repo = route_repo
        self.summary_service = summary_service
        self.assessment_repo = assessment_repo
        self.outcome_service = outcome_service
        self.task_repo = task_repo
        self.jd_repo = jd_repo

    # ================= 路线列表 =================

    def available_routes(self) -> list[dict]:
        if self.route_repo is None:
            return []
        try:
            routes = self.route_repo.list_learning_routes()
        except Exception:  # noqa: BLE001
            return []
        return [{"id": r.id, "name": r.name, "goal": getattr(r, "goal", "")}
                for r in routes]

    # ================= 上下文构造 =================

    def build_context(self, key: str, *, route_id: int | None = None) -> dict:
        base = key.split(".")[0]
        builder = getattr(self, f"_context_{base}", None)
        if builder is None:
            return {}
        return builder(key, route_id)

    # ---------- 任务复核 ----------

    def _context_task_review(self, key: str, route_id) -> dict:
        task = self._pick_task()
        if task is None:
            data = dict(SAMPLE_TASK)
        else:
            from ..utils.date_utils import to_display

            data = {
                "title": task.title,
                "description": task.description or "",
                "category": task.category or "未分类",
                "estimated_minutes": task.estimated_minutes,
                "priority": {1: "低", 2: "中", 3: "高"}.get(task.priority, "未知"),
                "scheduled_date": to_display(task.scheduled_date),
                "postpone_count": task.postpone_count,
                "reason": "（真实任务暂无未完成原因，使用示例原因）临时有课程实验",
            }
        from ..database.repository import Task

        pseudo = Task(
            id=0,
            title=data["title"],
            description=data["description"],
            category=data["category"],
            estimated_minutes=int(data["estimated_minutes"] or 0),
            priority={"低": 1, "中": 2, "高": 3}.get(data["priority"], 2),
            status="active",
            reason=None,
            scheduled_date="1970-01-01",
            postpone_count=int(data["postpone_count"] or 0),
            created_at="",
            updated_at="",
            completed_at=None,
            not_done_at=None,
        )
        # 用真实 task 或伪 task 都能构造；伪 task 直接覆盖日期显示
        ctx = build_task_review_vars(pseudo, data["reason"], today=self.today_provider())
        ctx["task_scheduled_date"] = data["scheduled_date"]
        return ctx

    def _pick_task(self):
        if self.task_repo is None:
            return None
        try:
            for t in self.task_repo.list_by_date(self.today_provider()):
                return t
            # 找不到今日任务则取最近任意任务
            rows = self.task_repo.conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return self.task_repo._row_to_task(rows) if rows is not None else None
        except Exception:  # noqa: BLE001
            return None

    # ---------- Planner ----------

    def _planner_for(self, route_id: int | None):
        if route_id is not None and self.scheduler is not None:
            try:
                return self.scheduler._make_route_planner(int(route_id))
            except Exception:  # noqa: BLE001
                pass
        return self.daily_planner_service

    def _context_planner(self, key: str, route_id) -> dict:
        limit = 180
        if _role(key) == "system":
            # system 提示只需要 daily_limit（所有路线一致），无需构造路线 Planner
            if self.daily_planner_service is not None:
                limit = getattr(self.daily_planner_service, "max_daily_minutes", 180)
            return build_planner_system_vars(limit)
        planner = self._planner_for(route_id)
        if planner is None:
            # 无真实数据：仍给出可渲染的示例上下文
            from ..ai.planner_context import PlanningContext

            ctx = PlanningContext(current_date=self.today_provider())
            ctx.current_daily_limit = limit
            return build_planner_user_vars(ctx, None)
        try:
            context = planner.build_context(self.today_provider())
        except Exception:  # noqa: BLE001
            from ..ai.planner_context import PlanningContext

            context = PlanningContext(current_date=self.today_provider())
            context.current_daily_limit = planner.max_daily_minutes
        long_term = getattr(getattr(planner, "planner", None), "long_term_context", None)
        return build_planner_user_vars(context, long_term)

    # ---------- Assessment ----------

    def _pick_knowledge_point(self):
        if self.assessment_repo is None:
            return None
        try:
            points = self.assessment_repo.list_knowledge_points()
        except Exception:  # noqa: BLE001
            return None
        return points[0] if points else None

    def _context_assessment(self, key: str, route_id) -> dict:
        if key == "assessment.generate.system":
            return {}
        if key.startswith("assessment.generate"):
            kp = self._pick_knowledge_point()
            if kp is None:
                data = SAMPLE_KNOWLEDGE_POINT
            else:
                data = {
                    "name": kp.get("name") or "",
                    "description": kp.get("description") or "",
                }
            return build_assessment_generate_vars(
                data["name"], data["description"], 4
            )
        # judge
        questions = SAMPLE_QUESTIONS
        answers = SAMPLE_ANSWERS
        if self.assessment_repo is not None:
            try:
                attempts = self.assessment_repo.list_attempts()
                for a in attempts:
                    import json

                    q = json.loads(a.get("questions_json") or "[]")
                    if q:
                        questions = q
                        ans = json.loads(a.get("answers_json") or "[]")
                        answers = ans if ans else ["（该次验收尚未提交答案）"] * len(q)
                        break
            except Exception:  # noqa: BLE001
                pass
        return build_assessment_judge_vars(questions, answers)

    # ---------- Summary ----------

    def _context_summary(self, key: str, route_id) -> dict:
        if key == "summary.monthly.system":
            return {}
        today = self.today_provider()
        year, month = int(today[:4]), int(today[5:7])
        stats: dict = {}
        if self.summary_service is not None:
            try:
                stats = self.summary_service.get_monthly_summary(year, month)["stats"]
            except Exception:  # noqa: BLE001
                stats = {}
        if not stats:
            stats = {"month": f"{year}-{month:02d}", "note": SAMPLE_MARK}
        return build_monthly_summary_vars(stats)

    # ---------- Route builder / suggestion ----------

    def _context_route_builder(self, key: str, route_id) -> dict:
        if key == "route_builder.system":
            return {}
        routes = self.available_routes()
        context = dict(SAMPLE_ROUTE_CONTEXT)
        if routes:
            r = next((x for x in routes if route_id and x["id"] == route_id), routes[0])
            context = {
                "route_name": r["name"],
                "goal": r.get("goal") or "",
                "background": "",
                "focus": "",
                "depth": "",
            }
        skills = []
        if self.route_repo is not None and route_id is not None:
            try:
                skills = [
                    {"skill_id": sid}
                    for sid in self.route_repo.list_skill_ids(int(route_id))
                ]
            except Exception:  # noqa: BLE001
                skills = []
        return build_route_builder_vars(context, skills, None)

    def _context_route_suggestion(self, key: str, route_id) -> dict:
        if key == "route_suggestion.system":
            return {}
        routes = self.available_routes()
        return build_route_suggest_vars(
            f"新技能候选{SAMPLE_MARK}", routes or [{"name": "（示例路线）", "id": 0}]
        )

    # ---------- JD ----------

    def _context_jd_parse(self, key: str, route_id) -> dict:
        if key == "jd_parse.system":
            return {}
        raw = ""
        if self.jd_repo is not None:
            try:
                jds = self.jd_repo.list_all()
                if jds:
                    raw = jds[0].get("raw_text") or ""
            except Exception:  # noqa: BLE001
                raw = ""
        return build_jd_parse_vars(raw or SAMPLE_JD_TEXT)

    # ---------- Resume ----------

    def _context_resume_material(self, key: str, route_id) -> dict:
        if key == "resume_material.system":
            return {}
        outcomes = []
        if self.outcome_service is not None:
            try:
                outcomes = self.outcome_service.list_recent(limit=10)
            except Exception:  # noqa: BLE001
                outcomes = []
        return build_resume_material_vars(outcomes or SAMPLE_OUTCOMES)

    # ================= 预览 =================

    def preview_conversation(
        self, prompt_key: str, *, route_id: int | None = None
    ) -> ConversationPreview:
        """返回一个功能完整的 system + user + runtime context 预览。"""
        system_key = prompt_key
        user_key = _swap_role(prompt_key)
        if _role(prompt_key) == "user":
            system_key, user_key = user_key, prompt_key

        context = self.build_context(user_key, route_id=route_id)
        # system 变量（如 daily_limit）从 user 上下文里补齐
        system_context = dict(context)
        try:
            system_context.update(self.build_context(system_key, route_id=route_id))
        except Exception:  # noqa: BLE001
            pass

        conv = ConversationPreview(prompt_key=prompt_key, context=context)
        if system_key in self.registry.keys():
            conv.system = self.registry.preview(system_key, system_context)
        if user_key in self.registry.keys():
            conv.user = self.registry.preview(user_key, context)
        return conv

    def preview(
        self, prompt_key: str, *, route_id: int | None = None
    ) -> PromptPreview:
        """单条消息预览（保持旧接口）。"""
        if _role(prompt_key) == "system":
            context = self.build_context(_swap_role(prompt_key), route_id=route_id)
            context.update(self.build_context(prompt_key, route_id=route_id))
        else:
            context = self.build_context(prompt_key, route_id=route_id)
        return self.registry.preview(prompt_key, context)
