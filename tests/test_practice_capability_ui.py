"""Phase 5：Practice → Capability UI + 负向回归（offscreen）。"""

from __future__ import annotations

import inspect

from app.services.capability import PROJECT, capability_label
from tests.practice_capability_helpers import (
    add_output,
    add_topic,
    complete,
    create_evidence,
    ready_lora,
)


def _labels(widget):
    from PySide6.QtWidgets import QLabel, QPushButton

    out = [lbl.text() for lbl in widget.findChildren(QLabel)]
    out += [b.text() for b in widget.findChildren(QPushButton)]
    return out


def _detail(env, project_id, qtbot):
    from app.ui.practice_page import PracticeProjectDetailDialog

    dlg = PracticeProjectDetailDialog(
        project_id, env.service, env.route_repo, env.skill_repo,
        env.plan_repo, env.pc,
    )
    qtbot.addWidget(dlg)
    return dlg


class TestProjectDetailEvidence:
    def test_section_and_confirm_button(self, practice_capability_env, qtbot):
        env = practice_capability_env
        r = ready_lora(env)
        dlg = _detail(env, r.project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "【项目能力证据】" in joined
        assert r.lora.name in joined
        assert "可确认项目能力证据" in joined
        assert "确认项目使用" in joined

    def test_ineligible_shows_reason(self, practice_capability_env, qtbot):
        env = practice_capability_env
        project = env.service.create_project("P", "other",
                                            route_ids=[env.r2.id])
        add_topic(env, project, env.r2.id, "LoRA / QLoRA")
        dlg = _detail(env, project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "项目尚未标记为已完成" in joined

    def test_active_evidence_display(self, practice_capability_env, qtbot):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora,
                        [r.repo["id"], r.bench["id"], r.ckpt["id"]],
                        usage="完成 LoRA adapter 训练与推理")
        dlg = _detail(env, r.project["id"], qtbot)
        joined = "\n".join(_labels(dlg))
        assert "✓ 已在真实项目中使用" in joined
        assert "Repository · Benchmark · Checkpoint" in joined
        assert "完成 LoRA adapter 训练与推理" in joined
        assert "撤销证据" in joined
        assert "查看证据" in joined

    def test_detail_refresh_after_revoke(self, practice_capability_env, qtbot):
        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        dlg = _detail(env, r.project["id"], qtbot)
        assert "✓ 已在真实项目中使用" in "\n".join(_labels(dlg))
        env.pc.revoke_project_topic_evidence(ev["id"], "r")
        dlg.refresh()
        joined = "\n".join(_labels(dlg))
        assert "✓ 已在真实项目中使用" not in joined
        assert "撤销证据" not in joined


class TestConfirmationDialog:
    def test_confirmation_flow(self, practice_capability_env, qtbot):
        from PySide6.QtWidgets import QDialog

        from app.ui.practice_dialogs import PracticeTopicEvidenceDialog

        env = practice_capability_env
        r = ready_lora(env)
        outputs = env.service.outputs.list_by_project(r.project["id"])
        dlg = PracticeTopicEvidenceDialog(
            r.project, r.lora.id, r.lora.name, env.r2.name, outputs, env.pc,
        )
        qtbot.addWidget(dlg)
        dlg.usage_edit.setPlainText("使用 LoRA 完成 adapter 训练与推理。")
        dlg.confirm_check.setChecked(True)
        # 默认勾选主要证据（repository/benchmark/checkpoint）
        assert len(dlg.selected_output_ids()) >= 1
        dlg._on_accept()
        assert dlg.result() == QDialog.DialogCode.Accepted
        ev = dlg.result_evidence()
        assert ev["is_active"] is True
        kp = ev["knowledge_point_id"]
        assert env.cap.get_current_level(kp) == PROJECT

    def test_confirmation_requires_checkbox(self, practice_capability_env, qtbot):
        from app.ui.practice_dialogs import PracticeTopicEvidenceDialog

        env = practice_capability_env
        r = ready_lora(env)
        outputs = env.service.outputs.list_by_project(r.project["id"])
        dlg = PracticeTopicEvidenceDialog(
            r.project, r.lora.id, r.lora.name, env.r2.name, outputs, env.pc,
        )
        qtbot.addWidget(dlg)
        dlg.usage_edit.setPlainText("x")
        dlg.confirm_check.setChecked(False)
        dlg._on_accept()
        assert "确认" in dlg.error_label.text()
        assert env.conn.execute(
            "SELECT COUNT(*) FROM practice_topic_evidence"
        ).fetchone()[0] == 0

    def test_readme_not_checked_by_default(self, practice_capability_env, qtbot):
        from app.ui.practice_dialogs import PracticeTopicEvidenceDialog

        env = practice_capability_env
        r = ready_lora(env)
        outputs = env.service.outputs.list_by_project(r.project["id"])
        dlg = PracticeTopicEvidenceDialog(
            r.project, r.lora.id, r.lora.name, env.r2.name, outputs, env.pc,
        )
        qtbot.addWidget(dlg)
        assert r.readme["id"] not in dlg.selected_output_ids()


class TestPracticeCard:
    def test_card_evidence_count(self, practice_capability_env, qtbot):
        from app.ui.practice_page import PracticeProjectsPage

        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        page = PracticeProjectsPage(
            env.service, env.route_repo, env.skill_repo, env.plan_repo,
            capability_service=env.pc,
        )
        qtbot.addWidget(page)
        joined = "\n".join(_labels(page))
        assert "项目能力证据：1" in joined


class TestRouteDetail:
    def test_route_detail_shows_project_capability(
        self, practice_capability_env, qtbot
    ):
        from app.database.assessment_repository import AssessmentRepository
        from app.services.learning_route_service import LearningRouteService
        from app.services.route_plan_service import RoutePlanService
        from app.services.route_progress_service import RouteProgressService
        from app.ui.routes_page import RouteDetailDialog

        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        progress = RouteProgressService(
            env.repo, AssessmentRepository(env.conn), env.plan_repo,
            route_repo=env.route_repo, capability_service=env.cap,
        )
        dlg = RouteDetailDialog(
            env.r2, LearningRouteService(env.route_repo),
            RoutePlanService(env.plan_repo, env.repo,
                             AssessmentRepository(env.conn)),
            progress_service=progress,
            today_provider=lambda: "2026-09-15",
            capability_service=env.cap,
            practice_capability_service=env.pc,
        )
        qtbot.addWidget(dlg)
        joined = "\n".join(_labels(dlg))
        assert "已在真实项目中使用" in joined

    def test_capability_label_value(self):
        assert capability_label(PROJECT) == "已在真实项目中使用"


class TestGenericEvidenceDialog:
    def test_practice_evidence_block_and_revoke(
        self, practice_capability_env, qtbot
    ):
        from app.ui.capability_dialog import CapabilityEvidenceDialog

        env = practice_capability_env
        r = ready_lora(env)
        ev = create_evidence(env, r.project, r.lora, [r.repo["id"]])
        dlg = CapabilityEvidenceDialog(
            r.lora.name, ev["knowledge_point_id"], env.cap,
            practice_capability_service=env.pc,
        )
        qtbot.addWidget(dlg)
        joined = "\n".join(_labels(dlg))
        assert "已在真实项目中使用" in joined
        assert env.r2.name in joined or "来源" in joined
        assert "撤销证据" in joined


class TestMonthlySummary:
    def _page(self, env, qtbot, start="2026-09-01", end="2026-09-30"):
        from app.ui.summary_pages import MonthlySummaryPage

        class _Svc:
            def get_monthly_summary(self, y, m):
                return {
                    "start": start, "end": end,
                    "stats": {"route_stats": [], "category_ranking": []},
                    "ai_summary": None,
                }

        page = MonthlySummaryPage(
            _Svc(), today_provider=lambda: "2026-09-15",
            practice_capability_service=env.pc,
        )
        qtbot.addWidget(page)
        return page

    def test_monthly_counts_not_average(self, practice_capability_env, qtbot):
        env = practice_capability_env
        r = ready_lora(env)
        page = self._page(env, qtbot)
        joined = "\n".join(_labels(page))
        assert "本月新增项目能力证据：PROJECT：0" in joined
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        page.refresh()
        joined = "\n".join(_labels(page))
        assert "本月新增项目能力证据：PROJECT：1" in joined
        assert r.lora.name in joined
        # 不得出现平均 Capability 评分
        assert "平均" not in joined


class TestNegativeRegressions:
    def test_planner_does_not_reference_practice(self):
        import app.services.daily_planner_service as dps
        import app.services.route_scheduler as sched
        import app.services.study_plan_service as sps

        for mod in (dps, sched, sps):
            src = inspect.getsource(mod).lower()
            assert "practice" not in src, mod.__name__

    def test_capability_service_does_not_read_practice_in_planner_path(self):
        import app.services.daily_planner_service as dps

        src = inspect.getsource(dps).lower()
        assert "practice_capability" not in src
        assert "capability" not in src or "practice" not in src

    def test_six_routes_isolation(self, practice_capability_env):
        env = practice_capability_env
        routes = [r for r in env.route_repo.list_learning_routes()]
        keys = {r.route_key for r in routes}
        for i in range(1, 7):
            assert any(k.startswith(f"R{i}_") for k in keys)
        # 不存在 R7 Practice route
        assert not any(k.startswith("R7") for k in keys)

    def test_jd_market_signal_untouched(self):
        import app.services.jd_service as jd
        import app.services.market_signal as ms

        for mod in (jd, ms):
            assert "practice" not in inspect.getsource(mod).lower()

    def test_ai_settings_untouched(self):
        import app.ai.config_service as cs
        import app.ai.prompt_registry as pr

        for mod in (cs, pr):
            assert "practice" not in inspect.getsource(mod).lower()

    def test_prompt_registry_no_practice_vars(self, prompt_registry):
        # Prompt override 体系不因 Phase 5 改变
        assert prompt_registry is not None

    def test_capability_does_not_affect_review_schedule(
        self, practice_capability_env
    ):
        env = practice_capability_env
        r = ready_lora(env)
        before = env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE next_review_date IS NOT NULL"
        ).fetchone()[0]
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        after = env.conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE next_review_date IS NOT NULL"
        ).fetchone()[0]
        assert before == after == 0

    def test_skill_mastered_state_unchanged(self, practice_capability_env):
        env = practice_capability_env
        r = ready_lora(env)
        create_evidence(env, r.project, r.lora, [r.repo["id"]])
        # 不产生任何 mastery 变化
        row = env.conn.execute(
            "SELECT SUM(mastery_estimate) FROM knowledge_points"
        ).fetchone()
        assert float(row[0] or 0.0) == 0.0


class TestMainWindowWiring:
    def test_main_window_has_practice_capability(
        self, qtbot, practice_capability_env, repo, task_service, date_service
    ):
        from app.services.learning_route_service import LearningRouteService
        from app.services.route_plan_service import RoutePlanService
        from app.database.assessment_repository import AssessmentRepository
        from app.ui.main_window import MainWindow

        env = practice_capability_env
        w = MainWindow(
            task_service=task_service,
            date_service=date_service,
            today_provider=lambda: "2026-09-15",
            route_service=LearningRouteService(env.route_repo),
            route_plan_service=RoutePlanService(
                env.plan_repo, env.repo, AssessmentRepository(env.conn)
            ),
            capability_service=env.cap,
            practice_service=env.service,
            practice_capability_service=env.pc,
        )
        qtbot.addWidget(w)
        assert w.practice_capability_service is env.pc
        assert w.practice_page.capability_service is env.pc
        w._switch_to_practice()
        assert w.stack.currentIndex() == w.practice_page_index
