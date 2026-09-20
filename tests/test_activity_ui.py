"""Activity UI 测试（offscreen）：Today badge / Route chips / profile editor。"""

from __future__ import annotations

from app.services.learning_activity import ALL_ACTIVITY_KINDS


def _lora(env):
    return env.plan_repo.find_topic_by_name_in_route(
        env.route_ids["R2_LLM_POST_TRAINING"], "LoRA / QLoRA"
    )


class TestTodayActivityBadge:
    def test_badge_shown(self, activity_env, qtbot):
        from app.ui.task_widget import TaskWidget

        env = activity_env
        lora = _lora(env)
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "experiment")
        task = env.repo.create(
            "LoRA 实验", scheduled_date="2026-01-05", topic_id=lora.id,
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
            component_id=comp["id"], learning_activity_kind="experiment",
        )
        w = TaskWidget(task, route_name="R2 LLM Post-Training")
        qtbot.addWidget(w)
        assert w.activity_tag_label.isVisible() or w.activity_tag_label.text()
        assert "实验" in w.activity_tag_label.text()

    def test_no_badge_for_plain_todo(self, activity_env, qtbot):
        from app.ui.task_widget import TaskWidget

        env = activity_env
        task = env.repo.create("刷邮件", scheduled_date="2026-01-05")
        w = TaskWidget(task)
        qtbot.addWidget(w)
        assert w.activity_tag_label.text() == ""


class TestRouteDetailChips:
    def test_chips_render(self, activity_env, qtbot):
        from app.services.learning_route_service import LearningRouteService
        from app.services.route_plan_service import RoutePlanService
        from app.ui.routes_page import RouteDetailDialog

        env = activity_env
        route = env.route_repo.get_by_key("R2_LLM_POST_TRAINING")
        dlg = RouteDetailDialog(
            route,
            LearningRouteService(env.route_repo, skill_repo=env.skill_repo),
            RoutePlanService(env.plan_repo, env.repo,
                             topic_learning_service=env.tl),
            today_provider=lambda: "2026-01-05",
            topic_learning_service=env.tl,
        )
        qtbot.addWidget(dlg)
        lora = _lora(env)
        chips = dlg._activity_chips(lora.id)
        for label in ("理论", "代码理解", "实验"):
            assert label in chips
        # required pending = ○，optional pending = ◇
        assert "○" in chips
        assert "◇" in chips

    def test_chips_show_complete(self, activity_env, qtbot):
        from app.services.learning_route_service import LearningRouteService
        from app.services.route_plan_service import RoutePlanService
        from app.ui.routes_page import RouteDetailDialog

        env = activity_env
        lora = _lora(env)
        comp = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        t = env.repo.create(
            "done", scheduled_date="2026-01-05", topic_id=lora.id,
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
            component_id=comp["id"], learning_activity_kind="theory",
        )
        env.repo.mark_done(t.id)
        route = env.route_repo.get_by_key("R2_LLM_POST_TRAINING")
        dlg = RouteDetailDialog(
            route,
            LearningRouteService(env.route_repo, skill_repo=env.skill_repo),
            RoutePlanService(env.plan_repo, env.repo,
                             topic_learning_service=env.tl),
            today_provider=lambda: "2026-01-05",
            topic_learning_service=env.tl,
        )
        qtbot.addWidget(dlg)
        chips = dlg._activity_chips(lora.id)
        assert "理论 ✓" in chips


class TestProfileEditor:
    def test_dialog_state_and_validation(self, activity_env, qtbot):
        from app.ui.topic_learning_dialog import TopicLearningProfileDialog

        env = activity_env
        lora = _lora(env)
        dlg = TopicLearningProfileDialog(lora, env.tl)
        qtbot.addWidget(dlg)
        state = dlg.desired_state()
        assert set(state) == set(ALL_ACTIVITY_KINDS)
        assert state["theory"] == (True, True)
        assert state["interview"] == (True, False)
        # 取消所有 required → 校验失败
        for kind in ALL_ACTIVITY_KINDS:
            dlg._rows[kind]["required"].setChecked(False)
        assert dlg.validate() is not None

    def test_dialog_save_applies(self, activity_env, qtbot):
        from app.ui.topic_learning_dialog import TopicLearningProfileDialog

        env = activity_env
        lora = _lora(env)
        dlg = TopicLearningProfileDialog(lora, env.tl)
        qtbot.addWidget(dlg)
        # 把 interview 改成 required
        dlg._rows["interview"]["required"].setChecked(True)
        dlg._on_save()
        assert env.tl.repo.get_by_topic_and_kind(
            lora.id, "interview"
        )["required"] is True

    def test_dialog_blocks_disable_with_active_task(self, activity_env, qtbot):
        from app.ui.topic_learning_dialog import TopicLearningProfileDialog

        env = activity_env
        lora = _lora(env)
        theory = env.tl.repo.get_by_topic_and_kind(lora.id, "theory")
        env.repo.create(
            "active", scheduled_date="2026-01-05", topic_id=lora.id,
            route_id=env.route_ids["R2_LLM_POST_TRAINING"],
            component_id=theory["id"], learning_activity_kind="theory",
        )
        dlg = TopicLearningProfileDialog(lora, env.tl)
        qtbot.addWidget(dlg)
        dlg._rows["theory"]["enabled"].setChecked(False)
        assert dlg.validate() is None  # 至少还有 code_reading/experiment required
        # 保存会因 active task 失败
        from PySide6.QtWidgets import QMessageBox

        warnings = []
        orig = QMessageBox.warning
        QMessageBox.warning = lambda *a, **k: warnings.append(a)
        try:
            dlg._on_save()
        finally:
            QMessageBox.warning = orig
        assert warnings
        assert env.tl.repo.get(theory["id"])["enabled"] is True


class TestPromptOverrideCompat:
    def test_optional_var_does_not_break_old_override(self, prompt_registry):
        key = "planner.user"
        # 旧用户 override 不含新增的 learning_activity_section（optional）
        old = ("请规划 {{context_json}} {{output_instruction}}")
        prompt_registry.set_override(key, old)
        assert prompt_registry.is_customized(key) is True
        rendered = prompt_registry.render(
            key, {"context_json": "{}", "output_instruction": "OUT"}
        )
        assert "OUT" in rendered

    def test_learning_activity_section_renders(self, prompt_registry):
        from app.ai.planner_context import ContextTopic, PlanningContext
        from app.ai.prompts import build_planner_user_vars

        ctx = PlanningContext(current_date="2026-01-05")
        ctx.available_topics = [
            ContextTopic(topic_id=1, title="LoRA", next_activity="experiment",
                         next_activity_label="实验"),
        ]
        ctx.current_daily_limit = 180
        vars_ = build_planner_user_vars(ctx, None)
        assert "实验" in vars_["learning_activity_section"]
        assert "LoRA" in vars_["learning_activity_section"]
