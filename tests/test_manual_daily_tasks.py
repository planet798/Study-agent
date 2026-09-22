"""Phase A：手动添加今日学习任务（普通 To-do / 正式知识任务）。

覆盖需求 26 的 1~9、25、26：
- 创建普通 manual todo / manual knowledge；
- manual todo 不创建 kp、不可 Assessment；
- manual knowledge 可关联已有 topic / 新建临时 kp / 可 Assessment；
- done ≠ mastered；
- 重复 manual kp 不重复创建（规范化名称幂等）；
- 来源标签正确。
"""

from __future__ import annotations

from app.database.assessment_repository import AssessmentRepository
from app.database.study_plan_repository import StudyPlanRepository
from app.services.manual_task_service import ManualTaskService
from app.services.study_plan_service import StudyPlanService
from app.services.task_service import TaskService
from app.ui.manual_task_dialog import KIND_KNOWLEDGE, KIND_TODO, AddLearningTaskDialog
from app.ui.task_widget import TaskWidget

TODAY = "2026-09-15"
PHASE_START = "2026-09-01"


def _env(conn, topic_names=("Transformer", "RAG")):
    plan_repo = StudyPlanRepository(conn)
    plan = plan_repo.create_plan(name="p", start_date=PHASE_START,
                                 end_date="2026-12-31")
    phase = plan_repo.create_phase(plan_id=plan.id, name="阶段",
                                   start_date=PHASE_START,
                                   end_date="2026-12-31")
    topics = {}
    for i, name in enumerate(topic_names):
        topics[name] = plan_repo.create_topic(
            phase_id=phase.id, name=name, description=f"{name} 描述",
            estimated_minutes=60, priority=3, order_index=i,
        )
    arepo = AssessmentRepository(conn)
    repo = _repo(conn)
    sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
    svc = ManualTaskService(repo, assessment_repo=arepo, study_plan_service=sps)
    return {"plan_repo": plan_repo, "arepo": arepo, "sps": sps,
            "svc": svc, "topics": topics, "repo": repo}


def _repo(conn):
    from app.database.repository import TaskRepository

    return TaskRepository(conn)


# ================= 1~2：创建 =================

class TestCreate:
    def test_create_manual_todo(self, conn):
        env = _env(conn)
        t = env["svc"].create_todo(
            "刷 LeetCode 3 道", description="数组 + 哈希", estimated_minutes=45,
            scheduled_date=TODAY,
        )
        assert t.source == "manual"
        assert t.task_type == "manual"
        assert t.topic_id is None
        assert t.knowledge_point_id is None
        assert t.status == "active"

    def test_create_manual_knowledge_temp_kp(self, conn):
        env = _env(conn)
        t = env["svc"].create_knowledge_task(
            "强化学习基础", description="RL / policy / value function",
            estimated_minutes=60, scheduled_date=TODAY,
        )
        assert t.source == "manual"
        assert t.task_type == "new"
        assert t.topic_id is None
        assert t.knowledge_point_id is not None
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert kp["name"] == "强化学习基础"
        assert kp["topic_id"] is None
        assert kp["mastery_estimate"] == 0.0
        assert kp["last_assessed_at"] is None

    def test_create_manual_knowledge_existing_topic(self, conn):
        env = _env(conn)
        topic = env["topics"]["Transformer"]
        t = env["svc"].create_knowledge_task(
            "Transformer 复习", topic_id=topic.id,
            scheduled_date=TODAY, estimated_minutes=30,
        )
        assert t.topic_id == topic.id
        assert t.knowledge_point_id is not None
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert kp["topic_id"] == topic.id
        assert kp["name"] == "Transformer"


# ================= 3~4：manual todo 隔离 =================

class TestTodoIsolation:
    def test_manual_todo_has_no_kp(self, conn):
        env = _env(conn)
        t = env["svc"].create_todo("看 PPO 面试视频", scheduled_date=TODAY)
        assert t.knowledge_point_id is None
        assert env["arepo"].list_knowledge_points() == []

    def test_manual_todo_cannot_assess(self, conn, qtbot):
        env = _env(conn)
        t = env["svc"].create_todo("整理 RL 面试题", scheduled_date=TODAY)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert hasattr(w, "assessment_btn") is False
        assert w._can_assess(w.task()) is False

    def test_manual_knowledge_can_assess(self, conn, qtbot):
        env = _env(conn)
        t = env["svc"].create_knowledge_task("PPO 基础", scheduled_date=TODAY)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert hasattr(w, "assessment_btn") is True


# ================= 8：done ≠ mastered =================

class TestDoneNotMastered:
    def test_done_does_not_set_mastery(self, conn):
        env = _env(conn)
        t = env["svc"].create_knowledge_task("哈希表", scheduled_date=TODAY)
        TaskService(env["repo"]).complete_task(t.id)
        kp = env["arepo"].get_knowledge_point(t.knowledge_point_id)
        assert kp["mastery_estimate"] == 0.0
        assert kp["last_assessed_at"] is None
        assert kp["review_count"] == 0


# ================= 9：临时知识点幂等 =================

