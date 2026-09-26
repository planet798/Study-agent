"""S1 regression: Daily Review is retired; legacy rows remain untouched."""
from __future__ import annotations

import json

from app.ai.interface import AIServiceError
from app.ai.planner_context import KnowledgeEvidence, PlanningContext
from app.ai.prompts import build_knowledge_evidence_section
from app.database.assessment_repository import AssessmentRepository
from app.database.repository import TaskRepository
from app.services.assessment_service import AssessmentService
from app.services.notes_service import NotesService

DAY = "2026-09-10"
QUESTIONS = [{"question": "q", "type": "concept", "expected_points": 1}]
JUDGMENT = {
    "questions": [{"question_index": 0, "verdict": "correct", "reason": "ok"}],
    "weak_points": ["边界条件"], "result_level": "good", "mastery_estimate": 0.8,
}


class FakeClient:
    def is_configured(self):
        return True

    def chat(self, *_args, **_kwargs):
        return json.dumps(JUDGMENT, ensure_ascii=False)


def test_assessment_updates_mastery_but_preserves_legacy_review_fields(conn):
    arepo = AssessmentRepository(conn)
    kp = arepo.create_knowledge_point("legacy-kp")
    arepo.update_knowledge_point(
        kp["id"], next_review_date="2026-09-10", interval_days=5,
        review_count=3, mastery_estimate=0.2,
    )
    attempt = arepo.create_attempt(kp["id"], json.dumps(QUESTIONS))

    result = AssessmentService(FakeClient(), assessment_repo=arepo).submit_answers(
        attempt["id"], ["answer"], today=DAY,
    )

    updated = arepo.get_knowledge_point(kp["id"])
    assert updated["mastery_estimate"] == 0.8
    assert updated["last_assessed_at"]
    assert updated["next_review_date"] == "2026-09-10"
    assert updated["interval_days"] == 5
    assert updated["review_count"] == 3
    assert result["judge_status"] == "judged"
    assert len(arepo.list_attempts_for_knowledge_point(kp["id"])) == 1
    assert conn.execute("SELECT COUNT(*) FROM review_schedule").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE task_type='review'").fetchone()[0] == 0


def test_today_ignores_historical_review_rows_and_has_no_review_ui(
    qtbot, repo, task_service, date_service, fixed_today,
):
    historical = repo.create(title="历史复习", scheduled_date=fixed_today,
                source="review", task_type="review", estimated_minutes=40)
    arepo = AssessmentRepository(repo.conn)
    kp = arepo.create_knowledge_point("legacy")
    conn = repo.conn
    conn.execute(
        "INSERT INTO review_schedule (knowledge_point_id, scheduled_date, "
        "interval_days, status, task_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kp["id"], fixed_today, 5, "pending", historical.id, "history"),
    )
    conn.commit()
    repo.create(title="普通学习", scheduled_date=fixed_today,
                source="manual", task_type="new", estimated_minutes=25)
    from app.ui.main_window import MainWindow
    window = MainWindow(task_service, date_service, today_provider=lambda: fixed_today)
    qtbot.addWidget(window)
    window.refresh()

    labels = [label.text() for label in window.findChildren(__import__(
        "PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert "历史复习" not in labels
    assert "普通学习" in labels
    assert "今日复习" not in labels
    assert "每日巩固" not in labels
    assert "到期复习" not in labels
    assert window.today_page.pending_card.value() == "1"
    assert window.today_page.minutes_card.value() == "25 分钟"
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE id = ?", (historical.id,)).fetchone()[0] == 1
    schedule = conn.execute(
        "SELECT knowledge_point_id, scheduled_date, interval_days, status, task_id "
        "FROM review_schedule"
    ).fetchone()
    assert tuple(schedule) == (kp["id"], fixed_today, 5, "pending", historical.id)
    assert not hasattr(window.today_page, "review_card")
    window.close()
    from app.ui.design.theme_manager import ThemeManager
    ThemeManager.instance().set_theme("light")


def test_planner_evidence_and_prompt_exclude_review_fields():
    evidence = KnowledgeEvidence(
        knowledge_point_id=1, name="Tensor", mastery_estimate=0.4,
        weak_points=("shape",), last_assessed_at="2026-09-01",
        recent_result_level="poor",
    )
    context = PlanningContext(current_date=DAY, knowledge_evidence=[evidence])
    serialized = json.dumps(context.to_dict(), ensure_ascii=False)
    prompt = build_knowledge_evidence_section(context)
    assert "review_count" not in serialized
    assert "next_review_date" not in serialized
    assert "下次复习" not in prompt
    assert "weak_points" not in prompt  # rendered in Chinese below, not raw field name
    assert "薄弱点:shape" in prompt
    assert "mastery:0.40" in prompt
    assert "最近验收:poor" in prompt


def test_notes_have_no_review_section_and_weak_points_require_assessment_evidence(
    conn,
):
    repo = TaskRepository(conn)
    arepo = AssessmentRepository(conn)
    kp = arepo.create_knowledge_point("kp")
    learning = repo.create(title="学习", scheduled_date=DAY, task_type="new",
                           knowledge_point_id=kp["id"])
    historical = repo.create(title="Review", scheduled_date=DAY, task_type="review",
                             source="review", knowledge_point_id=kp["id"])
    # Historical review task alone must not create weak-point claims.
    content = NotesService(repo=repo, assessment_repo=arepo).build_daily_note(DAY)
    assert "## 今日复习" not in content
    assert "> 今日暂无该部分记录" in content

    attempt = arepo.create_attempt(kp["id"], json.dumps(QUESTIONS), task_id=learning.id)
    arepo.update_attempt(attempt["id"], judge_status="judged",
                         weak_points_json='["真实薄弱点"]')
    content = NotesService(repo=repo, assessment_repo=arepo).build_daily_note(DAY)
    assert "## 今日复习" not in content
    assert "真实薄弱点" in content
    assert historical.id is not None


def test_route_progress_model_has_no_review_schedule_fields():
    from app.database.schema import SCHEMA_VERSION
    from app.services.route_progress_service import KnowledgeStatus, RouteProgress
    assert SCHEMA_VERSION == 20
    assert "next_review_date" not in KnowledgeStatus.__dataclass_fields__
    assert "due_review_count" not in RouteProgress.__dataclass_fields__
    assert "upcoming_review_count" not in RouteProgress.__dataclass_fields__
