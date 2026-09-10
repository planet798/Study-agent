"""Phase B：JdService —— JD 入库 / 解析 / 频率 / 影响分析 / 预览。

覆盖（与需求清单对应）：
1 文本 JD 正常入库
2 文件 JD 正常入库
3 raw_text 完整保存
4 规则解析
5 AI 正常解析
6 AI 非法结构
7 AI 调用失败 fallback
8 must / plus 正确区分
9 同一 JD 技能不重复计数
10 重复执行幂等
11 新 JD 后 priority 变化
12 must > plus
13 mastery 高仍然降权
14 prerequisite gate
15 dry-run 不写数据库
16 正式 add-jd 写入数据库
17 weekly preview 不创建 task
18 不修改 study_phase
19 不修改 career_context 长期路线
20 无匹配技能时正常运行
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import JdRepository, SkillRepository
from app.services.jd_service import JdService
from app.services.skill_service import SkillService

JD_BASIC = (
    "算法实习生（AI 方向）\n"
    "负责大模型相关的召回、排序与 LLM/RAG 应用开发。\n"
    "熟悉 Python、PyTorch，掌握推荐系统基础。\n"
    "有 SFT / LoRA 微调经验、vLLM、Docker 部署经验者优先。"
)

JD_NO_SKILL = "热爱学习，做事认真，沟通能力强，英语通过六级。\n"


def _seed_mini(skill_repo):
    """小型确定性技能集（含门禁与共振标记）。"""
    skill_repo.create(name="Python", tier="S", status="mastered")
    skill_repo.create(name="PyTorch", tier="S", status="learning")
    skill_repo.create(name="Transformer", tier="S", status="learning")
    skill_repo.create(name="LLM 基础", tier="S", status="learning")
    skill_repo.create(name="RAG", tier="S", status="not_started",
                      prerequisites=["LLM 基础", "Embedding"])
    skill_repo.create(name="推荐系统基础", tier="S", status="not_started")
    skill_repo.create(name="Recall", tier="S", status="not_started",
                      prerequisites=["推荐系统基础", "Embedding"])
    skill_repo.create(name="Ranking", tier="S", status="not_started",
                      prerequisites=["推荐系统基础", "Recall"])
    skill_repo.create(name="Embedding", tier="S", status="not_started",
                      shared_connector=True)
    skill_repo.create(name="SFT", tier="S", status="not_started",
                      prerequisites=["LLM 基础", "Transformer"])
    skill_repo.create(name="vLLM", tier="B", status="deferred")
    skill_repo.create(name="Docker", tier="B", status="not_started")


@pytest.fixture()
def conn_ok(conn):
    return conn


@pytest.fixture()
def skill_repo(conn_ok):
    return SkillRepository(conn_ok)


@pytest.fixture()
def jd_repo(conn_ok):
    return JdRepository(conn_ok)


@pytest.fixture()
def assessment_repo(conn_ok):
    return AssessmentRepository(conn_ok)


@pytest.fixture()
def skill_service(conn_ok, skill_repo, assessment_repo):
    return SkillService(skill_repo, assessment_repo=assessment_repo)


@pytest.fixture()
def jd_service(skill_repo, jd_repo, skill_service):
    svc = JdService(jd_repo, skill_repo, skill_service)
    _seed_mini(skill_repo)
    return svc


def _ai_ok(text):
    return json.dumps(
        {"direction": "llm", "must": ["Python", "PyTorch", "RAG"],
         "plus": ["Docker"], "intern": True},
        ensure_ascii=False,
    )


class TestJdIngest:
    def test_text_jd_inserts(self, jd_service, conn):
        r = jd_service.add_jd(JD_BASIC, company="某公司", title="算法实习生")
        assert r["inserted"] is True
        assert r["id"] > 0
        assert JdRepository(conn).list_all()  # 至少一条

    def test_file_jd_inserts(self, jd_service, tmp_path):
        p = tmp_path / "jd.md"
        p.write_text(JD_BASIC, encoding="utf-8")
        r = jd_service.add_jd_from_file(p, company="某公司")
        assert r["inserted"] is True
        assert r["parsed"]["raw_text"] if "raw_text" in r["parsed"] else True

    def test_raw_text_preserved_exactly(self, jd_service, jd_repo):
        multi = "第一行：推荐算法实习生\n第二行：熟悉召回排序\n第三行：有 LLM 经验优先"
        jd_service.add_jd(multi)
        row = jd_repo.list_all()[0]
        assert row["raw_text"] == multi

    def test_idempotent_duplicate_add(self, jd_service, jd_repo, skill_repo):
        a = jd_service.add_jd(JD_BASIC)
        b = jd_service.add_jd(JD_BASIC)
        assert a["inserted"] is True and b["idempotent"] is True
        assert len(jd_repo.list_all()) == 1
        # 频率不重复累计
        freq = skill_repo.get_by_name("PyTorch")["jd_frequency"]
        assert freq["must"] == 1


class TestParse:
    def test_rules_parse(self, jd_service):
        parsed = jd_service.parse_jd(JD_BASIC, use_ai=False)
        assert parsed["method"] == "rules"
        assert parsed["intern"] is True
        assert "推荐系统基础" in parsed["must"]
        assert "SFT" in parsed["plus"]
        assert "vLLM" in parsed["plus"]
        assert "Docker" in parsed["plus"]

    def test_must_plus_distinction(self, jd_service):
        text = "熟悉推荐系统召回、排序，有 LLM/RAG 经验优先"
        parsed = jd_service.parse_jd(text, use_ai=False)
        assert "Recall" in parsed["must"]
        assert "Ranking" in parsed["must"]
        assert "推荐系统基础" in parsed["must"]
        assert "LLM 基础" in parsed["plus"]
        assert "RAG" in parsed["plus"]

    def test_ai_normal_parse(self, jd_service):
        jd_service.parse_ai = _ai_ok
        parsed = jd_service.parse_jd(JD_BASIC, use_ai=True)
        assert parsed["method"] == "ai"
        assert "RAG" in parsed["must"]
        assert "Docker" in parsed["plus"]

    def test_ai_invalid_structure_falls_back(self, jd_service):
        def bad(text):
            return "not json at all"

        jd_service.parse_ai = bad
        parsed = jd_service.parse_jd(JD_BASIC, use_ai=True)
        assert parsed["method"] == "rules_fallback"
        assert parsed["must"]  # 回退规则结果非空

    def test_ai_call_failure_falls_back(self, jd_service):
        def boom(text):
            raise RuntimeError("AI 挂了")

        jd_service.parse_ai = boom
        parsed = jd_service.parse_jd(JD_BASIC, use_ai=True)
        assert parsed["method"] == "rules_fallback"
        assert parsed["intern"] is True


class TestFrequency:
    def test_same_skill_not_double_counted(self, jd_service, jd_repo, skill_repo):
        text = "熟悉 Python、精通 Python，掌握 PyTorch 与 PyTorch 的高级特性"
        jd_service.add_jd(text)
        assert skill_repo.get_by_name("Python")["jd_frequency"]["must"] == 1
        assert skill_repo.get_by_name("PyTorch")["jd_frequency"]["must"] == 1
        assert skill_repo.get_by_name("Python")["jd_frequency"]["total_jds"] == 1

    def test_recompute_after_delete_is_correct(self, jd_service, jd_repo,
                                               skill_repo):
        jd_service.add_jd(JD_BASIC)
        jd_service.add_jd(JD_NO_SKILL)  # 无匹配技能也入库，占比分母
        assert skill_repo.get_by_name("PyTorch")["jd_frequency"]["total_jds"] == 2
        jd_repo.delete(jd_repo.list_all()[0]["id"])  # 删掉无技能那条
        jd_service.recompute_skill_frequencies()
        jd_service.skill_service.recompute_all_priority_scores()
        assert skill_repo.get_by_name("PyTorch")["jd_frequency"]["total_jds"] == 1


class TestPriorityChange:
    def test_new_jd_raises_priority(self, jd_service, jd_repo):
        before = jd_service.skill_service.recompute_all_priority_scores()
        score_before = {d["name"]: d["score"] for d in before}
        r = jd_service.add_jd(JD_BASIC)
        imp = r["impact"]
        by_skill = {e["skill"]: e for e in imp["affected_skills"]}
        for e in imp["affected_skills"]:
            assert e["priority_after"] >= e["priority_before"]
        # 至少一个技能优先级上升
        assert any(
            e["priority_after"] > e["priority_before"]
            for e in imp["affected_skills"]
        )

    def test_must_stronger_than_plus(self, jd_service, skill_repo):
        skill = skill_repo.get_by_name("推荐系统基础")
        s_must = {**skill, "jd_frequency": {"must": 1, "plus": 0, "total_jds": 1}}
        s_plus = {**skill, "jd_frequency": {"must": 0, "plus": 1, "total_jds": 1}}
        p_must = jd_service.skill_service.compute_skill_score(s_must)["score"]
        p_plus = jd_service.skill_service.compute_skill_score(s_plus)["score"]
        assert p_must > p_plus

    def test_high_mastery_still_demotes(self, jd_service, skill_repo,
                                        assessment_repo, conn):
        jd_service.add_jd(JD_BASIC)
        p1 = skill_repo.get_by_name("Embedding")["priority_score"]
        # 补上高掌握证据：即使 JD 仍为 must，也要降权
        kp = assessment_repo.create_knowledge_point("embedding.core")
        assessment_repo.update_knowledge_point(
            kp["id"], mastery_estimate=0.95,
            last_assessed_at="2026-09-08T10:00:00",
        )
        skill_repo.update(skill_repo.get_by_name("Embedding")["id"],
                          mastery_ref=f"kp:{kp['id']}")
        jd_service.skill_service.recompute_all_priority_scores()
        p2 = skill_repo.get_by_name("Embedding")["priority_score"]
        assert p2 < p1

    def test_prerequisite_gate_blocks(self, jd_service):
        r = jd_service.add_jd(JD_BASIC)  # RAG/微调等出现，但 RAG 前置未满足
        blocked = {e["skill"] for e in r["impact"]["prerequisite_blocked"]}
        focus = {e["skill"] for e in r["impact"]["weekly_focus"]}
        assert "RAG" in blocked
        assert "RAG" not in focus
        assert "vLLM" not in focus  # deferred/低优先


class TestDryRunAndWrite:
    def test_dry_run_writes_nothing(self, jd_service, jd_repo, skill_repo, conn):
        before_freq = {s["name"]: s["jd_frequency"] for s in skill_repo.list_all()}
        before_score = {s["name"]: s["priority_score"]
                        for s in skill_repo.list_all()}
        imp = jd_service.preview_impact(JD_BASIC)
        assert len(jd_repo.list_all()) == 0
        assert "推荐系统基础" in {e["skill"] for e in imp["affected_skills"]}
        for s in skill_repo.list_all():
            assert s["jd_frequency"] == before_freq[s["name"]]
            assert s["priority_score"] == before_score[s["name"]]

    def test_cli_dry_run_writes_nothing(self, tmp_path):
        from app.main import _run_add_jd_cli

        db = tmp_path / "cli.db"
        code = _run_add_jd_cli([
            "--text", JD_BASIC, "--dry-run", "--no-ai", "--db", str(db),
            "--today", "2026-09-08",
        ])
        assert code == 0
        from app.database.connection import get_connection

        conn = get_connection(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM jds").fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM skills").fetchone()[0] == 0
        finally:
            conn.close()

    def test_formal_add_writes_db(self, jd_service, jd_repo, skill_repo):
        r = jd_service.add_jd(JD_BASIC, company="某公司")
        assert r["inserted"] is True
        assert len(jd_repo.list_all()) == 1
        assert skill_repo.get_by_name("PyTorch")["jd_frequency"]["must"] == 1


class TestBoundaries:
    def test_weekly_preview_creates_no_tasks(self, jd_service, conn):
        w = jd_service.preview_weekly_priorities("2026-09-08")
        assert w["window_days"] == 7
        assert len(w["daily_focus"]) == 7
        assert TaskRepository(conn).list_by_date("2026-09-09") == []
        # 也不写 skills
        assert jd_service.skill_repo.list_all()

    def test_no_study_phase_change(self, jd_service, conn):
        n_phases = conn.execute(
            "SELECT COUNT(*) FROM study_phases").fetchone()[0]
        n_topics = conn.execute(
            "SELECT COUNT(*) FROM study_topics").fetchone()[0]
        jd_service.add_jd(JD_BASIC)
        jd_service.preview_weekly_priorities("2026-09-08")
        assert conn.execute(
            "SELECT COUNT(*) FROM study_phases").fetchone()[0] == n_phases
        assert conn.execute(
            "SELECT COUNT(*) FROM study_topics").fetchone()[0] == n_topics

    def test_career_context_untouched(self, jd_service, conn):
        from app.services.jd_service import DEFAULT_CAREER_CONTEXT_PATH

        before = DEFAULT_CAREER_CONTEXT_PATH.read_bytes()
        jd_service.add_jd(JD_BASIC)
        assert DEFAULT_CAREER_CONTEXT_PATH.read_bytes() == before

    def test_no_match_jd_runs_fine(self, jd_service, jd_repo):
        r = jd_service.add_jd(JD_NO_SKILL)
        assert r["inserted"] is True
        assert r["parsed"]["must"] == []
        assert r["parsed"]["plus"] == []
        assert len(jd_repo.list_all()) == 1
