"""PromptRegistry / renderer / override 测试。

覆盖：Registry 列出全部生产 Prompt、key 唯一、default 可读、无 override 用默认、
override 生效、save 后立即生效、reset 恢复默认、必需/未知变量校验、渲染安全、
runtime context 与 template 分离、preview 与真实渲染一致。
"""

from __future__ import annotations

import pytest

from app.ai.prompt_defaults import DEFAULT_PROMPT_DEFINITIONS
from app.ai.prompt_registry import (
    PromptOverrideRepository,
    PromptRegistry,
    PromptRenderError,
    PromptValidationError,
    UnknownPromptKeyError,
    extract_variables,
    render_template,
    validate_template,
)


class TestRegistryMetadata:
    def test_all_keys_unique(self):
        keys = [d.key for d in DEFAULT_PROMPT_DEFINITIONS]
        assert len(keys) == len(set(keys))

    def test_expected_production_keys_present(self):
        keys = {d.key for d in DEFAULT_PROMPT_DEFINITIONS}
        assert keys == {
            "task_review.system", "task_review.user",
            "planner.system", "planner.user",
            "assessment.generate.system", "assessment.generate.user",
            "assessment.judge.system", "assessment.judge.user",
            "summary.monthly.system", "summary.monthly.user",
            "route_builder.system", "route_builder.user",
            "route_suggestion.system", "route_suggestion.user",
            "jd_parse.system", "jd_parse.user",
            "resume_material.system", "resume_material.user",
        }

    def test_defaults_readable(self, prompt_registry):
        for d in prompt_registry.definitions():
            assert prompt_registry.default_template(d.key).strip()

    def test_categories_and_grouping(self, prompt_registry):
        cats = prompt_registry.categories()
        assert "Planner" in cats
        assert "Assessment" in cats
        assert "Prompt" not in cats

    def test_unknown_key_raises(self, prompt_registry):
        with pytest.raises(UnknownPromptKeyError):
            prompt_registry.definition("nope.nope")


class TestRenderer:
    def test_simple_substitution(self):
        assert render_template("a {{x}} b", {"x": "Y"}) == "a Y b"

    def test_missing_variable_raises(self):
        with pytest.raises(PromptRenderError):
            render_template("a {{x}} b", {})

    def test_whole_empty_line_removed(self):
        out = render_template("A\n{{x}}\nB", {"x": ""})
        assert out == "A\nB"

    def test_multiple_newlines_collapsed(self):
        out = render_template("A\n\n\n\nB", {})
        assert out == "A\n\nB"

    def test_single_braces_are_literal(self):
        # JSON 花括号不是变量，绝不做 str.format
        out = render_template('{"a": {{n}}}', {"n": 1})
        assert out == '{"a": 1}'

    def test_extract_variables_order_and_unique(self):
        assert extract_variables("{{a}} {{b}} {{a}}") == ["a", "b"]

    def test_no_eval_or_format_injection(self):
        # 模板里若含 {0.__class__} 之类，也应原样保留，不执行
        out = render_template("x {0.__class__} y", {})
        assert out == "x {0.__class__} y"


class TestValidation:
    def test_missing_required_detected(self, prompt_registry):
        d = prompt_registry.definition("task_review.user")
        result = validate_template(d, "没有变量的模板")
        assert "task_title" in result.missing_required
        assert result.ok is False

    def test_unknown_variable_detected(self, prompt_registry):
        d = prompt_registry.definition("task_review.user")
        template = (
            "{{task_title}} {{reason}} {{output_instruction}} {{routes_name}}"
        )
        result = validate_template(d, template)
        assert result.unknown_variables == ["routes_name"]
        assert result.ok is False

    def test_valid_template_passes(self, prompt_registry):
        d = prompt_registry.definition("task_review.user")
        template = "{{task_title}} {{reason}} {{output_instruction}}"
        assert validate_template(d, template).ok is True


class TestOverrideLifecycle:
    def test_no_override_uses_default(self, prompt_registry):
        key = "planner.system"
        assert prompt_registry.is_customized(key) is False
        assert prompt_registry.effective_template(key) == \
            prompt_registry.default_template(key)

    def test_set_override_takes_effect_immediately(self, prompt_registry):
        key = "assessment.generate.system"
        custom = "CUSTOM-INSTRUCTION {{knowledge_point_name}}"
        prompt_registry.set_override("assessment.generate.user",
                                     prompt_registry.default_template("assessment.generate.user"))
        prompt_registry.set_override(key, "CUSTOM-INSTRUCTION")
        assert prompt_registry.is_customized(key) is True
        assert prompt_registry.effective_template(key) == "CUSTOM-INSTRUCTION"

    def test_save_then_render_uses_override(self, prompt_registry):
        key = "summary.monthly.system"
        prompt_registry.set_override(key, "自定义总结系统提示")
        assert prompt_registry.render(key, {}) == "自定义总结系统提示"

    def test_reset_restores_default(self, prompt_registry):
        key = "summary.monthly.system"
        default = prompt_registry.default_template(key)
        prompt_registry.set_override(key, "临时覆盖")
        assert prompt_registry.reset(key) is True
        assert prompt_registry.is_customized(key) is False
        assert prompt_registry.effective_template(key) == default

    def test_invalid_override_rejected(self, prompt_registry):
        key = "planner.user"
        with pytest.raises(PromptValidationError) as exc:
            prompt_registry.set_override(key, "缺少变量的模板")
        assert "context_json" in str(exc.value)
        # 拒绝后不写入
        assert prompt_registry.is_customized(key) is False

    def test_override_persisted_in_db(self, conn, prompt_registry):
        prompt_registry.set_override("planner.system", "PERSISTED")
        repo = PromptOverrideRepository(conn)
        assert repo.get("planner.system") == "PERSISTED"
        # 新 registry 重新加载时也能看到
        fresh = PromptRegistry(PromptOverrideRepository(conn))
        assert fresh.effective_template("planner.system") == "PERSISTED"

    def test_cache_isolated_from_db_after_construction(self, conn, prompt_registry):
        # 构造后其它写入不自动同步（单实例 UI 场景下 set_override 会同步）
        assert prompt_registry.is_customized("jd_parse.system") is False


class TestPreviewSeparation:
    def test_preview_exposes_template_and_context_separately(self, prompt_registry):
        key = "task_review.user"
        from app.ai.prompts import build_task_review_vars
        from app.database.repository import Task

        task = Task(id=1, title="读书", description="d", category="学习",
                    estimated_minutes=30, priority=2, status="active",
                    reason=None, scheduled_date="2026-01-05", postpone_count=0,
                    created_at="", updated_at="", completed_at=None,
                    not_done_at=None)
        context = build_task_review_vars(task, "没时间")
        preview = prompt_registry.preview(key, context)
        # template 与 rendered 分离
        assert "{{task_title}}" in preview.template
        assert "{{task_title}}" not in preview.rendered
        assert "读书" in preview.rendered
        assert preview.context["reason"] == "没时间"

    def test_preview_uses_override(self, prompt_registry):
        key = "task_review.user"
        prompt_registry.set_override(
            key, "OVERRIDE {{task_title}} {{reason}} {{output_instruction}}"
        )
        preview = prompt_registry.preview(
            key, {"task_title": "T", "reason": "R", "output_instruction": "O"}
        )
        assert preview.is_customized is True
        assert preview.rendered == "OVERRIDE T R O"
