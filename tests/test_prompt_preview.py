"""PromptPreviewService 测试：真实数据 / route 隔离 / template-context 分离。"""

from __future__ import annotations

from app.ai.planner_context import PlanningContext
from app.ai.prompt_registry import PromptOverrideRepository, PromptRegistry
from app.services.prompt_preview_service import PromptPreviewService


class FakePlanner:
    max_daily_minutes = 180
    planner = None

    def __init__(self, name: str):
        self.name = name

    def build_context(self, date_str: str) -> PlanningContext:
        ctx = PlanningContext(current_date=date_str)
        ctx.route_name = f"route-{self.name}"
        ctx.route_goal = f"goal-{self.name}"
        ctx.current_daily_limit = 180
        return ctx


class FakeScheduler:
    def __init__(self):
        self.calls: list[int] = []

    def _make_route_planner(self, route_id: int) -> FakePlanner:
        self.calls.append(route_id)
        return FakePlanner(f"R{route_id}")


def _service(conn) -> tuple[PromptPreviewService, PromptRegistry, FakeScheduler]:
    registry = PromptRegistry(PromptOverrideRepository(conn))
    scheduler = FakeScheduler()
    svc = PromptPreviewService(
        registry, today_provider=lambda: "2026-01-05", scheduler=scheduler
    )
    return svc, registry, scheduler


class TestPlannerPreview:
    def test_route_selection_uses_route_scoped_planner(self, conn):
        svc, _, scheduler = _service(conn)
        conv = svc.preview_conversation("planner.user", route_id=1)
        assert scheduler.calls == [1]
        assert "route-R1" in conv.user.rendered

    def test_route_a_preview_does_not_leak_route_b(self, conn):
        svc, _, _ = _service(conn)
        a = svc.preview_conversation("planner.user", route_id=1)
        b = svc.preview_conversation("planner.user", route_id=2)
        assert "route-R1" in a.user.rendered
        assert "route-R2" not in a.user.rendered
        assert "route-R2" in b.user.rendered
        assert "route-R1" not in b.user.rendered

    def test_preview_rendered_matches_registry_render(self, conn):
        svc, registry, _ = _service(conn)
        conv = svc.preview_conversation("planner.user", route_id=1)
        # final preview 与真实 request messages 完全一致（同一渲染入口）
        assert conv.user.rendered == registry.render("planner.user", conv.context)

    def test_system_and_user_separated(self, conn):
        svc, _, _ = _service(conn)
        conv = svc.preview_conversation("planner.system", route_id=1)
        assert conv.system is not None
        assert conv.user is not None
        assert conv.system.definition.role == "system"
        assert conv.user.definition.role == "user"


class TestTemplateContextSeparation:
    def test_template_keeps_placeholder_context_has_value(self, conn):
        svc, _, _ = _service(conn)
        conv = svc.preview_conversation("planner.user", route_id=1)
        assert "{{context_json}}" in conv.user.template
        assert "{{context_json}}" not in conv.user.rendered
        assert conv.context["context_json"]

    def test_missing_real_data_marked_as_sample(self, conn):
        svc, _, _ = _service(conn)
        # 没有 real services 时，assessment 使用示例数据并明确标注
        conv = svc.preview_conversation("assessment.generate.user")
        assert "示例数据" in conv.user.rendered


class TestOverrideReflectedInPreview:
    def test_override_used_in_preview(self, conn):
        svc, registry, _ = _service(conn)
        registry.set_override(
            "task_review.user",
            "PREVIEW-OVERRIDE {{task_title}} {{reason}} {{output_instruction}}",
        )
        conv = svc.preview_conversation("task_review.user")
        assert "PREVIEW-OVERRIDE" in conv.user.rendered
        assert conv.user.is_customized is True
