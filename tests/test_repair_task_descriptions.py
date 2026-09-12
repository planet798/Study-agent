"""存量生成型任务 description 幂等回填（一次性修复）测试。

对应要求（11 项）：
1 旧 description=主题名 → 可成功回填
2 回填后 description 具备 actionable content
3 已有详细 description 的任务不会被覆盖
4 done 任务不会被回填
5 review / extra / manual 不会被回填
6 没有 topic_id 的任务不会报错
7 找不到 topic 的任务不会报错
8 重复执行不会继续修改（幂等）
9 repair 失败不会阻止启动
10 任务其它字段全部保持不变
11 2026-09-12 的三个旧任务可正确修复
"""

from __future__ import annotations

import pytest

from app.database.repository import TaskRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.study_plan_service import StudyPlanService
from app.services.task_content import has_actionable_content

PHASE_START = "2026-09-01"
TODAY = "2026-09-12"

# 三个真实问题主题
TOPIC_NAMES = ("Function Calling / Tool Calling",
               "ReAct 推理与行动",
               "Planning 任务规划")

# 一条“较长、人工维护过、但没有标准标记”的描述（如任务 id=19 那般）
LONG_MANUAL_DESC = (
    "先理解 LLaMA / Qwen 的架构组成（RMSNorm 前置、RoPE、GQA、SwiGLU FFN、"
    "Pre-Norm 残差），再动手：1) 用 nn.Module 实现一个简化 SwiGLU FFN"
    "（gate/up/down 三层，hidden 维度按 8/3 比例缩放）；2) 实现 GQA 的 KV "
    "头分组（如 32 个 Q 头 / 8 个 KV 头），对比 MHA 的参数量与 KV Cache "
    "显存占用差异；3) 对照 KV Cache 与 RoPE 已学内容，写出总结笔记：GQA "
    "为什么能在推理阶段省显存。目标：能独立讲清 Qwen/LLaMA 单层结构框图。"
)

ACTIONABLE_DESC = (
    "【学习目标】\n跑通 Tool Calling 闭环\n\n"
    "【具体学习事项】\n1. 理解数据流\n2. 实现 calculator tool\n"
    "3. 调用模型\n\n【实践】\n写最小 demo\n\n"
    "【完成标准】\n- 能画数据流\n- 能跑 demo\n- 能改 tool\n"
    "【客观验收】\n- 运行 demo\n"
)


def _build(conn):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="m", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="mini",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    topics = {}
    for i, name in enumerate(TOPIC_NAMES):
        topics[name] = plan_repo.create_topic(phase_id=phase.id, name=name,
                                              estimated_minutes=60,
                                              priority=3, order_index=i)
    repo = TaskRepository(conn)
    sps = StudyPlanService(repo, plan_repo)
    return {"plan_repo": plan_repo, "repo": repo, "sps": sps,
            "topics": topics}


def _mk(repo, *, title, scheduled_date=TODAY, description="",
        source="generated", task_type="new", topic_id=None, **kw):
    return repo.create(
        title=title, scheduled_date=scheduled_date, description=description,
        source=source, task_type=task_type, topic_id=topic_id, **kw,
    )