class TestManualKpIdempotent:
    def test_same_normalized_name_reuses_kp(self, conn):
        env = _env(conn)
        a = env["svc"].get_or_create_manual_knowledge_point("强化学习基础", "")
        b = env["svc"].get_or_create_manual_knowledge_point(" 强化学习基础 ", "")
        assert a["id"] == b["id"]
        assert len(env["arepo"].list_knowledge_points()) == 1

    def test_different_names_create_distinct_kp(self, conn):
        env = _env(conn)
        a = env["svc"].get_or_create_manual_knowledge_point("强化学习基础", "")
        b = env["svc"].get_or_create_manual_knowledge_point("PPO", "")
        assert a["id"] != b["id"]
        assert len(env["arepo"].list_knowledge_points()) == 2

    def test_no_substring_match(self, conn):
        env = _env(conn)
        env["svc"].get_or_create_manual_knowledge_point("强化学习基础", "")
        env["svc"].get_or_create_manual_knowledge_point("基础", "")
        assert len(env["arepo"].list_knowledge_points()) == 2


# ================= 25~26：来源标签 =================

class TestSourceTags:
    def test_todo_tag(self, conn, qtbot):
        env = _env(conn)
        t = env["svc"].create_todo("刷题", scheduled_date=TODAY)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert w.source_tag_label.text() == "自定义"

    def test_manual_knowledge_tag(self, conn, qtbot):
        env = _env(conn)
        t = env["svc"].create_knowledge_task("强化学习基础", scheduled_date=TODAY)
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert w.source_tag_label.text() == "自定义知识"

    def test_agent_generated_tag(self, conn, qtbot):
        env = _env(conn)
        t = env["repo"].create("Embedding 与向量检索", scheduled_date=TODAY,
                               source="generated", task_type="new")
        w = TaskWidget(env["repo"].get(t.id))
        qtbot.addWidget(w)
        assert w.source_tag_label.text() == "Agent 规划"


# ================= 对话框数据 =================

class TestAddDialog:
    def test_todo_payload(self, qtbot):
        dlg = AddLearningTaskDialog(topics=[{"id": 1, "name": "T"}],
                                    default_date=TODAY)
        qtbot.addWidget(dlg)
        dlg.title_edit.setText("刷 LeetCode 3 道")
        dlg.minutes_spin.setValue(45)
        payload = dlg.result_payload()
        assert payload["kind"] == KIND_TODO
        assert payload["title"] == "刷 LeetCode 3 道"
        assert payload["estimated_minutes"] == 45
        assert payload["scheduled_date"] == TODAY
        assert payload["topic_id"] is None

    def test_knowledge_existing_topic_payload(self, qtbot):
        dlg = AddLearningTaskDialog(topics=[{"id": 7, "name": "Transformer"}],
                                    default_date=TODAY)
        qtbot.addWidget(dlg)
        dlg.knowledge_radio.setChecked(True)
        dlg.title_edit.setText("Transformer 复习")
        dlg.topic_radio.setChecked(True)
        dlg.topic_combo.setCurrentIndex(0)
        payload = dlg.result_payload()
        assert payload["kind"] == KIND_KNOWLEDGE
        assert payload["topic_id"] == 7

    def test_knowledge_temp_payload(self, qtbot):
        dlg = AddLearningTaskDialog(topics=[], default_date=TODAY)
        qtbot.addWidget(dlg)
        dlg.knowledge_radio.setChecked(True)
        dlg.title_edit.setText("强化学习基础")
        payload = dlg.result_payload()
        assert payload["kind"] == KIND_KNOWLEDGE
        assert payload["topic_id"] is None


# ================= 28：Windows UI 添加流程 =================

class TestAddFlowUI:
    def _window(self, qtbot, conn):
        from app.services.date_service import DateService
        from app.ui.main_window import MainWindow

        env = _env(conn)
        w = MainWindow(
            task_service=TaskService(env["repo"]),
            date_service=DateService(env["repo"]),
            today_provider=lambda: TODAY,
            assessment_repo=env["arepo"],
            study_plan_service=env["sps"],
            manual_task_service=env["svc"],
        )
        qtbot.addWidget(w)
        return env, w

    def test_add_button_present(self, qtbot, conn):
        _, w = self._window(qtbot, conn)
        assert w.add_task_btn.text() == "＋ 添加学习任务"

    def test_add_todo_via_window(self, qtbot, conn, monkeypatch):
        env, w = self._window(qtbot, conn)
        from app.ui import main_window as mw
        from app.ui.manual_task_dialog import KIND_TODO

        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog

                return QDialog.DialogCode.Accepted

            def result_payload(self):
                return {
                    "kind": KIND_TODO, "title": "刷 LeetCode 3 道",
                    "description": "", "estimated_minutes": 45,
                    "scheduled_date": TODAY, "topic_id": None,
                }

        monkeypatch.setattr(mw, "AddLearningTaskDialog", FakeDialog)
        w._on_add_learning_task()
        todos = [t for t in env["repo"].list_by_date(TODAY)
                 if t.task_type == "manual"]
        assert [t.title for t in todos] == ["刷 LeetCode 3 道"]

    def test_add_knowledge_via_window(self, qtbot, conn, monkeypatch):
        env, w = self._window(qtbot, conn)
        from app.ui import main_window as mw
        from app.ui.manual_task_dialog import KIND_KNOWLEDGE

        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog

                return QDialog.DialogCode.Accepted

            def result_payload(self):
                return {
                    "kind": KIND_KNOWLEDGE, "title": "强化学习基础",
                    "description": "RL 基础", "estimated_minutes": 60,
                    "scheduled_date": TODAY, "topic_id": None,
                }

        monkeypatch.setattr(mw, "AddLearningTaskDialog", FakeDialog)
        w._on_add_learning_task()
        tasks = [t for t in env["repo"].list_by_date(TODAY)
                 if t.source == "manual" and t.task_type == "new"]
        assert [t.title for t in tasks] == ["强化学习基础"]
        assert tasks[0].knowledge_point_id is not None
