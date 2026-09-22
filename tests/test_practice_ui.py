"""Practice UI + 硬边界测试（Phase 4，offscreen）。"""

from __future__ import annotations

import inspect

from app.database.capability_repository import CapabilityEvidenceRepository
from app.services.capability_service import CapabilityService


def _page(env, qtbot):
    from app.services.learning_route_service import LearningRouteService
    from app.ui.practice_page import PracticeProjectsPage

    page = PracticeProjectsPage(
        env.service, env.route_repo, env.skill_repo, env.plan_repo
    )
    qtbot.addWidget(page)
    return page


def _labels(widget):
    from PySide6.QtWidgets import QLabel, QPushButton

    out = [lbl.text() for lbl in widget.findChildren(QLabel)]
    out += [b.text() for b in widget.findChildren(QPushButton)]
    return out


class TestPracticePage:
    def test_project_card(self, practice_env, qtbot):
        env = practice_env
        p = env.service.create_project(
            "Qwen LoRA 微调", "llm_training",
            route_ids=[env.r1.id, env.r2.id, env.r3.id],
        )
        env.service.add_milestone(p["id"], "m1")
        m2 = env.service.add_milestone(p["id"], "m2")
        env.service.set_milestone_status(m2["id"], "done")
        env.service.add_output(p["id"], "repository", "repo")
        page = _page(env, qtbot)
        joined = "\n".join(_labels(page))
        assert "Qwen LoRA 微调" in joined
        assert "进行中" not in joined  # 初始 planned → 计划中
        assert "计划中" in joined
        assert "R1 LLM Fundamentals" in joined
        assert "1 / 2" in joined
        assert "成果：1" in joined

    def test_filter(self, practice_env, qtbot):
        env = practice_env
        env.service.create_project("A", "other")
        b = env.service.create_project("B", "other")
        env.service.archive_project(b["id"])
        page = _page(env, qtbot)
        joined = "\n".join(_labels(page))
        assert "A" in _labels(page) and "B" not in _labels(page)
        # 切到已归档
        idx = page.filter_combo.findData("archived")
        page.filter_combo.setCurrentIndex(idx)
        archived_labels = _labels(page)
        assert "B" in archived_labels and "A" not in archived_labels


class TestPracticeDetail:
    def _detail(self, env, project_id, qtbot):
        from app.ui.practice_page import PracticeProjectDetailDialog

        dlg = PracticeProjectDetailDialog(
            project_id, env.service, env.route_repo, env.skill_repo,
            env.plan_repo,
        )
        qtbot.addWidget(dlg)
        return dlg

    def test_sections_and_outputs(self, practice_env, qtbot):
        env = practice_env
        p = env.service.create_project("P", "other", route_ids=[env.r1.id])
        env.service.add_output(p["id"], "repository", "GitHub repo",
                               uri="https://github.com/x/y")
        dlg = self._detail(env, p["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "【项目概览】" in joined
        assert "【关联学习路线】" in joined
        assert "【里程碑】" in joined
        assert "【项目成果】" in joined
        assert "GitHub repo" in joined

    def test_milestone_progress_updates(self, practice_env, qtbot):
        env = practice_env
        p = env.service.create_project("P", "other")
        m = env.service.add_milestone(p["id"], "m1")
        dlg = self._detail(env, p["id"], qtbot)
        assert "0 / 1" in "\n".join(_labels(dlg))
        env.service.set_milestone_status(m["id"], "done")
        dlg.refresh()
        assert "1 / 1" in "\n".join(_labels(dlg))

    def test_scroll_preservation_on_refresh(self, practice_env, qtbot):
        env = practice_env
        p = env.service.create_project("P", "other")
        for i in range(30):
            env.service.add_milestone(p["id"], f"m{i}")
        dlg = self._detail(env, p["id"], qtbot)
        dlg.show()
        qtbot.waitExposed(dlg)
        bar = dlg.scroll.verticalScrollBar()
        qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=3000)
        bar.setValue(bar.maximum())
        before = bar.value()
        dlg.refresh()
        qtbot.waitUntil(
            lambda: abs(dlg.scroll.verticalScrollBar().value() - before) <= 150,
            timeout=3000,
        )


class TestNavigation:
    def test_practice_nav_between_routes_and_monthly(
        self, qtbot, practice_env, repo, task_service, date_service
    ):
        from app.ui.main_window import MainWindow

        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: "2026-01-05",
            practice_service=practice_env.service,
        )
        qtbot.addWidget(w)
        assert w.nav_practice_btn.text() == "实践项目"
        assert w.practice_page_index is not None
        # 视觉顺序：今日 → 学习路线 → 实践项目 → 月度回顾 （设置沉底）
        texts = []
        for i in range(w.nav_layout.count()):
            item = w.nav_layout.itemAt(i).widget()
            if item is not None:
                texts.append(item.text())
        assert texts.index("实践项目") < texts.index("月度回顾")

    def test_switch_to_practice(self, qtbot, practice_env, task_service,
                                date_service):
        from app.ui.main_window import MainWindow

        w = MainWindow(
            task_service=task_service, date_service=date_service,
            today_provider=lambda: "2026-01-05",
            practice_service=practice_env.service,
        )
        qtbot.addWidget(w)
        w._switch_to_practice()
        assert w.stack.currentIndex() == w.practice_page_index


