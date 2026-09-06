"""学习计划服务层。

职责：
- 管理长期学习计划（StudyPlan / StudyPhase / StudyTopic）的种子与查询；
- 根据日期判断当前所在阶段（get_current_phase）；
- 根据当前阶段的主题生成每日任务（generate_daily_tasks，规则简单可预测，不使用 LLM）。

分层原则：
- 长期计划 ≠ 每日任务。
  StudyPlan/Phase/Topic 定义"学什么"，tasks 定义"今天实际做什么"。
- 任务生成结果进入现有 tasks 表，与 TaskService 完全兼容。
"""

from __future__ import annotations

from ..database.repository import TaskRepository
from ..database.schema import STATUS_ACTIVE, STATUS_DONE
from ..database.study_plan_repository import StudyPlan, StudyPlanRepository

# 默认每日自主学习时间预算（分钟）
MAX_DAILY_STUDY_MINUTES = 180

# 长期学习路线的默认阶段/主题种子（与 docs/career_context.json 的 skill_roadmap 对齐）。
# Phase1 保留历史名称与主题，以兼容已有完成任务的主题关联；
# 后续阶段按 career_context 的“阶段二~五”展开为可跟踪主题。
_DEFAULT_PHASES = [
    {
        "name": "Python + Linux + Git",
        "desc": "编程与开发环境基础（工程底座）",
        "start": "2026-09-01", "end": "2026-09-05",
        "priority": 3,
        "goals": "能熟练用 Python 写脚本、用 Git 协作、Linux 下工作",
        "topics": [
            ("Python 语法与基础练习", 45, 3),
            ("Python 面向对象与常用库", 45, 2),
            ("Linux 常用命令与工具链", 30, 2),
            ("Git 版本控制与协作流程", 30, 1),
        ],
    },
    {
        "name": "阶段二：深度学习与 LLM 基础",
        "desc": "从 PyTorch 到 Transformer 再到 LLM 基础机制",
        "start": "2026-09-06", "end": "2026-12-31",
        "priority": 2,
        "goals": "能独立训练最小模型，理解 Transformer 与 LLM 核心机制",
        "topics": [
            ("PyTorch 张量与自动求导（Tensor / autograd）", 60, 3),
            ("最小线性回归训练闭环（y=2x+1）", 60, 3),
            ("Dataset 与 DataLoader", 60, 2),
            ("nn.Module 与模型搭建", 60, 2),
            ("Transformer：Attention / MHA / FFN", 60, 3),
            ("LayerNorm / RMSNorm", 45, 2),
            ("Tokenizer 与分词", 45, 2),
            ("RoPE 位置编码", 45, 2),
            ("KV Cache", 45, 2),
            ("Hugging Face Transformers", 60, 2),
            ("generate / sampling 解码策略", 45, 2),
            ("Qwen / LLaMA 架构：GQA / SwiGLU", 60, 1),
        ],
    },
    {
        "name": "阶段三：LLM 应用",
        "desc": "LLM 应用开发、RAG 与 Agent",
        "start": "2027-01-01", "end": "2027-03-31",
        "priority": 2,
        "goals": "能搭建可用的 RAG 与简单 Agent 应用",
        "topics": [
            ("Function Calling / Tool Calling", 60, 3),
            ("ReAct 推理与行动", 45, 3),
            ("Planning 任务规划", 45, 2),
            ("Memory / State 状态管理", 45, 2),
            ("RAG 全流程搭建", 60, 3),
            ("Chunking 分块策略", 45, 2),
            ("Embedding 与向量检索", 45, 2),
            ("BM25 与混合检索", 45, 2),
            ("RRF 排序融合", 30, 1),
            ("Reranker 重排序", 45, 2),
            ("Evaluation / Badcase / LLM-as-Judge", 45, 2),
            ("Agent 实现与多步编排", 60, 3),
        ],
    },
    {
        "name": "阶段四：模型训练与部署",
        "desc": "高效微调与生产部署",
        "start": "2027-04-01", "end": "2027-06-30",
        "priority": 2,
        "goals": "掌握 LoRA/SFT 微调与 vLLM 部署基础",
        "topics": [
            ("LoRA / QLoRA", 60, 3),
            ("PEFT", 45, 2),
            ("SFT 指令微调", 60, 3),
            ("LLaMA-Factory", 45, 2),
            ("vLLM 与 PagedAttention", 60, 2),
            ("Continuous Batching", 45, 2),
            ("Docker", 60, 1),
        ],
    },
    {
        "name": "阶段五：后续扩展",
        "desc": "多模态与推理优化（按方向选择深入）",
        "start": "2027-07-01", "end": "2027-08-31",
        "priority": 1,
        "goals": "了解多模态与推理优化方向，按需深入",
        "topics": [
            ("VLM / 多模态基础", 60, 2),
            ("DDP / ZeRO / DeepSpeed（先理解）", 60, 1),
            ("推理优化基础", 45, 2),
            ("C/C++ / CUDA（方向确定后深入）", 60, 1),
            ("VLA / World Model（了解）", 60, 1),
        ],
    },
]

