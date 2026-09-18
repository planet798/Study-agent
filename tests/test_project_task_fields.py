"""Phase 11 Step 1：Project-driven Learning 数据层测试。

覆盖 tasks 项目化字段（v11）：创建/读取/列表/更新、默认空串、
task_type 扩展（project / experiment，无 CHECK 约束）、
以及旧 new/review/extra 行为不变。
"""

from __future__ import annotations

from app.database.repository import Task, TaskRepository
from app.database.schema import STATUS_ACTIVE, STATUS_DONE, get_schema_version

PROJECT_FIELDS = (
    "project_name",
    "project_repo",
    "deliverable",
    "acceptance_criteria",
    "expected_artifact",
)


def _blank(task: Task) -> bool:
    return all(getattr(task, f) == "" for f in PROJECT_FIELDS)


class TestProjectTaskRoundtrip:
    def test_create_and_read_back(self, repo):
        t = repo.create(
            title="Mini RAG service", scheduled_date="2026-09-20",
            source="generated", task_type="project",
            project_name="Mini RAG",
            project_repo="https://github.com/me/mini-rag",
            deliverable="A working retrieval API",
            acceptance_criteria="recall@5 >= 0.6; tests pass",
            expected_artifact="repo + short report",
        )
        assert t.task_type == "project"
        got = repo.get(t.id)
        assert got.project_name == "Mini RAG"
        assert got.project_repo == "https://github.com/me/mini-rag"
        assert got.deliverable == "A working retrieval API"
        assert got.acceptance_criteria == "recall@5 >= 0.6; tests pass"
        assert got.expected_artifact == "repo + short report"

    def test_list_maps_project_fields(self, repo):
        t = repo.create(title="p", scheduled_date="2026-09-20",
                        task_type="project", project_name="P")
        listed = [x for x in repo.list_by_date("2026-09-20") if x.id == t.id]
        assert listed and listed[0].project_name == "P"

    def test_list_by_topic_maps_fields(self, repo):
        t = repo.create(title="p", scheduled_date="2026-09-20",
                        task_type="project", topic_id=7,
                        deliverable="D")
        listed = [x for x in repo.list_by_topic_id(7) if x.id == t.id]
        assert listed and listed[0].deliverable == "D"

    def test_update_project_fields(self, repo):
        t = repo.create(title="p", scheduled_date="2026-09-20",
                        task_type="project")
        updated = repo.update(t.id, project_repo="https://x/y",
                              acceptance_criteria="ok")
        assert updated.project_repo == "https://x/y"
        assert updated.acceptance_criteria == "ok"

    def test_experiment_type_allowed(self, repo):
        t = repo.create(title="e", scheduled_date="2026-09-20",
                        task_type="experiment",
                        expected_artifact="plots")
        assert repo.get(t.id).task_type == "experiment"
        assert repo.get(t.id).expected_artifact == "plots"


class TestDefaults:
    def test_new_fields_default_empty(self, repo):
        t = repo.create(title="plain", scheduled_date="2026-09-20")
        assert _blank(t)

    def test_old_row_style_insert_defaults_empty(self, conn):
        conn.execute(
            "INSERT INTO tasks (title, description, category, "
            "estimated_minutes, priority, status, scheduled_date, "
            "postpone_count, created_at, updated_at, source) "
            "VALUES ('raw','','学习',1,1,'active','2026-09-20',0,'t','t','manual')"
        )
        conn.commit()
        row = Task(**dict(conn.execute(
            "SELECT * FROM tasks WHERE title='raw'").fetchone()))
        assert _blank(row)

    def test_schema_version_is_current(self, conn):
        from app.database.schema import SCHEMA_VERSION

        assert get_schema_version(conn) == SCHEMA_VERSION


class TestLegacyTypesUnchanged:
    def test_new_task_behavior(self, repo):
        t = repo.create(title="n", scheduled_date="2026-09-20",
                        source="generated", task_type="new")
        assert t.status == STATUS_ACTIVE and t.is_active
        assert _blank(t)

    def test_review_task_behavior(self, repo):
        t = repo.create(title="r", scheduled_date="2026-09-20",
                        source="review", task_type="review",
                        knowledge_point_id=3)
        assert t.task_type == "review"
        assert t.knowledge_point_id == 3
        assert _blank(t)

    def test_extra_task_behavior(self, repo):
        t = repo.create(title="x", scheduled_date="2026-09-20",
                        source="extra", task_type="extra",
                        difficulty="challenge")
        assert t.task_type == "extra"
        assert t.difficulty == "challenge"
        assert _blank(t)

    def test_complete_and_not_done_still_work(self, repo):
        a = repo.create(title="a", scheduled_date="2026-09-20")
        repo.mark_done(a.id)
        assert repo.get(a.id).is_done
        b = repo.create(title="b", scheduled_date="2026-09-20")
        repo.mark_not_done(b.id, "reason")
        assert repo.get(b.id).status == "not_done"
        assert repo.get(b.id).reason == "reason"