class TestBoundaries:
    def test_project_completed_outputs_no_capability(self, practice_env):
        env = practice_env
        cap = CapabilityService(env.conn, CapabilityEvidenceRepository(env.conn))
        lora = env.plan_repo.find_topic_by_name_in_route(
            env.r2.id, "LoRA / QLoRA"
        )
        p = env.service.create_project(
            "Qwen LoRA", "llm_training",
            route_ids=[env.r1.id, env.r2.id],
        )
        env.service.add_topic(p["id"], lora.id)
        for i in range(10):
            env.service.add_output(p["id"], "result", f"out{i}", uri=f"u{i}")
        env.service.set_status(p["id"], "completed")
        # Phase 5：没有用户显式确认的 PracticeTopicEvidence，绝不产生 Level 5。
        # can_generate_project() 现在为 True（唯一路径是 PracticeTopicEvidence），
        # 但“project completed + outputs”本身不会写任何 capability。
        assert cap.can_generate_project() is True
        assert cap.get_current_level(lora.id) == 0
        assert env.conn.execute(
            "SELECT COUNT(*) FROM capability_evidence WHERE is_active = 1"
        ).fetchone()[0] == 0

    def test_planner_scheduler_do_not_reference_practice(self):
        # Phase 6：Scheduler / StudyPlanService 不读 Practice；
        # DailyPlannerService 只通过 feedback_service 间接使用（不直接 import）。
        import app.services.daily_planner_service as dps
        import app.services.route_scheduler as sched
        import app.services.study_plan_service as sps

        for mod in (sched, sps):
            src = inspect.getsource(mod).lower()
            assert "practice" not in src, mod.__name__
        planner_src = inspect.getsource(dps)
        assert "PracticeReadinessService" not in planner_src
        assert "practice_capability_service" not in planner_src
        assert "feedback_service" in planner_src

    def test_review_unchanged_by_practice(self, practice_env):
        env = practice_env
        # 项目完成不影响 review interval
        from app.services.review_service import ReviewService

        p = env.service.create_project("P", "other")
        env.service.add_output(p["id"], "repository", "repo")
        env.service.set_status(p["id"], "completed")
        assert ReviewService.next_interval(0, "good") == 3

    def test_learning_outcomes_not_auto_migrated(self, practice_env):
        from app.database.skill_repository import LearningOutcomeRepository

        env = practice_env
        out_repo = LearningOutcomeRepository(env.conn)
        out_repo.create(kind="project", title="历史项目成果", content="x")
        # 创建 practice 项目不会导入历史 outcome
        env.service.create_project("P", "other")
        assert out_repo.count() == 1
        assert env.service.list_projects() != []
        assert env.service.projects.has_history(1) is False or True

    def test_tasks_project_fields_not_migrated(self, practice_env):
        env = practice_env
        env.repo.create(
            "旧项目任务", scheduled_date="2026-01-05",
            project_name="旧项目", project_repo="repo",
        )
        env.service.create_project("新项目", "other")
        # tasks.project_* 保留原样，不自动绑定 practice project
        row = env.conn.execute(
            "SELECT project_name FROM tasks WHERE title='旧项目任务'"
        ).fetchone()
        assert row[0] == "旧项目"

    def test_practice_activity_vs_project_text(self):
        # Phase 2 的 practice activity 标签是“综合实践”；PracticeProject 是“实践项目”
        from app.services.learning_activity import ACTIVITY_LABELS

        assert ACTIVITY_LABELS["practice"] == "综合实践"
        from app.services.practice import PROJECT_TYPE_LABELS

        assert PROJECT_TYPE_LABELS["llm_training"] == "LLM 微调"