_EXPECTED_PHASE_NAMES = tuple(p["name"] for p in _DEFAULT_PHASES)


class StudyPlanService:
    def __init__(
        self,
        repo: TaskRepository,
        plan_repo: StudyPlanRepository | None = None,
        max_daily_minutes: int = MAX_DAILY_STUDY_MINUTES,
    ):
        self.repo = repo
        self.plan_repo = plan_repo or StudyPlanRepository(repo.conn)
        # 允许测试/日后调整预算
        self.max_daily_minutes = max_daily_minutes
        self._default_plan_created = False

    # ================= 默认研一计划 =================

    def ensure_default_plan(self) -> StudyPlan:
        """确保默认计划存在并与长期学习路线一致（幂等）。

        首次运行：创建计划 + 全量种子。
        之后每次运行：对已有计划就地同步（更新/补齐阶段与主题，并移除
        没有任务引用的过期阶段），绝不触碰历史任务。
        """
        existing = self.plan_repo.get_active_plan()
        if existing is None:
            self._default_plan_created = True
            return self._create_default_plan()
        self._default_plan_created = False
        self._reconcile_plan(existing)
        return self.plan_repo.get_active_plan() or existing

    def _create_default_plan(self) -> StudyPlan:
        plan = self.plan_repo.create_plan(
            name="USTC AI 研一大厂算法路线",
            description=(
                "基于真实 JD 与 docs/career_context.json 的长期算法学习路线："
                "工程底座 → 深度学习与 LLM 基础 → LLM 应用 → 模型训练与部署 → 扩展。"
            ),
            start_date="2026-09-01",
            end_date="2027-08-31",
        )
        self._seed_phases_and_topics(plan.id)
        return plan

    def _seed_phases_and_topics(self, plan_id: int) -> None:
        """按 _DEFAULT_PHASES 写入阶段与主题（仅新建计划时使用）。"""
        for spec in _DEFAULT_PHASES:
            phase = self.plan_repo.create_phase(
                plan_id=plan_id,
                name=spec["name"],
                description=spec["desc"],
                start_date=spec["start"],
                end_date=spec["end"],
                priority=spec["priority"],
                goals=spec["goals"],
            )
            self._upsert_topics(phase.id, spec["topics"])

    # ---------- 计划同步（把旧 DB 计划对齐到长期学习路线） ----------

    def _reconcile_plan(self, plan: StudyPlan) -> None:
        """把已有计划就地同步到 _DEFAULT_PHASES（幂等，不触碰任务历史）。

        - 对每个预期阶段/主题按 name 做 upsert（不存在创建、存在更新）；
        - 移除“预期之外”且没有任何任务引用的过期阶段（如旧的八阶段计划）。
        """
        for spec in _DEFAULT_PHASES:
            phase = self._find_phase_by_name(plan.id, spec["name"])
            if phase is None:
                phase = self.plan_repo.create_phase(
                    plan_id=plan.id,
                    name=spec["name"],
                    description=spec["desc"],
                    start_date=spec["start"],
                    end_date=spec["end"],
                    priority=spec["priority"],
                    goals=spec["goals"],
                )
            else:
                self.plan_repo.update_phase(
                    phase.id,
                    description=spec["desc"],
                    start_date=spec["start"],
                    end_date=spec["end"],
                    priority=spec["priority"],
                    goals=spec["goals"],
                )
            self._upsert_topics(phase.id, spec["topics"])

        for phase in self.plan_repo.list_phases(plan.id):
            if phase.name in _EXPECTED_PHASE_NAMES:
                continue
            self._delete_phase_if_unused(phase)

    def _find_phase_by_name(self, plan_id: int, name: str):
        for phase in self.plan_repo.list_phases(plan_id):
            if phase.name == name:
                return phase
        return None

    def _upsert_topics(self, phase_id: int, topics) -> None:
        """按主题名 upsert；只新增/更新，不删除（保留历史主题关联）。"""
        existing = {t.name: t for t in self.plan_repo.list_topics(phase_id)}
        for idx, (name, minutes, priority) in enumerate(topics):
            topic = existing.get(name)
            if topic is None:
                self.plan_repo.create_topic(
                    phase_id=phase_id,
                    name=name,
                    description=name,
                    estimated_minutes=minutes,
                    priority=priority,
                    order_index=idx,
                )
            else:
                self.plan_repo.update_topic(
                    topic.id,
                    description=name,
                    estimated_minutes=minutes,
                    priority=priority,
                    order_index=idx,
                )

    def _delete_phase_if_unused(self, phase) -> None:
        """仅当阶段下所有主题都没有任务引用时才删除（保护历史任务）。"""
        for topic in self.plan_repo.list_topics(phase.id):
            if self._topic_has_tasks(topic.id):
                return
        for topic in self.plan_repo.list_topics(phase.id):
            self.plan_repo.delete_topic(topic.id)
        self.plan_repo.delete_phase(phase.id)

    def _topic_has_tasks(self, topic_id: int) -> bool:
        row = self.repo.conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE topic_id = ?", (topic_id,)
        ).fetchone()
        return (row["n"] or 0) > 0

    # ================= 当前阶段 =================

    def get_active_plan_full(self) -> StudyPlan | None:
        """获取 active 计划并加载全部阶段及主题。"""
        plan = self.plan_repo.get_active_plan()
        if plan is None:
            return None
        return self.plan_repo.get_plan_with_phases(plan.id)

    def get_current_phase(self, date_str: str):
        """返回当前应学习的阶段；无匹配返回 None。

        规则：
        - 先按日期锚定阶段（start_date <= date <= end_date）；
        - 若锚定阶段及其后续阶段的所有主题都已完成，则自动推进到下一个
          还有未完成任务主题的阶段（即使日期窗口尚未开始），避免“阶段完成
          后每天无任务可生成”的卡死；
        - 计划期之外（早于开始或晚于结束）仍返回 None。
        """
        plan = self.get_active_plan_full()
        if plan is None or not plan.phases:
            return None
        phases = sorted(plan.phases, key=lambda p: p.start_date)
        if date_str < phases[0].start_date:
            return None
        if date_str > phases[-1].end_date:
            return None

        anchor_index = None
        for idx, phase in enumerate(phases):
            if phase.start_date <= date_str <= phase.end_date:
                anchor_index = idx
                break
        if anchor_index is None:
            # 落在阶段间隙：取后续第一个阶段
            anchor_index = next(
                (i for i, p in enumerate(phases) if p.start_date > date_str),
                None,
            )
            if anchor_index is None:
                return None

        done_ids = self._done_topic_ids()
        for phase in phases[anchor_index:]:
            if self._phase_has_remaining_topics(phase, done_ids):
                return phase
        return None

    def _phase_has_remaining_topics(self, phase, done_topic_ids: set[int]) -> bool:
        """阶段内是否还存在未完成（未生成过 done 任务）的主题。"""
        return any(t.id not in done_topic_ids for t in phase.topics)

    # ================= 每日任务生成 =================

    def generate_daily_tasks(self, date_str: str) -> dict:
        """为 date_str 生成每日学习任务（不使用 LLM，规则简单可预测）。

        规则（按优先级排序后逐条采纳）：
        1. 当天已存在的任务占用预算（延期任务优先：已有任务的活动任务
           视为已占用时间，且不会重复生成同主题任务）；
        2. 高优先级主题优先；
        3. 已经完成过的主题不再重复生成；
        4. 不超过 max_daily_minutes 总预算；
        5. 若当天还没有任何任务，至少安排一个核心主题；
        6. 剩余预算装不下剩余主题时停止，不强行塞满。

        :return: {"generated": [Task], "phase": name|None, "selected": [topic_id], ...}
        """
        result = {
            "phase": None,
            "generated": [],
            "selected": [],
            "skipped_done": [],
            "skipped_duplicate": [],
            "skipped_budget": [],
        }
        phase = self.get_current_phase(date_str)
        if phase is None:
            return result
        result["phase"] = phase.name

        # 今天已有的任务（含延期进来的）
        today_tasks = self.repo.list_by_date(date_str)
        active_topic_ids = {
            t.topic_id
            for t in today_tasks
            if t.topic_id is not None and t.status == STATUS_ACTIVE
        }
        committed = sum(
            t.estimated_minutes
            for t in today_tasks
            if t.status == STATUS_ACTIVE
        )
        remaining = self.max_daily_minutes - committed

        # 已完成的主题列表（任意日期完成过即视为已掌握）
        done_topic_ids = self._done_topic_ids()

        # 按优先级高在前排（同优先级按创建顺序）
        topics = sorted(
            phase.topics,
            key=lambda t: (-t.priority, t.order_index),
        )

        for topic in topics:
            if topic.id in done_topic_ids:
                result["skipped_done"].append(topic.id)
                continue
            if topic.id in active_topic_ids:
                result["skipped_duplicate"].append(topic.id)
                continue
            # 至少安排一个核心任务：当天完全为空时，第一个可用的主题直接采纳
            if not today_tasks and not result["selected"]:
                task = self._create_task_from_topic(topic, date_str)
                result["generated"].append(task)
                result["selected"].append(topic.id)
                remaining -= topic.estimated_minutes
                continue
            if topic.estimated_minutes > remaining:
                result["skipped_budget"].append(topic.id)
                continue
            task = self._create_task_from_topic(topic, date_str)
            result["generated"].append(task)
            result["selected"].append(topic.id)
            remaining -= topic.estimated_minutes

        return result

    def _create_task_from_topic(self, topic, date_str: str):
        """把一个主题落成 tasks 表中的一条任务。"""
        from .task_service import TaskService

        # 直接走 repository（等价于 TaskService.create_task 的底层），
        # 保留 source='generated' 与 topic_id 关联，且与 TaskService 兼容。
        return self.repo.create(
            title=topic.name,
            scheduled_date=date_str,
            description=topic.description,
            category="学习",
            estimated_minutes=topic.estimated_minutes,
            priority=topic.priority,
            source="generated",
            topic_id=topic.id,
        )

    def _done_topic_ids(self) -> set[int]:
        """返回所有已完成过的主主题 id。"""
        rows = self.repo.conn.execute(
            "SELECT DISTINCT topic_id FROM tasks WHERE status = ? AND topic_id IS NOT NULL",
            (STATUS_DONE,),
        ).fetchall()
        return {r["topic_id"] for r in rows}
