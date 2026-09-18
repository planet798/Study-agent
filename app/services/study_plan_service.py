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
from ..database.schema import STATUS_ACTIVE, STATUS_CANCELLED, STATUS_DONE
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
        "name": "阶段四：推荐 / 搜索系统基础",
        "desc": "搜广推基础链路：召回 / 排序 / 重排 / 特征 / 评估",
        "start": "2027-04-01", "end": "2027-06-30",
        "priority": 2,
        "goals": "能从数据到召回/排序/重排跑通推荐系统基础链路，并做离线评估与 badcase 分析",
        "topics": [
            ("SQL 数据分析基础", 45, 2),
            ("推荐系统整体架构", 60, 3),
            ("协同过滤基础", 60, 2),
            ("Embedding Recall / 向量召回", 60, 2),
            ("双塔召回 Two-Tower", 60, 2),
            ("多路召回与 Candidate Generation", 60, 2),
            ("Ranking 基础", 60, 3),
            ("CTR 预估基础", 60, 2),
            ("Wide & Deep / DeepFM 基础", 60, 2),
            ("推荐系统评估指标", 45, 2),
            ("Rerank / 重排基础", 45, 2),
            ("用户画像与特征工程", 45, 2),
            ("推荐系统 Badcase 分析", 45, 2),
            ("LLM + Recommendation 基础", 60, 2),
        ],
    },
    {
        "name": "阶段五：模型训练与部署",
        "desc": "高效微调与生产部署",
        "start": "2027-07-01", "end": "2027-08-31",
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
        "name": "阶段六：后续扩展",
        "desc": "多模态与推理优化（按方向选择深入）",
        "start": "2027-09-01", "end": "2027-10-31",
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

# 新增「阶段四：推荐 / 搜索系统基础」后，把旧的后两个阶段改名（保留 phase_id/topics）。
_LEGACY_PHASE_RENAMES = {
    "阶段四：模型训练与部署": "阶段五：模型训练与部署",
    "阶段五：后续扩展": "阶段六：后续扩展",
}


class StudyPlanService:
    def __init__(
        self,
        repo: TaskRepository,
        plan_repo: StudyPlanRepository | None = None,
        max_daily_minutes: int = MAX_DAILY_STUDY_MINUTES,
        assessment_repo=None,
        skill_service=None,
        route_id: int | None = None,
        learning_route_repo=None,
        scope_tasks_by_route: bool = False,
    ):
        self.repo = repo
        self.plan_repo = plan_repo or StudyPlanRepository(repo.conn)
        # 允许测试/日后调整预算
        self.max_daily_minutes = max_daily_minutes
        # 可选：读到知识掌握证据，用于“薄弱优先 / 高掌握与复习中不重复”（Phase 8）
        self.assessment_repo = assessment_repo
        # 可选：SkillService，用于“JD/技能优先级 + 前置门禁”（Phase C）；
        # 不注入时行为与旧版完全一致（无 jd_boost / 无 gate）
        self.skill_service = skill_service
        # Phase B：本 Service 服务的学习路线。route_id=None 时解析系统默认
        # learning route（搜广推 + LLM）；路由数据缺失时回退旧行为。
        self.route_id = route_id
        self.learning_route_repo = learning_route_repo
        # Phase D：多路线 Scheduler 创建的 route-scoped 实例置 True，
        # 使“当天已有任务 / 预算 / 最近任务”只统计本路线，避免跨路线干扰。
        self.scope_tasks_by_route = scope_tasks_by_route
        self._resolved_route_id_cache: int | None = None
        self._route_resolved = False
        self._default_plan_created = False

    def _resolved_route_id(self) -> int | None:
        """解析本 Service 服务的 route_id（显式优先，其次系统默认 learning route）。"""
        if self.route_id is not None:
            return self.route_id
        if self._route_resolved:
            return self._resolved_route_id_cache
        self._route_resolved = True
        repo = self.learning_route_repo
        if repo is None:
            try:
                from ..database.learning_route_repository import (
                    LearningRouteRepository,
                )

                repo = LearningRouteRepository(self.repo.conn)
            except Exception:  # noqa: BLE001 - 无路线表时不阻塞旧行为
                repo = None
        if repo is not None:
            try:
                default = repo.get_default_learning_route()
                self._resolved_route_id_cache = (
                    default.id if default is not None else None
                )
            except Exception:  # noqa: BLE001
                self._resolved_route_id_cache = None
        return self._resolved_route_id_cache

    def is_planning_enabled(self) -> bool:
        """当前路线的自动规划是否启用。

        - 未绑定路线（旧行为）→ True，保持兼容；
        - 路线 archived 或 planning_enabled=0 → False（暂停/归档不生成新的 Agent task）。
        """
        route_id = self._resolved_route_id()
        if route_id is None:
            return True
        repo = self.learning_route_repo
        if repo is None:
            try:
                from ..database.learning_route_repository import (
                    LearningRouteRepository,
                )

                repo = LearningRouteRepository(self.repo.conn)
            except Exception:  # noqa: BLE001
                return True
        try:
            route = repo.get(route_id)
        except Exception:  # noqa: BLE001
            return True
        if route is None:
            return True
        return bool(route.planning_enabled) and route.status == "active"

    # ================= 默认研一计划 =================

    def ensure_default_plan(self) -> StudyPlan:
        """确保默认计划存在并与长期学习路线一致（幂等）。

        首次运行：创建计划 + 全量种子。
        之后每次运行：对已有计划就地同步（更新/补齐阶段与主题，并移除
        没有任务引用的过期阶段），绝不触碰历史任务。
        """
        existing = self.plan_repo.get_active_plan(
            route_id=self._resolved_route_id()
        )
        if existing is None:
            self._default_plan_created = True
            return self._create_default_plan()
        self._default_plan_created = False
        self._reconcile_plan(existing)
        return (
            self.plan_repo.get_active_plan(route_id=self._resolved_route_id())
            or existing
        )

    def _create_default_plan(self) -> StudyPlan:
        plan = self.plan_repo.create_plan(
            name="USTC AI 研一大厂算法路线",
            description=(
                "基于真实 JD 与 docs/career_context.json 的长期算法学习路线："
                "工程底座 → 深度学习与 LLM 基础 → LLM 应用 → 推荐 / 搜索系统基础 "
                "→ 模型训练与部署 → 扩展。"
            ),
            start_date="2026-09-01",
            end_date="2027-08-31",
            route_id=self._resolved_route_id(),
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
        - 先把旧阶段名（阶段四/五）改名为阶段五/六，保留 phase_id 与 topics；
        - 移除“预期之外”且没有任何任务引用的过期阶段（如旧的八阶段计划）。
        """
        # 0) 历史阶段改名（幂等；保留原 phase_id / topics / tasks）
        for old_name, new_name in _LEGACY_PHASE_RENAMES.items():
            old = self._find_phase_by_name(plan.id, old_name)
            if old is not None and self._find_phase_by_name(
                plan.id, new_name
            ) is None:
                self.plan_repo.update_phase(old.id, name=new_name)

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
        """获取本 Service 所服务路线的 active 计划（含阶段与主题）。

        route 隔离：只加载本路线（或兼容旧数据的未绑定计划）的 active plan，
        绝不会把其它路线的 phases/topics 混进来。
        """
        plan = self.plan_repo.get_active_plan(route_id=self._resolved_route_id())
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

    def generate_daily_tasks(self, date_str: str, max_tasks: int | None = None) -> dict:
        """为 date_str 生成每日学习任务（不使用 LLM，规则简单可预测）。

        规则（Phase 8 起）：
        1. 当天已存在的任务占用预算（延期任务优先）；
        2. 高优先级主题优先；薄弱（低掌握或有 weak_points）主题按证据提前；
        3. 已经完成过的主题不再重复生成；高掌握且最近验收良好、或有未完成复习任务
           的主题不再重复安排（复习交给 ReviewService，不伪造 mastery）；
        4. 不超过 max_daily_minutes 总预算；
        5. 若当天还没有任何任务，至少安排一个核心主题；
        6. 剩余预算装不下剩余主题时停止。

        :param max_tasks: Phase D：本次最多创建多少个任务（Scheduler 每个 slot
            调用一次，传 1）；None 表示不限制（保持旧行为）。
        :return: {"generated": [Task], "phase": name|None, "selected": [topic_id], ...}
        """
        result = {
            "phase": None,
            "generated": [],
            "selected": [],
            "skipped_done": [],
            "skipped_duplicate": [],
            "skipped_budget": [],
            "skipped_gate": [],
            "skipped_cancelled": [],
        }
        # Phase C：暂停/归档路线的自动规划入口不生成新 task
        if not self.is_planning_enabled():
            return result
        phase = self.get_current_phase(date_str)
        if phase is None:
            return result
        result["phase"] = phase.name

        # 今天已有的任务（含延期进来的）；多路线模式下只看本路线
        today_tasks = self.repo.list_by_date(date_str)
        scope_route = self._resolved_route_id() if self.scope_tasks_by_route else None
        if scope_route is not None:
            today_tasks = [
                t for t in today_tasks if t.route_id == scope_route
            ]
        active_topic_ids = {
            t.topic_id
            for t in today_tasks
            if t.topic_id is not None and t.status == STATUS_ACTIVE
        }
        # 任何非 cancelled、带 topic 的当天任务（含 manual / done / 延期）
        # 都视为该 topic 今天已被占用，Scheduler 不得重复生成。
        occupied_topic_ids = {
            t.topic_id
            for t in today_tasks
            if t.topic_id is not None and t.status != STATUS_CANCELLED
        }
        # 用户当天主动移除（cancelled）的 topic：今天不再重新安排；
        # 只对“当天”生效，次日的候选集不受影响。
        cancelled_topic_ids = {
            t.topic_id
            for t in today_tasks
            if t.topic_id is not None and t.status == STATUS_CANCELLED
        }
        committed = sum(
            t.estimated_minutes
            for t in today_tasks
            if t.status == STATUS_ACTIVE
        )
        remaining = self.max_daily_minutes - committed

        # 已完成的主题列表（任意日期完成过即视为已掌握）
        done_topic_ids = self._done_topic_ids()

        # 知识掌握证据（Phase 8；无 assessment_repo 时为空，行为与旧版一致）
        weak_ids = set()
        skip_ids = set()
        if self.assessment_repo is not None:
            from .knowledge_evidence import skip_topic_ids, weak_topic_ids

            weak_ids = weak_topic_ids(self.repo, self.assessment_repo)
            skip_ids = skip_topic_ids(self.repo, self.assessment_repo)

        # 技能视图（Phase C；仅在有 skill_service 时生效）
        # blocked_ids : 前置未满足 → 绝不生成（skipped_gate）
        # jd_boost    : topic_id -> 关联技能的 JD 频次强度（0~1，max over skills）
        blocked_ids, jd_boost = self.skill_topic_views(phase.topics, date_str)

        # 优先级排序（Phase C）：
        # 1. 前置满足（gate ok 在前） 2. 薄弱点 3. JD must/plus 4. 主题优先级 5. 原始顺序
        topics = sorted(
            phase.topics,
            key=lambda t: (
                int(t.id in blocked_ids),
                -int(t.id in weak_ids),
                -jd_boost.get(t.id, 0.0),
                -t.priority,
                t.order_index,
            ),
        )

        for topic in topics:
            if max_tasks is not None and len(result["generated"]) >= max_tasks:
                result["skipped_budget"].append(topic.id)
                continue
            if topic.id in done_topic_ids or topic.id in skip_ids:
                result["skipped_done"].append(topic.id)
                continue
            if topic.id in blocked_ids:
                # 前置关键技能未满足：即使 JD 高频也不能生成
                result["skipped_gate"].append(topic.id)
                continue
            if topic.id in occupied_topic_ids or topic.id in active_topic_ids:
                result["skipped_duplicate"].append(topic.id)
                continue
            if topic.id in cancelled_topic_ids:
                # 当天被用户移除过的 topic：今天 replan / 回退生成都不要再安排
                result["skipped_cancelled"].append(topic.id)
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
        """把一个主题落成 tasks 表中的一条任务（description 用可执行学习内容）。"""
        from .task_content import build_topic_task_content
        from .task_service import TaskService

        # 直接走 repository（等价于 TaskService.create_task 的底层），
        # 保留 source='generated' 与 topic_id 关联，且与 TaskService 兼容。
        task = self.repo.create(
            title=topic.name,
            scheduled_date=date_str,
            description=build_topic_task_content(topic.name, topic.description),
            category="学习",
            estimated_minutes=topic.estimated_minutes,
            priority=topic.priority,
            source="generated",
            topic_id=topic.id,
            route_id=self.plan_repo.get_route_id_for_topic(topic.id)
            or self._resolved_route_id(),
        )
        # 新建正式任务即建立 topic -> knowledge_point -> task 关联（幂等）
        return self.link_task_knowledge_point(task, topic)

    def _done_topic_ids(self) -> set[int]:
        """返回所有已完成过的主主题 id。"""
        rows = self.repo.conn.execute(
            "SELECT DISTINCT topic_id FROM tasks WHERE status = ? AND topic_id IS NOT NULL",
            (STATUS_DONE,),
        ).fetchall()
        return {r["topic_id"] for r in rows}

    # ================= 技能视图（Phase C） =================

    def skill_topic_views(
        self, topics, end_date: str | None = None
    ) -> tuple[set[int], dict[int, float]]:
        """按主题计算技能维度信息（仅当注入 skill_service 时不为空）。

        :return: (blocked_topic_ids, jd_boost)
        - blocked_ids : 关联技能被前置门禁阻塞的主题（绝不生成）
        - jd_boost    : topic_id -> 关联技能的市场需求因子最大值（0~1）

        Step 6：jd_boost 改用 SkillService.market_factor（Daily Summary 优先，
        individual JD fallback），使规则 fallback 同样利用近期市场信号。
        """
        blocked: set[int] = set()
        jd_boost: dict[int, float] = {}
        if self.skill_service is None:
            return blocked, jd_boost
        if end_date is not None:
            try:
                self.skill_service.refresh_market(end_date)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.skill_service.refresh_coverage()
        except Exception:  # noqa: BLE001
            pass
        for t in topics:
            names = self.skill_service.skills_for_topic(t.id)
            if not names:
                continue
            skill_blocked = False
            boost = 0.0
            for name in names:
                skill = self.skill_service.skill_repo.get_by_name(name)
                if skill is None:
                    continue
                detail = self.skill_service.compute_skill_score(skill)
                if detail.get("gate") == "blocked":
                    skill_blocked = True
                boost = max(
                    boost, self.skill_service.market_factor(skill)
                )
            if skill_blocked:
                blocked.add(t.id)
            jd_boost[t.id] = boost
        return blocked, jd_boost

    # ================= 存量任务 description 回填（一次性修复） =================

    def repair_existing_task_descriptions(self) -> int:
        """把历史“生成型新任务”里占位/过短的 description 幂等回填为结构化学习内容。

        只处理满足全部条件的任务：
        - source = 'generated' 且 task_type = 'new'
        - 未完成（status != 'done'，即 active / not_done）
        - topic_id 非空且能找到对应 study_topic
        - description 尚不具备“可执行内容”（名型占位 / 合格检查 / 过短）

        写明规则：本方法只更新 description，不改 title / status /
        scheduled_date / estimated_minutes / task_type / topic_id / source。
        幂等：修复后 description 即具备可执行内容，下次调用不再改动。
        单条失败不影响其它任务，也不影响启动。
        :return: 本次实际修复的条数
        """
        from ..utils.date_utils import now_iso
        from .task_content import build_topic_task_content, has_actionable_content

        rows = self.repo.conn.execute(
            "SELECT * FROM tasks WHERE source = 'generated' AND task_type = 'new'"
        ).fetchall()
        repaired = 0
        for row in rows:
            if row["status"] == "done":
                continue
            topic_id = row["topic_id"]
            if topic_id is None:
                continue
            try:
                topic = self.plan_repo.conn.execute(
                    "SELECT id, name, description FROM study_topics WHERE id = ?",
                    (topic_id,),
                ).fetchone()
                if topic is None:
                    continue
                desc = (row["description"] or "").strip()
                # 1) 已经是完整可执行内容 → 不动（避免重复生成）
                if has_actionable_content(desc):
                    continue
                # 2) 占位型（= 主题名/主题描述）或明显过短的短文本 → 回填
                is_placeholder = (
                    not desc
                    or desc == (topic["name"] or "").strip()
                    or desc == (topic["description"] or "").strip()
                )
                if not is_placeholder and len(desc) >= 80:
                    # 已有较长的、人工维护过的描述 → 不覆盖
                    continue
                content = build_topic_task_content(
                    topic["name"], topic["description"]
                )
                self.repo.conn.execute(
                    "UPDATE tasks SET description = ?, updated_at = ? "
                    "WHERE id = ?",
                    (content, now_iso(), row["id"]),
                )
                repaired += 1
            except Exception:  # noqa: BLE001 - 单条失败不影响其它任务与启动
                continue
        self.repo.conn.commit()
        return repaired

    # ================= topic -> knowledge_point 关联（Review 链路修复） =================

    # 允许建立 topic -> kp 关联的任务种类（source, task_type）
    # manual/new：用户手动添加的“正式知识学习任务”（关联已有 topic）
    _LINKABLE_TASK_KINDS = {
        ("generated", "new"),
        ("extra", "extra"),
        ("manual", "new"),
    }

    def link_task_knowledge_point(self, task, topic=None):
        """把一个正式新知识任务 / 额外任务幂等关联到其 topic 的唯一知识点。

        这是 topic -> knowledge_point -> task 的**唯一实现**（AI path、
        fallback path 与 ExtraTaskService 都复用它，不各写一套）。

        约束：
        - 只处理 (source,task_type) ∈ {('generated','new'), ('extra','extra'),
          ('manual','new')}；
        - 只处理 topic_id 非空且能查到 topic 的任务；
        - 只写 tasks.knowledge_point_id（以及 updated_at），其它字段一律不动；
        - 幂等：已有 knowledge_point_id 直接返回，不重复建 kp；
        - 绝不伪造验收证据（kp 的 mastery/复习字段保持原样）。
        """
        if task is None:
            return task
        # Phase B：正式任务若缺 route，从 topic 推导回填（幂等）
        if task.route_id is None and task.topic_id is not None:
            route_id = self.plan_repo.get_route_id_for_topic(task.topic_id)
            if route_id is not None:
                from ..utils.date_utils import now_iso

                self.repo.conn.execute(
                    "UPDATE tasks SET route_id = ?, updated_at = ? WHERE id = ?",
                    (route_id, now_iso(), task.id),
                )
                self.repo.conn.commit()
                task = self.repo.get(task.id)
        if task.knowledge_point_id is not None:
            return task
        # 正式新知识任务与额外任务都可关联知识点（manual/review 不在此列）
        if (task.source, task.task_type) not in self._LINKABLE_TASK_KINDS:
            return task
        if task.topic_id is None or self.assessment_repo is None:
            return task
        if topic is None:
            topic = self.plan_repo.get_topic(task.topic_id)
        if topic is None:
            return task
        from ..utils.date_utils import now_iso
        from .task_content import build_topic_task_content

        description = (getattr(topic, "description", "") or "") or \
            build_topic_task_content(topic.name, "")
        topic_route_id = self.plan_repo.get_route_id_for_topic(topic.id)
        kp = self.assessment_repo.get_or_create_knowledge_point_for_topic(
            topic.id, topic.name, description, route_id=topic_route_id
        )
        if kp is None:
            return task
        # 同步 route（task 与 kp 一致）
        new_route_id = task.route_id or topic_route_id
        self.repo.conn.execute(
            "UPDATE tasks SET knowledge_point_id = ?, route_id = ?, updated_at = ? "
            "WHERE id = ?",
            (kp["id"], new_route_id, now_iso(), task.id),
        )
        self.repo.conn.commit()
        return self.repo.get(task.id)

    def repair_task_knowledge_points(self) -> dict:
        """启动时修复历史任务：为 generated/new 且有 topic_id 的任务补 kp 关联。

        只补“关系”，不补“证据”：不会创建 assessment、不会设置 mastery、
        不会因为任务 done 就推断“已掌握”。

        处理条件（全部满足）：
        - source='generated'
        - task_type='new'
        - topic_id IS NOT NULL
        - knowledge_point_id IS NULL

        :return: {"repaired": int, "skipped": int, "error": int}
        """
        rows = self.repo.conn.execute(
            "SELECT * FROM tasks WHERE source = 'generated' AND task_type = 'new'"
        ).fetchall()
        repaired = skipped = errors = 0
        for row in rows:
            if row["knowledge_point_id"] is not None:
                skipped += 1
                continue
            if row["topic_id"] is None:
                skipped += 1
                continue
            try:
                task = self.repo.get(row["id"])
                before = task.knowledge_point_id
                after = self.link_task_knowledge_point(task)
                if after is not None and after.knowledge_point_id != before \
                        and after.knowledge_point_id is not None:
                    repaired += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001 - 单条失败不影响其它任务与启动
                errors += 1
                continue
        return {"repaired": repaired, "skipped": skipped, "error": errors}
