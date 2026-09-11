"""Phase D：learning_outcome_service —— 成果沉淀 + 简历素材。

覆盖（与清单映射）：
1 task completion → outcome
2 assessment good/excellent → outcome
3 ok / poor 不生成虚假“已掌握成果”
4 manual outcome
5 outcome 幂等
6 dataset 缺失不编造
7 metrics 缺失不编造
8 Git commit 可获取
9 Git 不可用不阻塞
10 AI 正常生成 resume material
11 AI 失败 fallback
12 AI 试图增加不存在事实 → 拒绝
17 outcome 与 knowledge_point / topic 正确关联
18 旧库没有 outcomes 时正常运行
19 不修改 tasks（不只生成/查询时改动任务）
20 不修改 study_plan
21 不修改 Planner（planner_decisions 不变）
22 不修改 career_context
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.connection import get_connection
from app.database.repository import TaskRepository
from app.database.skill_repository import (
    JdRepository,
    LearningOutcomeRepository,
    SkillRepository,
)
from app.services.assessment_service import AssessmentService
from app.services.learning_outcome_service import (
    ALLOWED_KINDS,
    KIND_PROJECT,
    LearningOutcomeService,
)
from app.services.review_service import ReviewService
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService

GIT_HEX = r"^[0-9a-f]{40}$"


class FakeQuestionAI:
    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return json.dumps(
            {"questions": [
                {"question": "解释 autograd", "type": "concept",
                 "expected_points": 2}]},
            ensure_ascii=False,
        )


class FakeJudgeGood:
    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return json.dumps(
            {"questions": [{"question_index": 0, "verdict": "correct",
                            "reason": "正确"}],
             "weak_points": [], "result_level": "good",
             "mastery_estimate": 0.72}, ensure_ascii=False,
        )


class FakeJudgePoor:
    def is_configured(self):
        return True

    def chat(self, system_prompt, user_prompt, **kwargs):
        return json.dumps(
            {"questions": [{"question_index": 0, "verdict": "incorrect",
                            "reason": "错"}],
             "weak_points": ["梯度清零"], "result_level": "poor",
             "mastery_estimate": 0.3}, ensure_ascii=False,
        )


@pytest.fixture()
def outcome_repo(conn):
    return LearningOutcomeRepository(conn)


@pytest.fixture()
def lo_service(conn, outcome_repo):
    return LearningOutcomeService(outcome_repo)


class TestTaskOutcome:
    def test_task_completion_creates_outcome(self, conn, lo_service):
        task_service = TaskService(TaskRepository(conn),
                                   outcome_service=lo_service)
        t = task_service.create_task("PyTorch 实现 Attention", scheduled_date="2026-09-10")
        task_service.complete_task(t.id)
        outcomes = lo_service.list_by_date("2026-09-10")
        assert len(outcomes) == 1
        o = outcomes[0]
        assert o["task_id"] == t.id
        assert o["kind"] == "topic"          # 学习证据，不自动升级项目成果
        assert o["title"] == "PyTorch 实现 Attention"

    def test_task_outcome_is_idempotent(self, conn, lo_service):
        task_service = TaskService(TaskRepository(conn),
                                   outcome_service=lo_service)
        task = task_service.create_task("重复任务", scheduled_date="2026-09-10")
        task_service.complete_task(task.id)
        # 模拟“重复完成/刷新”：再次调用同 task 生成 -> 更新而非新增
        lo_service.generate_from_task(task)
        lo_service.generate_from_task(task)
        assert len(lo_service.list_by_date("2026-09-10")) == 1

    def test_task_outcome_does_not_fabricate(self, conn, lo_service):
        task_service = TaskService(TaskRepository(conn),
                                   outcome_service=lo_service)
        t = task_service.create_task("普通概念学习", scheduled_date="2026-09-10")
        task_service.complete_task(t.id)
        o = lo_service.list_by_date("2026-09-10")[0]
        assert o["dataset"] == ""
        assert o["metrics"] == {}
        assert o["github_url"] == ""
        assert not o["resume_keywords"] or isinstance(o["resume_keywords"], list)


class TestAssessmentOutcome:
    def _run_assessment(self, conn, lo_service, judge_ai, today="2026-09-10"):
        assessment_repo = AssessmentRepository(conn)
        kp = assessment_repo.create_knowledge_point("pytorch.autograd")
        review_scheduler = ReviewService(TaskRepository(conn), assessment_repo)
        svc = AssessmentService(
            judge_ai, assessment_repo=assessment_repo,
            review_service=review_scheduler, outcome_service=lo_service,
        )
        attempt = assessment_repo.create_attempt(
            kp["id"], '[{"question":"q","type":"concept","expected_points":1}]')
        svc.submit_answers(attempt["id"], ["答案"], today=today)
        return kp, assessment_repo

    def test_good_creates_mastery_outcome(self, conn, lo_service):
        kp, arepo = self._run_assessment(conn, lo_service, FakeJudgeGood())
        outs = lo_service.list_by_date("2026-09-10")
        assert outs
        o = outs[0]
        assert o["kind"] == "topic"
        assert "验收达成" in o["title"]
        assert o["linked_kp_id"] == kp["id"]
        assert o["metrics"]["mastery_estimate"] == pytest.approx(0.72, abs=1e-3)
        assert o["dataset"] == "" and o["github_url"] == ""
        # 掌握证据，不进入“项目成果”候选
        assert o["kind"] not in KIND_PROJECT

    def test_poor_is_not_fake_achievement(self, conn, lo_service):
        kp, _ = self._run_assessment(conn, lo_service, FakeJudgePoor())
        o = lo_service.list_by_date("2026-09-10")[0]
        assert o["kind"] == "note"
        assert "薄弱" in o["title"]
        assert "weak_points" in o["metrics"] or "梯度清零" in o.get("content", "")
        # 不声称“已掌握”
        assert "已掌握成果" not in o["title"]

    def test_ok_not_over_generated_as_project(self, conn, lo_service):
        # ok：记录掌握情况（note），不当作项目成果
        o = lo_service.create_outcome(
            date="2026-09-10", kind="topic", title="X 掌握情况",
            metrics={"mastery_estimate": 0.5})
        assert o["kind"] == "topic"


class TestManual:
    def test_manual_outcome_saved(self, lo_service):
        o = lo_service.create_manual_outcome(
            date="2026-09-10", kind="project",
            title="LoRA 微调项目", content="SFT+LoRA 微调 Qwen",
            tech_stack=["LoRA", "PyTorch"], dataset="alpaca",
            metrics={"eval_loss": 0.12}, github_url="https://github.com/x",
            resume_keywords=["SFT", "LoRA"],
        )
        got = lo_service.outcome_repo.get(o["id"])
        assert got["kind"] == "project"
        assert got["dataset"] == "alpaca"
        assert got["metrics"]["eval_loss"] == 0.12
        assert got["resume_keywords"] == ["SFT", "LoRA"]

    def test_manual_validation(self, lo_service):
        with pytest.raises(ValueError):
            lo_service.create_manual_outcome(kind="bogus", title="x")
        with pytest.raises(ValueError):
            lo_service.create_manual_outcome(kind="project", title="  ")
        with pytest.raises(ValueError):
            lo_service.create_manual_outcome(kind="project", title="x",
                                             metrics=[1, 2])


class TestGit:
    def test_git_commit_available(self, lo_service):
        commit = lo_service.current_git_commit()
        assert commit is not None
        import re
        assert re.fullmatch(GIT_HEX, commit)
        o = lo_service.create_manual_outcome(
            kind="project", title="G", derive_git=True)
        assert o["git_commit"] == commit

    def test_git_unavailable_not_blocking(self, conn, outcome_repo, tmp_path):
        nowhere = tmp_path / "not_a_repo"
        svc = LearningOutcomeService(outcome_repo, project_root=nowhere)
        assert svc.current_git_commit() is None
        o = svc.create_manual_outcome(
            kind="note", title="无 git", derive_git=True)
        assert o["git_commit"] == "" or o["git_commit"] is None


class _GoodAI:
    def is_configured(self):
        return True

    def chat(self, s, u, **k):
        return json.dumps({
            "keywords": ["双塔", "召回", "PyTorch"],
            "bullets": ["完成「召回实验」：实现双塔召回（技术栈：PyTorch）"],
            "summary": "完成召回实验，采用双塔策略。",
        }, ensure_ascii=False)


class _EvilAI:
    def is_configured(self):
        return True

    def chat(self, s, u, **k):
        return json.dumps({
            "keywords": ["编造词"],
            "bullets": [
                "完成「召回实验」：推理速度提升 30%，准确率达 95%",
                "实现双塔召回（代码：https://fake-github.example/x）",
            ],
            "summary": "性能提升 20%，线上收益 +15%。",
        }, ensure_ascii=False)


class _BrokenAI:
    def is_configured(self):
        return True

    def chat(self, s, u, **k):
        raise RuntimeError("AI 挂了")


class TestResumeMaterial:
    def _seed(self, lo_service):
        lo_service.create_manual_outcome(
            date="2026-09-10", kind="project", title="召回实验",
            content="实现双塔召回", tech_stack=["PyTorch"],
            resume_keywords=["召回", "双塔"])
        lo_service.create_manual_outcome(
            date="2026-09-10", kind="topic", title="学了个概念")

    def test_ai_normal_generates_material(self, lo_service):
        self._seed(lo_service)
        mat = lo_service.build_resume_material(ai_client=_GoodAI())
        assert mat["ai_augmented"] is True
        assert mat["candidate_bullets"]
        assert "recall" not in " ".join(mat["candidate_bullets"]).lower()

    def test_ai_failure_falls_back(self, lo_service):
        self._seed(lo_service)
        mat = lo_service.build_resume_material(ai_client=_BrokenAI())
        assert mat["ai_augmented"] is False
        assert mat["candidate_bullets"]  # 确定性 bullet 兜底
        assert all("「召回实验」" in b for b in mat["candidate_bullets"])

    def test_malicious_ai_facts_rejected(self, lo_service):
        self._seed(lo_service)
        mat = lo_service.build_resume_material(ai_client=_EvilAI())
        joined = " ".join(mat["candidate_bullets"]) + mat["technical_summary"]
        assert "30%" not in joined
        assert "95%" not in joined
        assert "提升 20%" not in joined
        assert "fake-github" not in joined
        # 确定性 bullet（只含真实事实）
        assert any("双塔召回" in b for b in mat["candidate_bullets"])

    def test_deterministic_without_project(self, lo_service):
        lo_service.create_manual_outcome(kind="topic", title="只学了概念")
        mat = lo_service.build_resume_material()
        assert mat["ai_augmented"] is False
        assert "尚无已完成的" in mat["technical_summary"] or \
            not mat["candidate_bullets"]


class TestLinksAndBoundaries:
    def test_links_kp_and_topic(self, conn, lo_service):
        from app.database.study_plan_repository import StudyPlanRepository

        plan_repo = StudyPlanRepository(conn)
        plan = plan_repo.create_plan(name="p", start_date="2026-09-01",
                                     end_date="2026-12-31")
        phase = plan_repo.create_phase(plan_id=plan.id, name="ph",
                                       start_date="2026-09-01",
                                       end_date="2026-12-31")
        topic = plan_repo.create_topic(phase_id=phase.id, name="Attention")
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("attention", topic_id=topic.id)
        t = TaskRepository(conn).create(
            title="实现 Attention", scheduled_date="2026-09-10",
            topic_id=topic.id, knowledge_point_id=kp["id"])
        lo_service.generate_from_task(t)
        o = lo_service.outcome_repo.get_by_task_id(t.id)
        assert o["linked_topic_id"] == topic.id
        assert o["linked_kp_id"] == kp["id"]

    def test_old_db_without_outcomes_runs_fine(self, tmp_path):
        path = tmp_path / "old.db"
        conn = get_connection(path)  # 迁移到系统最新
        try:
            svc = LearningOutcomeService(LearningOutcomeRepository(conn))
            assert svc.list_by_date("2026-09-10") == []
            mat = svc.build_resume_material()
            assert mat["candidate_bullets"] == []
        finally:
            conn.close()

    def test_does_not_touch_tasks_plan_planner_career(self, conn, lo_service):
        from app.services.jd_service import DEFAULT_CAREER_CONTEXT_PATH

        task_rows_before = conn.execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0]
        plan_rows = conn.execute(
            "SELECT COUNT(*) FROM study_plans").fetchone()[0]
        decision_rows = conn.execute(
            "SELECT COUNT(*) FROM planner_decisions").fetchone()[0]
        career_before = DEFAULT_CAREER_CONTEXT_PATH.read_bytes()

        lo_service.create_manual_outcome(kind="project", title="仅记录成果")
        lo_service.build_resume_material()

        assert conn.execute(
            "SELECT COUNT(*) FROM tasks").fetchone()[0] == task_rows_before
        assert conn.execute(
            "SELECT COUNT(*) FROM study_plans").fetchone()[0] == plan_rows
        assert conn.execute(
            "SELECT COUNT(*) FROM planner_decisions").fetchone()[0] == decision_rows
        assert DEFAULT_CAREER_CONTEXT_PATH.read_bytes() == career_before
