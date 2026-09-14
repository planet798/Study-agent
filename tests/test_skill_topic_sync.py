"""skill↔topic 映射补齐 + 幂等 sync 测试。

覆盖：10 个真实映射、sync 幂等/存量补链/不重复、coverage≠mastery、
不建 assessment、RAG/Agent/Ranking 不提前解锁、无环、今日仍生成 Embedding。
"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService

TODAY = "2026-09-14"

TOPIC_NAMES = [
    "Tokenizer 与分词", "RoPE 位置编码", "KV Cache",
    "generate / sampling 解码策略", "Qwen / LLaMA 架构：GQA / SwiGLU",
    "Hugging Face Transformers", "SFT 指令微调", "LoRA / QLoRA",
    "Docker", "vLLM 与 PagedAttention", "VLM / 多模态基础",
    "DDP / ZeRO / DeepSpeed（先理解）", "C/C++ / CUDA（方向确定后深入）",
    "推理优化基础", "Embedding 与向量检索", "RAG 全流程搭建",
    "Reranker 重排序", "RRF 排序融合", "Agent 实现与多步编排",
    "Evaluation / Badcase / LLM-as-Judge",
    "PyTorch 主题", "Transformer 主题",
]

EXPECTED = {
    "LLM 基础": ["Tokenizer 与分词", "RoPE 位置编码", "KV Cache",
                 "generate / sampling 解码策略",
                 "Qwen / LLaMA 架构：GQA / SwiGLU"],
    "Hugging Face": ["Hugging Face Transformers"],
    "SFT": ["SFT 指令微调"],
    "LoRA / QLoRA": ["LoRA / QLoRA"],
    "Docker": ["Docker"],
    "vLLM": ["vLLM 与 PagedAttention"],
    "VLM": ["VLM / 多模态基础"],
    "分布式训练底层": ["DDP / ZeRO / DeepSpeed（先理解）"],
    "CUDA": ["C/C++ / CUDA（方向确定后深入）"],
    "复杂推理优化": ["推理优化基础"],
}

DEPS = {
    "Hugging Face": ["Python", "Transformer"],
    "LLM 基础": ["Python", "Transformer"],
    "SFT": ["Transformer", "LLM 基础", "Hugging Face"],
    "LoRA / QLoRA": ["PyTorch", "Transformer", "SFT"],
    "Embedding": ["Python", "PyTorch", "Transformer"],
    "RAG": ["LLM 基础", "Embedding"],
    "Recall": ["推荐系统基础", "Embedding"],
    "Ranking": ["推荐系统基础", "Recall"],
    "Agent": ["LLM 基础", "RAG"],
    "模型评估": ["LLM 基础", "Hugging Face"],
    "Docker": ["Linux"],
    "vLLM": ["LoRA / QLoRA", "Docker"],
}


def _env(conn, seed_links=False):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date="2026-09-01",
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date="2026-09-01",
                                   end_date="2026-11-30")
    topics = {}
    for i, name in enumerate(TOPIC_NAMES):
        topics[name] = plan_repo.create_topic(phase_id=phase.id, name=name,
                                              estimated_minutes=45,
                                              order_index=i)
    sr = SkillRepository(conn)
    for name in ("Python", "Linux", "PyTorch", "Transformer", "Git"):
        sr.upsert_by_name(name)
    sr.update(sr.get_by_name("Python")["id"], status="mastered")
    sr.update(sr.get_by_name("Linux")["id"], status="mastered")
    sr.update(sr.get_by_name("Git")["id"], status="mastered")
    for name, prereqs in DEPS.items():
        sr.upsert_by_name(name, prerequisites=prereqs)
    for name in ("推荐系统基础", "用户画像"):
        sr.upsert_by_name(name, prerequisites=["Python", "PyTorch"])
    for name in ("VLM", "分布式训练底层", "CUDA", "复杂推理优化"):
        sr.upsert_by_name(name)
    arepo = AssessmentRepository(conn)
    ss = SkillService(sr, plan_repo=plan_repo, assessment_repo=arepo)
    sps = StudyPlanService(TaskRepository(conn), plan_repo,
                           assessment_repo=arepo, skill_service=ss)
    if seed_links:
        ss.sync_skill_topic_links()
    return {"conn": conn, "plan_repo": plan_repo, "phase": phase, "topics": topics,
            "sr": sr, "arepo": arepo, "ss": ss, "sps": sps}


def _links(env, name):
    return set(env["sr"].get_by_name(name)["linked_topics"] or [])


class TestMapping:
    def test_all_real_mappings(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        for skill, topic_names in EXPECTED.items():
            expected_ids = {env["topics"][t].id for t in topic_names}
            assert _links(env, skill) == expected_ids, skill

    def test_no_course_skills_stay_unlinked(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        for skill in ("推荐系统基础", "Recall", "用户画像"):
            assert _links(env, skill) == set(), skill

    def test_sync_idempotent(self, conn):
        env = _env(conn)
        first = env["ss"].sync_skill_topic_links()
        assert first["added_count"] > 0
        second = env["ss"].sync_skill_topic_links()
        assert second["added_count"] == 0
        assert second["added"] == {}

    def test_sync_fills_existing_library(self, conn):
        env = _env(conn)  # 未 seed_links
        assert _links(env, "LLM 基础") == set()
        env["ss"].sync_skill_topic_links()
        assert len(_links(env, "LLM 基础")) == 5

    def test_no_duplicate_links(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        env["ss"].sync_skill_topic_links()
        env["ss"].sync_skill_topic_links()
        lt = env["sr"].get_by_name("LLM 基础")["linked_topics"]
        assert len(lt) == len(set(lt)) == 5

    def test_sync_does_not_modify_status_or_mastery(self, conn):
        env = _env(conn)
        before = {s["name"]: (s["status"], s["mastery_ref"])
                  for s in env["sr"].list_all()}
        env["ss"].sync_skill_topic_links()
        after = {s["name"]: (s["status"], s["mastery_ref"])
                 for s in env["sr"].list_all()}
        assert before == after
        assert env["arepo"].list_attempts() == []


class TestCoverageAndGate:
    def test_coverage_not_mastery(self, conn):
        env = _env(conn, seed_links=True)
        for t in ("Tokenizer 与分词", "RoPE 位置编码", "KV Cache",
                  "generate / sampling 解码策略",
                  "Qwen / LLaMA 架构：GQA / SwiGLU"):
            task = env["conn"] and env["sps"].repo.create(
                title=t, scheduled_date="2026-09-10", source="generated",
                topic_id=env["topics"][t].id)
            env["sps"].repo.mark_done(task.id)
        coverage = env["ss"].refresh_coverage()
        assert "LLM 基础" in coverage
        s = env["sr"].get_by_name("LLM 基础")
        assert s["status"] == "not_started"  # 未改成 mastered
        assert env["ss"]._mastery_for_skill(s) is None
        assert env["arepo"].list_attempts() == []

    def test_rag_needs_embedding(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        # 只让 LLM 基础可覆盖：其 topic 全部 done
        for t in EXPECTED["LLM 基础"]:
            task = env["sps"].repo.create(title=t, scheduled_date="2026-09-10",
                                          source="generated",
                                          topic_id=env["topics"][t].id)
            env["sps"].repo.mark_done(task.id)
        env["ss"].refresh_coverage()
        assert env["ss"].is_blocked(env["sr"].get_by_name("RAG")) is True
        assert "Embedding" in env["ss"].missing_prerequisites(
            env["sr"].get_by_name("RAG"))

    def test_agent_needs_rag(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        assert env["ss"].is_blocked(env["sr"].get_by_name("Agent")) is True
        assert "RAG" in env["ss"].missing_prerequisites(
            env["sr"].get_by_name("Agent"))

    def test_ranking_still_blocked_by_rec_base(self, conn):
        env = _env(conn)
        env["ss"].sync_skill_topic_links()
        missing = env["ss"].missing_prerequisites(env["sr"].get_by_name("Ranking"))
        assert "推荐系统基础" in missing
        assert "Recall" in missing

    def test_dependency_graph_acyclic(self, conn):
        env = _env(conn)
        skills = {s["name"]: s for s in env["sr"].list_all()}
        color = {n: 0 for n in skills}
        cycles = []

        def dfs(n, stack):
            color[n] = 1
            stack.append(n)
            for p in (skills[n]["prerequisites"] or []):
                if p not in skills:
                    continue
                if color[p] == 1:
                    cycles.append(stack[stack.index(p):] + [p])
                elif color[p] == 0:
                    dfs(p, stack)
            stack.pop()
            color[n] = 2

        for n in skills:
            if color[n] == 0:
                dfs(n, [])
        assert cycles == []


def _small_env(conn):
    """只含今天相关主题的最小环境：PyTorch/Transformer 已 done。"""
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date="2026-09-01",
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date="2026-09-01",
                                   end_date="2026-11-30")
    names = ["PyTorch 主题", "Transformer 主题", "Embedding 与向量检索",
             "RAG 全流程搭建", "Agent 实现与多步编排", "Reranker 重排序",
             "RRF 排序融合", "推荐系统基础 主题"]
    topics = {n: plan_repo.create_topic(phase_id=phase.id, name=n)
              for n in names}
    sr = SkillRepository(conn)
    for n in ("Python", "PyTorch", "Transformer", "Embedding", "RAG", "Agent",
              "Ranking", "推荐系统基础", "Recall"):
        sr.upsert_by_name(n)
    sr.update(sr.get_by_name("Python")["id"], status="mastered")
    sr.update(sr.get_by_name("Embedding")["id"],
              prerequisites=["PyTorch", "Transformer"])
    sr.update(sr.get_by_name("RAG")["id"], prerequisites=["Embedding"])
    sr.update(sr.get_by_name("Agent")["id"], prerequisites=["RAG"])
    sr.update(sr.get_by_name("Ranking")["id"],
              prerequisites=["推荐系统基础", "Recall"])
    sr.update(sr.get_by_name("推荐系统基础")["id"], prerequisites=["Python"])
    sr.update(sr.get_by_name("Recall")["id"], prerequisites=["推荐系统基础"])
    arepo = AssessmentRepository(conn)
    ss = SkillService(sr, plan_repo=plan_repo, assessment_repo=arepo)
    sps = StudyPlanService(TaskRepository(conn), plan_repo,
                           assessment_repo=arepo, skill_service=ss)
    ss.link_topic_by_name("PyTorch", "PyTorch 主题")
    ss.link_topic_by_name("Transformer", "Transformer 主题")
    ss.link_topic_by_name("Embedding", "Embedding 与向量检索")
    ss.link_topic_by_name("RAG", "RAG 全流程搭建")
    ss.link_topic_by_name("Agent", "Agent 实现与多步编排")
    ss.link_topic_by_name("Ranking", "Reranker 重排序")
    ss.link_topic_by_name("Ranking", "RRF 排序融合")
    # 推荐系统基础 / Recall 保持零 linked_topics（课程缺失）
    for n in ("PyTorch 主题", "Transformer 主题"):
        task = sps.repo.create(title=n, scheduled_date="2026-09-09",
                               source="generated", topic_id=topics[n].id)
        sps.repo.mark_done(task.id)
    return {"conn": conn, "sps": sps, "ss": ss, "sr": sr, "topics": topics,
            "phase": phase, "arepo": arepo}


class TestTodayStillWorks:
    def test_embedding_still_generated_and_no_phase_jump(self, conn):
        env = _small_env(conn)
        phase_before = env["sps"].get_current_phase(TODAY)
        res = env["sps"].generate_daily_tasks(TODAY)
        assert env["topics"]["Embedding 与向量检索"].id in res["selected"]
        # RAG/Agent/Ranking 不提前解锁
        assert env["topics"]["RAG 全流程搭建"].id not in res["selected"]
        assert env["topics"]["Agent 实现与多步编排"].id not in res["selected"]
        assert env["topics"]["Reranker 重排序"].id not in res["selected"]
        assert env["topics"]["RRF 排序融合"].id not in res["selected"]
        assert env["sps"].get_current_phase(TODAY).id == phase_before.id

    def test_gate_stepwise_after_embedding_done(self, conn):
        env = _small_env(conn)
        env["sps"].generate_daily_tasks(TODAY)
        emb_task = [t for t in env["sps"].repo.list_by_date(TODAY)] [0]
        env["sps"].repo.mark_done(emb_task.id)
        env["ss"].refresh_coverage()
        # Embedding 覆盖后 RAG 解锁；Agent 仍缺 RAG
        assert env["ss"].is_blocked(env["sr"].get_by_name("RAG")) is False
        assert env["ss"].is_blocked(env["sr"].get_by_name("Agent")) is True
