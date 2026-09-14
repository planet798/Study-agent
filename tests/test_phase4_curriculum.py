"""阶段四「推荐 / 搜索系统基础」课程结构测试。"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import SkillRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.services.task_content import build_topic_task_content

TODAY = "2026-09-14"
NEW_PHASE = "阶段四：推荐 / 搜索系统基础"
NEW_TOPICS = [
    "SQL 数据分析基础", "推荐系统整体架构", "协同过滤基础",
    "Embedding Recall / 向量召回", "双塔召回 Two-Tower",
    "多路召回与 Candidate Generation", "Ranking 基础", "CTR 预估基础",
    "Wide & Deep / DeepFM 基础", "推荐系统评估指标", "Rerank / 重排基础",
    "用户画像与特征工程", "推荐系统 Badcase 分析", "LLM + Recommendation 基础",
]


def _env(conn, seed_skills=True):
    repo = TaskRepository(conn)
    prepo = StudyPlanRepository(conn)
    arepo = AssessmentRepository(conn)
    sr = SkillRepository(conn)
    ss = SkillService(sr, plan_repo=prepo, assessment_repo=arepo)
    sps = StudyPlanService(repo, prepo, assessment_repo=arepo,
                           skill_service=ss)
    sps.ensure_default_plan()
    if seed_skills and not sr.list_all():
        ss.seed_from_career_context()
        ss.sync_skill_topic_links()
    return {"conn": conn, "repo": repo, "prepo": prepo, "arepo": arepo,
            "sr": sr, "ss": ss, "sps": sps}


def _phase(env, name):
    plan = env["prepo"].get_active_plan()
    return next((p for p in env["prepo"].list_phases(plan.id)
                 if p.name == name), None)


def _topic(env, name):
    plan = env["prepo"].get_active_plan()
    for p in env["prepo"].list_phases(plan.id):
        for t in env["prepo"].list_topics(p.id):
            if t.name == name:
                return t
    return None


def _links(env, name):
    s = env["sr"].get_by_name(name)
    return set(s["linked_topics"] or []) if s else set()


class TestPhaseStructure:
    def test_new_phase_and_topics_created(self, conn):
        env = _env(conn)
        ph = _phase(env, NEW_PHASE)
        assert ph is not None
        names = [t.name for t in env["prepo"].list_topics(ph.id)]
        for n in NEW_TOPICS:
            assert n in names, n
        assert len(names) == 14

    def test_idempotent(self, conn):
        env = _env(conn)
        plan = env["prepo"].get_active_plan()
        before = {p.name: len(env["prepo"].list_topics(p.id))
                  for p in env["prepo"].list_phases(plan.id)}
        env["sps"].ensure_default_plan()
        env["sps"].ensure_default_plan()
        after = {p.name: len(env["prepo"].list_topics(p.id))
                 for p in env["prepo"].list_phases(plan.id)}
        assert before == after
        assert sum(1 for p in env["prepo"].list_phases(plan.id)
                   if p.name == NEW_PHASE) == 1

    def test_phase_order(self, conn):
        env = _env(conn)
        plan = env["prepo"].get_active_plan()
        names = [p.name for p in env["prepo"].list_phases(plan.id)]
        assert names.index("阶段三：LLM 应用") < names.index(NEW_PHASE)
        assert names.index(NEW_PHASE) < names.index("阶段五：模型训练与部署")
        assert names.index("阶段五：模型训练与部署") < names.index(
            "阶段六：后续扩展")

    def test_legacy_rename_keeps_ids_and_history(self, conn):
        repo, prepo = TaskRepository(conn), StudyPlanRepository(conn)
        plan = prepo.create_plan(name="p", start_date="2026-09-01",
                                 end_date="2027-08-31")
        old4 = prepo.create_phase(plan_id=plan.id, name="阶段四：模型训练与部署",
                                  start_date="2027-04-01",
                                  end_date="2027-06-30")
        t = prepo.create_topic(phase_id=old4.id, name="LoRA / QLoRA")
        task = repo.create(title="历史 LoRA 任务", scheduled_date="2027-04-05",
                           source="generated", topic_id=t.id)
        repo.mark_done(task.id)
        old5 = prepo.create_phase(plan_id=plan.id, name="阶段五：后续扩展",
                                  start_date="2027-07-01",
                                  end_date="2027-08-31")
        arepo = AssessmentRepository(conn)
        ss = SkillService(SkillRepository(conn), plan_repo=prepo,
                          assessment_repo=arepo)
        StudyPlanService(repo, prepo, assessment_repo=arepo,
                         skill_service=ss).ensure_default_plan()
        # phase_id / topic_id 保留
        assert prepo.get_phase(old4.id).name == "阶段五：模型训练与部署"
        assert prepo.get_phase(old5.id).name == "阶段六：后续扩展"
        assert _topic({"prepo": prepo}, "LoRA / QLoRA").id == t.id
        assert repo.get(task.id).status == "done"
        assert repo.get(task.id).topic_id == t.id


class TestMappings:
    def test_recsys_mappings(self, conn):
        env = _env(conn)
        expected = {
            "SQL": ["SQL 数据分析基础"],
            "推荐系统基础": ["推荐系统整体架构", "协同过滤基础"],
            "Recall": ["Embedding Recall / 向量召回", "双塔召回 Two-Tower",
                       "多路召回与 Candidate Generation"],
            "Ranking": ["Reranker 重排序", "RRF 排序融合", "Ranking 基础"],
            "CTR": ["CTR 预估基础", "Wide & Deep / DeepFM 基础"],
            "Rerank": ["Rerank / 重排基础"],
            "用户画像": ["用户画像与特征工程"],
        }
        for skill, names in expected.items():
            assert _links(env, skill) == {_topic(env, n).id for n in names}, skill

    def test_ranking_keeps_history_links(self, conn):
        env = _env(conn)
        assert _topic(env, "Reranker 重排序").id in _links(env, "Ranking")
        assert _topic(env, "RRF 排序融合").id in _links(env, "Ranking")

    def test_recall_metric_not_recall_coverage(self, conn):
        env = _env(conn)
        metric = _topic(env, "推荐系统评估指标").id
        assert metric not in _links(env, "Recall")

    def test_no_zero_linked_prerequisites(self, conn):
        env = _env(conn)
        prereq = set()
        for s in env["sr"].list_all():
            prereq.update(s["prerequisites"] or [])
        zero = [s["name"] for s in env["sr"].list_all()
                if not (s["linked_topics"] or []) and s["name"] in prereq]
        assert zero == []

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


def _mark_done(env, topic_name):
    t = _topic(env, topic_name)
    task = env["repo"].create(title=topic_name, scheduled_date="2027-05-01",
                              source="generated", topic_id=t.id)
    env["repo"].mark_done(task.id)


class TestStepwiseProgression:
    def test_rec_base_then_recall_then_ranking(self, conn):
        env = _env(conn)
        ss, sr = env["ss"], env["sr"]
        # 初始：全部被阻塞
        assert ss.is_blocked(sr.get_by_name("Recall"))
        assert ss.is_blocked(sr.get_by_name("Ranking"))
        assert ss.is_blocked(sr.get_by_name("CTR"))
        assert ss.is_blocked(sr.get_by_name("Rerank"))

        _mark_done(env, "推荐系统整体架构")
        _mark_done(env, "协同过滤基础")
        ss.refresh_coverage()
        assert "推荐系统基础" in ss.refresh_coverage()
        assert ss.is_blocked(sr.get_by_name("Ranking"))  # 仍缺 Recall

        _mark_done(env, "Embedding Recall / 向量召回")
        _mark_done(env, "双塔召回 Two-Tower")
        _mark_done(env, "多路召回与 Candidate Generation")
        ss.refresh_coverage()
        assert "Recall" in ss.refresh_coverage()
        # Ranking 的 prereq（推荐系统基础 + Recall）已覆盖；Embedding 未覆盖
        assert not ss.is_blocked(sr.get_by_name("Ranking"))
        # CTR/Rerank 仍缺 Embedding
        assert ss.is_blocked(sr.get_by_name("CTR"))
        assert ss.is_blocked(sr.get_by_name("Rerank"))

    def test_not_unlocked_all_at_once(self, conn):
        env = _env(conn)
        ss, sr = env["ss"], env["sr"]
        _mark_done(env, "推荐系统整体架构")
        _mark_done(env, "协同过滤基础")
        ss.refresh_coverage()
        # 只覆盖推荐系统基础，不应解锁 Ranking/CTR/Rerank
        for n in ("Ranking", "CTR", "Rerank"):
            assert ss.is_blocked(sr.get_by_name(n)), n

    def test_coverage_is_not_mastery(self, conn):
        env = _env(conn)
        _mark_done(env, "推荐系统整体架构")
        _mark_done(env, "协同过滤基础")
        env["ss"].refresh_coverage()
        s = env["sr"].get_by_name("推荐系统基础")
        assert s["status"] == "not_started"
        assert env["ss"]._mastery_for_skill(s) is None
        assert env["arepo"].list_attempts() == []


class TestNoRegressionToday:
    def test_current_phase_and_today_task_unchanged(self, conn):
        env = _env(conn)
        # 复刻真实库：阶段二全部完成
        ph2 = _phase(env, "阶段二：深度学习与 LLM 基础")
        for t in env["prepo"].list_topics(ph2.id):
            _mark_done(env, t.name)
        env["ss"].refresh_coverage()
        phase = env["sps"].get_current_phase(TODAY)
        assert phase.name == "阶段三：LLM 应用"
        res = env["sps"].generate_daily_tasks(TODAY)
        assert res["selected"]  # 阶段三仍能正常生成
        ph3 = _phase(env, "阶段三：LLM 应用")
        phase3_ids = {t.id for t in env["prepo"].list_topics(ph3.id)}
        assert set(res["selected"]) <= phase3_ids
        # 不提前生成阶段四主题
        assert _topic(env, "双塔召回 Two-Tower").id not in res["selected"]
        assert _topic(env, "Ranking 基础").id not in res["selected"]

    def test_new_topic_task_content_executable(self):
        for name in ("SQL 数据分析基础", "双塔召回 Two-Tower", "CTR 预估基础"):
            text = build_topic_task_content(name, name)
            assert "【学习目标】" in text
            assert "【具体学习事项】" in text
            assert "【完成标准】" in text
            assert text != name
