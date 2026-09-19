"""Phase D：notes_service —— Obsidian 每日 Markdown。

覆盖（与清单映射）：
13 Markdown 生成
14 Markdown 结构
15 Markdown 重复导出幂等
+ 写入失败返回明确错误、不崩溃
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.database.skill_repository import LearningOutcomeRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.learning_outcome_service import LearningOutcomeService
from app.services.notes_service import NotesService
from app.services.study_plan_service import StudyPlanService

DAY = "2026-09-11"


def _build(conn, plan_repo):
    """构造 NotesService 所需环境：默认计划 + 当日任务 + 成果 + 验收证据。"""
    repo = TaskRepository(conn)
    assessment_repo = AssessmentRepository(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=assessment_repo)
    sps.ensure_default_plan()
    lo_repo = LearningOutcomeRepository(conn)
    lo_service = LearningOutcomeService(lo_repo)

    # 当日新知识 / 复习
    repo.create(title="PyTorch 实现 Attention", scheduled_date=DAY,
                description="实现 scaled dot-product attention",
                source="generated", topic_id=1, task_type="new")
    kp = assessment_repo.create_knowledge_point("pytorch.autograd")
    repo.create(title="复习 pytorch.autograd", scheduled_date=DAY,
                source="review", task_type="review",
                knowledge_point_id=kp["id"])
    # 一个失败任务的薄弱验收证据
    att = assessment_repo.create_attempt(
        kp["id"], '[{"question":"q","type":"concept","expected_points":1}]')
    assessment_repo.update_attempt(
        att["id"], judge_status="judged", result_level="poor",
        mastery_estimate=0.3, weak_points_json='["梯度清零"]')
    # 一条项目成果
    lo_service.create_manual_outcome(
        date=DAY, kind="project", title="Attention 实现项目",
        content="在 PyTorch 中实现 MHA", tech_stack=["PyTorch"])

    ns = NotesService(
        repo=repo, study_plan_service=sps, outcome_service=lo_service,
        assessment_repo=assessment_repo, jd_service=None,
    )
    return ns, lo_service, repo


class TestNoteGeneration:
    def test_markdown_file_created(self, conn, plan_repo, tmp_path):
        ns, _, _ = _build(conn, plan_repo)
        out = tmp_path / "vault"
        res = ns.export_daily_note(DAY, out)
        p = Path(res["path"])
        assert p.exists()
        assert p.name == f"{DAY}.md"
        assert p.read_text(encoding="utf-8").strip()

    def test_markdown_structure(self, conn, plan_repo, tmp_path):
        ns, _, repo = _build(conn, plan_repo)
        content = ns.build_daily_note(DAY)
        for section in (
            "# 2026-09-11 学习笔记",
            "## 今日学习目标",
            "## 今日新知识",
            "## 今日复习",
            "## 学习成果",
            "## 薄弱点",
            "## 简历素材",
            "## 明日建议",
        ):
            assert section in content
        # 真实数据出现
        assert "PyTorch 实现 Attention" in content
        assert "mastery 估计 0.30（AI 估计，非绝对事实）" in content
        assert "梯度清零" in content
        assert "Attention 实现项目" in content
        # 明确说明 mastery 是估计，不是绝对事实
        assert "AI 估计，非绝对事实" in content

    def test_idempotent_export(self, conn, plan_repo, tmp_path):
        ns, _, _ = _build(conn, plan_repo)
        out = tmp_path / "vault"
        ns.export_daily_note(DAY, out)
        ns.export_daily_note(DAY, out)
        p = out / f"{DAY}.md"
        files = list(out.glob("*.md"))
        assert len(files) == 1
        first = p.read_text(encoding="utf-8")
        ns.export_daily_note(DAY, out)
        assert p.read_text(encoding="utf-8") == first  # 内容稳定，不重复追加

    def test_empty_parts_honest(self, conn, plan_repo, tmp_path):
        # 无任务、无成果的干净日期：明确写“暂无”，不补写内容
        from app.database.connection import get_connection

        path = tmp_path / "db2.db"
        c2 = get_connection(path)
        try:
            repo = TaskRepository(c2)
            ns = NotesService(repo=repo)
            content = ns.build_daily_note(DAY)
            assert "> 今日暂无该部分记录" in content
            assert "实现" not in content  # 不伪造任何实现细节
        finally:
            c2.close()

    def test_write_failure_raises_clear_error(self, conn, plan_repo, tmp_path):
        ns, _, _ = _build(conn, plan_repo)
        blocked = tmp_path / "not_a_dir"
        blocked.write_text("i am a file", encoding="utf-8")

        with pytest.raises(OSError):
            ns.export_daily_note(DAY, blocked)


class TestCliExportNote:
    def test_cli_export_writes_and_is_idempotent(self, tmp_path):
        from app.main import _run_export_note_cli

        db = tmp_path / "cli.db"
        out = tmp_path / "notes"
        code = _run_export_note_cli([
            "--date", DAY, "--output", str(out), "--db", str(db),
        ])
        assert code == 0
        p = out / f"{DAY}.md"
        assert p.exists()
        first = p.read_text(encoding="utf-8")
        code2 = _run_export_note_cli([
            "--date", DAY, "--output", str(out), "--db", str(db),
        ])
        assert code2 == 0
        assert p.read_text(encoding="utf-8") == first
        assert len(list(out.glob("*.md"))) == 1

    def test_cli_export_bad_date_rejected(self):
        from app.main import _run_export_note_cli

        with pytest.raises(SystemExit):
            _run_export_note_cli(["--date", "2026/09/11", "--output", "/tmp"])
