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
        """若不存在默认研一计划，则创建（幂等）。返回计划主体（不带阶段）。"""
        existing = self.plan_repo.get_active_plan()
        if existing is not None:
            self._default_plan_created = False
            return existing

        plan = self.plan_repo.create_plan(
            name="USTC AI 研一大厂算法路线",
            description=(
                "研一系统学习路线：从编程/工具基础，到数据科学、深度学习、"
                "Transformer、LLM、RAG、Agent，再到 AI 工程化与项目实践。"
            ),
            start_date="2026-09-01",
            end_date="2027-08-31",
        )
        self._seed_phases_and_topics(plan.id)
        self._default_plan_created = True
        return plan

    def _seed_phases_and_topics(self, plan_id: int) -> None:
        """写入 8 个阶段的默认主题（主题具体内容可后续逐步完善）。"""
        phases = [
            {
                "name": "Python + Linux + Git",
                "desc": "编程与开发环境基础",
                "start": "2026-09-01", "end": "2026-10-15",
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
                "name": "NumPy + Pandas + 数据处理",
                "desc": "数据处理与可视化入门",
                "start": "2026-10-16", "end": "2026-11-30",
                "priority": 3,
                "goals": "能完成数据的读取、清洗、分析与简单可视化",
                "topics": [
                    ("NumPy 数组运算与广播", 45, 3),
                    ("Pandas DataFrame 处理", 45, 3),
                    ("数据清洗与缺失值处理", 30, 2),
                    ("Matplotlib 基础可视化", 30, 1),
                ],
            },
            {
                "name": "PyTorch + 深度学习基础",
                "desc": "深度学习框架与核心概念",
                "start": "2026-12-01", "end": "2027-01-31",
                "priority": 3,
                "goals": "能搭建并训练基础神经网络",
                "topics": [
                    ("张量与自动求导", 45, 3),
                    ("线性层与多层感知机", 45, 3),
                    ("损失函数与优化器", 30, 2),
                    ("训练循环与评估流程", 45, 2),
                ],
            },
            {
                "name": "Transformer",
                "desc": "现代大模型的核心架构",
                "start": "2027-02-01", "end": "2027-03-31",
                "priority": 3,
                "goals": "理解并实现 Transformer 关键组件",
                "topics": [
                    ("自注意力机制原理", 45, 3),
                    ("多头注意力与位置编码", 45, 3),
                    ("编码器-解码器结构", 45, 2),
                ],
            },
            {
                "name": "LLM 基础",
                "desc": "大语言模型的工作原理与训练",
                "start": "2027-04-01", "end": "2027-05-15",
                "priority": 3,
                "goals": "理解大模型的训练、对其与解码生成",
                "topics": [
                    ("语言模型与预训练任务", 45, 3),
                    ("指令微调与 RLHF 简介", 45, 2),
                    ("Prompt 与解码策略", 30, 2),
                ],
            },
            {
                "name": "RAG",
                "desc": "检索增强生成与知识库",
                "start": "2027-05-16", "end": "2027-06-30",
                "priority": 3,
                "goals": "能搭建一个可用的 RAG 应用",
                "topics": [
                    ("向量检索与嵌入", 45, 3),
                    ("RAG 全流程搭建", 60, 3),
                ],
            },
            {
                "name": "Agent",
                "desc": "智能体与工具调用",
                "start": "2027-07-01", "end": "2027-08-15",
                "priority": 3,
                "goals": "理解 Agent 架构并实现简单 Agent",
                "topics": [
                    ("Agent 架构与思维链", 45, 3),
                    ("工具调用与函数式接口", 45, 3),
                    ("多步任务编排", 45, 2),
                ],
            },
            {
                "name": "AI 工程化 + 项目",
                "desc": "部署、优化与综合项目",
                "start": "2027-08-16", "end": "2027-08-31",
                "priority": 2,
                "goals": "完成可展示的完整项目并掌握工程化要点",
                "topics": [
                    ("模型推理与部署", 60, 3),
                    ("综合项目开发", 60, 3),
                    ("性能优化与评测", 30, 1),
                ],
            },
        ]
        for ph in phases:
            phase = self.plan_repo.create_phase(
                plan_id=plan_id,
                name=ph["name"],
                description=ph["desc"],
                start_date=ph["start"],
                end_date=ph["end"],
                priority=ph["priority"],
                goals=ph["goals"],
            )
            for idx, (name, minutes, priority) in enumerate(ph["topics"]):
                self.plan_repo.create_topic(
                    phase_id=phase.id,
                    name=name,
                    description=name,
                    estimated_minutes=minutes,
                    priority=priority,
                    order_index=idx,
                )

    # ================= 当前阶段 =================

    def get_active_plan_full(self) -> StudyPlan | None:
        """获取 active 计划并加载全部阶段及主题。"""
        plan = self.plan_repo.get_active_plan()
        if plan is None:
            return None
        return self.plan_repo.get_plan_with_phases(plan.id)

    def get_current_phase(self, date_str: str):
        """根据日期返回当前 StudyPhase；无匹配返回 None。

        规则：start_date <= date <= end_date（含边界）。
        """
        plan = self.get_active_plan_full()
        if plan is None:
            return None
        for phase in plan.phases:
            if phase.start_date <= date_str <= phase.end_date:
                return phase
        return None

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