class TestRepair:
    def test_backfill_old_placeholder(self, conn):
        env = _build(conn)
        t = env["topics"]["Function Calling / Tool Calling"]
        task = _mk(env["repo"], title=t.name, description=t.name,
                   topic_id=t.id)
        assert has_actionable_content(task.description) is False
        n = env["sps"].repair_existing_task_descriptions()
        assert n >= 1
        got = env["repo"].get(task.id)
        assert has_actionable_content(got.description) is True
        assert got.description != t.name

    def test_repaired_has_all_sections(self, conn):
        env = _build(conn)
        topics = {name: tid for name, tid in
                  ((name, env["topics"][name].id) for name in TOPIC_NAMES)}
        for name, tid in topics.items():
            _mk(env["repo"], title=name, description=name, topic_id=tid)
        env["sps"].repair_existing_task_descriptions()
        rows = env["repo"].conn.execute(
            "SELECT title, description FROM tasks").fetchall()
        assert len(rows) == 3
        for row in rows:
            for sec in ("【学习目标】", "【具体学习事项】", "【实践】",
                        "【完成标准】"):
                assert sec in row["description"], f"{row['title']} 缺 {sec}"

    def test_manual_long_description_not_overwritten(self, conn):
        env = _build(conn)
        t = env["topics"]["Function Calling / Tool Calling"]
        task = _mk(env["repo"], title="Qwen/LLaMA 架构",
                   description=LONG_MANUAL_DESC, topic_id=t.id)
        n = env["sps"].repair_existing_task_descriptions()
        # 不清楚它的格式，但它是“已有详细人工内容”→ 不覆盖
        assert env["repo"].get(task.id).description == LONG_MANUAL_DESC

    def test_already_actionable_description_not_touched(self, conn):
        env = _build(conn)
        t = env["topics"]["Function Calling / Tool Calling"]
        task = _mk(env["repo"], title=t.name, description=ACTIONABLE_DESC,
                   topic_id=t.id)
        env["sps"].repair_existing_task_descriptions()
        assert env["repo"].get(task.id).description == ACTIONABLE_DESC

    def test_done_task_not_repaired(self, conn):
        env = _build(conn)
        t = env["topics"]["Function Calling / Tool Calling"]
        task = _mk(env["repo"], title=t.name, description=t.name,
                   topic_id=t.id)
        env["repo"].mark_done(task.id)  # -> done
        env["sps"].repair_existing_task_descriptions()
        got = env["repo"].get(task.id)
        assert got.description == t.name  # done 不回填
        assert got.status == "done"

    def test_review_extra_manual_skipped(self, conn):
        env = _build(conn)
        t = env["topics"]["ReAct 推理与行动"]
        ids = []
        ids.append(_mk(env["repo"], title="复习 ReAct", description=t.name,
                       source="review", task_type="review",
                       knowledge_point_id=1).id)
        ids.append(_mk(env["repo"], title="【额外】ReAct",
                       description=t.name, source="extra", task_type="extra",
                       difficulty="practice").id)
        ids.append(_mk(env["repo"], title="手动 ReAct", description=t.name,
                       source="manual", task_type="new").id)
        env["sps"].repair_existing_task_descriptions()
        for tid in ids:
            assert env["repo"].get(tid).description == t.name

    def test_no_topic_id_no_error(self, conn):
        env = _build(conn)
        task = _mk(env["repo"], title="无主题任务", description="学习方法")
        # 不抛异常
        n = env["sps"].repair_existing_task_descriptions()
        assert n == 0
        assert env["repo"].get(task.id).description == "学习方法"

    def test_missing_topic_no_error(self, conn):
        env = _build(conn)
        task = _mk(env["repo"], title="孤儿任务", description="x",
                   topic_id=99999)
        n = env["sps"].repair_existing_task_descriptions()
        assert n == 0  # topic 找不到 → 跳过，不报错
        assert env["repo"].get(task.id).description == "x"

    def test_idempotent(self, conn):
        env = _build(conn)
        t = env["topics"]["Planning 任务规划"]
        task = _mk(env["repo"], title=t.name, description=t.name,
                   topic_id=t.id)
        first = env["sps"].repair_existing_task_descriptions()
        assert first >= 1
        desc1 = env["repo"].get(task.id).description
        second = env["sps"].repair_existing_task_descriptions()
        assert second == 0
        assert env["repo"].get(task.id).description == desc1

    def test_repair_failure_does_not_raise(self, conn, monkeypatch):
        import app.services.task_content as content_mod

        env = _build(conn)
        t = env["topics"]["Function Calling / Tool Calling"]
        _mk(env["repo"], title=t.name, description=t.name, topic_id=t.id)

        def boom(*a, **k):
            raise RuntimeError("builder broken")

        monkeypatch.setattr(content_mod, "build_topic_task_content", boom)
        # 单条失败被吞掉，不抛异常、不阻止“启动”流程
        assert env["sps"].repair_existing_task_descriptions() == 0

    def test_other_fields_unchanged(self, conn):
        env = _build(conn)
        t = env["topics"]["ReAct 推理与行动"]
        task = _mk(env["repo"], title=t.name, description=t.name,
                   scheduled_date="2026-09-12", estimated_minutes=45,
                   priority=2, topic_id=t.id)
        before = env["repo"].get(task.id)
        env["sps"].repair_existing_task_descriptions()
        after = env["repo"].get(task.id)
        assert before.title == after.title
        assert before.status == after.status
        assert before.scheduled_date == after.scheduled_date
        assert before.estimated_minutes == after.estimated_minutes
        assert before.task_type == after.task_type
        assert before.topic_id == after.topic_id
        assert before.source == after.source
        assert before.priority == after.priority
        assert after.description != before.description  # 只有 description 变

    def test_three_stale_tasks_repaired(self, conn):
        """2026-09-12 三个旧任务：正确修复为统一结构化内容。"""
        env = _build(conn)
        for name in TOPIC_NAMES:
            t = env["topics"][name]
            _mk(env["repo"], title=t.name, description=t.name, topic_id=t.id,
                scheduled_date="2026-09-12")
        env["sps"].repair_existing_task_descriptions()
        rows = env["repo"].conn.execute(
            "SELECT title, description FROM tasks "
            "WHERE scheduled_date='2026-09-12' ORDER BY title").fetchall()
        assert len(rows) == 3
        for row in rows:
            assert has_actionable_content(row["description"]) is True
            assert "具体学习事项" in row["description"]
            assert "完成标准" in row["description"]
