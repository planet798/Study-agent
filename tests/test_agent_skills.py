"""Agent-4 static learning strategy model, registry and deterministic selection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect

import pytest

from app.agent.skills import (
    AgentSkill,
    AgentSkillNotFoundError,
    AgentSkillRegistry,
    AgentSkillSelector,
    AgentSkillValidationError,
    FALLBACK_SKILL_KEY,
    build_default_agent_skill_registry,
)
from app.services.learning_activity import (
    ACTIVITY_CODE_READING,
    ACTIVITY_EXPERIMENT,
    ACTIVITY_INTERVIEW,
    ACTIVITY_PRACTICE,
    ACTIVITY_THEORY,
)

EXPECTED_KEYS = (
    "general-study", "teach-concept", "code-reading", "experiment-coach",
    "interview-drill", "practice-coach",
)


def test_default_registry_has_six_static_skills_in_stable_order():
    registry = build_default_agent_skill_registry()
    assert tuple(skill.key for skill in registry.all()) == EXPECTED_KEYS
    assert all(isinstance(skill, AgentSkill) for skill in registry.all())
    assert all(skill.instruction.strip() for skill in registry.all())
    assert sum(skill.key == FALLBACK_SKILL_KEY for skill in registry.all()) == 1


def test_activity_kind_mapping_uses_existing_constants_deterministically():
    registry = build_default_agent_skill_registry()
    expected = {
        ACTIVITY_THEORY: "teach-concept",
        ACTIVITY_CODE_READING: "code-reading",
        ACTIVITY_EXPERIMENT: "experiment-coach",
        ACTIVITY_INTERVIEW: "interview-drill",
        ACTIVITY_PRACTICE: "practice-coach",
    }
    for activity, key in expected.items():
        assert registry.select_for_activity(activity).key == key


def test_none_empty_and_unknown_activity_use_general_fallback():
    registry = build_default_agent_skill_registry()
    assert registry.select_for_activity(None).key == "general-study"
    assert registry.select_for_activity("").key == "general-study"
    assert registry.select_for_activity("unknown-activity").key == "general-study"
    selector = AgentSkillSelector(registry)
    assert selector.select(None).key == "general-study"
    assert selector.select({}).key == "general-study"
    assert selector.select({"task": {"activity_kind": "future-kind"}}).key == "general-study"


def test_selector_reads_only_the_compact_context_activity_kind():
    registry = build_default_agent_skill_registry()
    selector = AgentSkillSelector(registry)
    selected = selector.select({"task": {"activity_kind": ACTIVITY_THEORY}})
    assert selected.key == "teach-concept"
    params = inspect.signature(selector.select).parameters
    assert tuple(params) == ("task_context",)
    source = inspect.getsource(AgentSkillSelector)
    assert "sqlite" not in source.lower()
    assert "Repository" not in source
    assert "ToolRegistry.execute" not in source


def test_registry_duplicate_invalid_key_empty_instruction_and_missing_fallback():
    registry = AgentSkillRegistry()
    fallback = AgentSkill(
        key="general-study", title="General", description="Fallback",
        instruction="Teach the current task.",
    )
    registry.register(fallback)
    with pytest.raises(AgentSkillValidationError, match="duplicate"):
        registry.register(fallback)
    with pytest.raises(AgentSkillValidationError, match="key"):
        registry.register(AgentSkill(
            key="Bad Key", title="Bad", description="", instruction="x",
        ))
    with pytest.raises(AgentSkillValidationError, match="instruction"):
        registry.register(AgentSkill(
            key="empty-instruction", title="Empty", description="", instruction="  ",
        ))

    no_fallback = AgentSkillRegistry()
    no_fallback.register(AgentSkill(
        key="teach-concept", title="Teach", description="Theory",
        instruction="Explain concepts.", activity_kinds=(ACTIVITY_THEORY,),
    ))
    with pytest.raises(AgentSkillValidationError, match="fallback"):
        no_fallback.select_for_activity(None)


def test_unknown_skill_lookup_is_a_controlled_error():
    with pytest.raises(AgentSkillNotFoundError):
        build_default_agent_skill_registry().get("not-registered")


def test_builtin_skill_instructions_cover_their_learning_behaviors():
    registry = build_default_agent_skill_registry()
    by_key = {skill.key: skill for skill in registry.all()}

    teach = by_key["teach-concept"].instruction
    for concept in ("概念", "机制", "例子", "理解"):
        assert concept in teach

    code = by_key["code-reading"].instruction
    assert "没有提供代码内容" in code
    assert "不得声称已读取本地仓库或文件" in code

    experiment = by_key["experiment-coach"].instruction
    for concept in ("Hypothesis", "Minimal Experiment", "Expected Observation",
                    "Actual Observation", "Interpretation", "Next Step"):
        assert concept in experiment
    assert "不能声称自己执行了代码" in experiment

    interview = by_key["interview-drill"].instruction
    assert "一个主要问题" in interview
    assert "先让用户回答" in interview
    assert "追问" in interview

    practice = by_key["practice-coach"].instruction
    for concept in ("deliverable", "acceptance criteria", "expected artifact"):
        assert concept in practice
    assert "不得声称文件已创建" in practice

    general = by_key["general-study"].instruction
    assert "当前 Task" in general
    assert "只读工具" in general
    assert "不要假设用户已经掌握" in general


def test_agent_skill_is_immutable_static_behavior_data_only():
    skill = build_default_agent_skill_registry().get("teach-concept")
    with pytest.raises(FrozenInstanceError):
        skill.key = "other"
    assert not any(hasattr(skill, attr) for attr in (
        "service", "repository", "conn", "tool_registry", "ai_client",
    ))


def test_skill_package_does_not_import_career_skill_or_database_implementation():
    from pathlib import Path
    import app.agent.skills as skills_package

    package_path = Path(skills_package.__file__).parent
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package_path.glob("*.py")
    )
    for forbidden in (
        "SkillService", "SkillRepository", "skill_repository", "sqlite3",
        "from ..database", "AgentToolRegistry",
    ):
        assert forbidden not in sources
