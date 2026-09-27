"""Built-in learning-behavior strategies, kept separate from Career Skills."""

from __future__ import annotations

from ...services.learning_activity import (
    ACTIVITY_CODE_READING,
    ACTIVITY_EXPERIMENT,
    ACTIVITY_INTERVIEW,
    ACTIVITY_PRACTICE,
    ACTIVITY_THEORY,
)
from .base import AgentSkill
from .registry import AgentSkillRegistry

_NO_EXECUTION = (
    "你没有本地文件系统、终端或代码执行能力；除非用户在对话中提供内容，"
    "否则不要声称看到了本地文件、运行了代码/命令或观察到执行结果。"
)
_NO_STATE_MUTATION = (
    "你只能提供对话帮助和读取当前学习信息；不要声称已完成 Task、更新 Mastery / "
    "Capability、完成 Assessment、创建 Evidence 或修改 Project。"
)


def build_default_agent_skill_registry() -> AgentSkillRegistry:
    """Create the fixed built-in Skills in documented, stable order."""
    registry = AgentSkillRegistry()
    registry.register(AgentSkill(
        key="general-study",
        title="通用学习陪伴",
        description="围绕当前 Task 进行通用学习辅导；无明确学习方式时使用。",
        instruction=(
            "围绕当前 Task 推进学习；先理解用户此刻的目标，再用清晰的小步骤解释、"
            "练习或排查问题。必要时使用只读工具查看真实学习状态。"
            "不要假设用户已经掌握，也不要把一次口头确认当成正式验收。"
            + _NO_EXECUTION + _NO_STATE_MUTATION
        ),
        activity_kinds=(),
        priority=-100,
    ))
    registry.register(AgentSkill(
        key="teach-concept",
        title="概念讲解",
        description="帮助用户理解理论概念、机制和关系。",
        instruction=(
            "以理解为目标，围绕当前 Topic 解释概念、核心机制及其关系；"
            "可用一个最小例子说明，并在合适时检查用户是否理解或邀请其复述/推导。"
            "按用户问题灵活组织，不强制固定模板，不因用户说懂了就声称 Mastery 已更新。"
            + _NO_STATE_MUTATION
        ),
        activity_kinds=(ACTIVITY_THEORY,),
        priority=10,
    ))
    registry.register(AgentSkill(
        key="code-reading",
        title="代码阅读",
        description="帮助用户理解其提供的代码结构、控制流和数据流。",
        instruction=(
            "先确认要理解的代码目标，再根据用户实际粘贴/提供的内容解释输入输出、"
            "控制流或数据流、关键实现，并区分框架样板与核心逻辑。"
            "没有提供代码内容时，明确请用户粘贴相关代码；不得声称已读取本地仓库或文件。"
            + _NO_EXECUTION + _NO_STATE_MUTATION
        ),
        activity_kinds=(ACTIVITY_CODE_READING,),
        priority=10,
    ))
    registry.register(AgentSkill(
        key="experiment-coach",
        title="实验引导",
        description="帮助用户设计最小实验并分析用户提供的观察结果。",
        instruction=(
            "按需要引导 Hypothesis → Minimal Experiment → Expected Observation → "
            "Actual Observation → Interpretation → Next Step。可以设计实验并分析用户提供的运行结果；"
            "但没有 Sandbox，不能声称自己执行了代码、命令或实验，也不要伪造观察结果。"
            + _NO_EXECUTION + _NO_STATE_MUTATION
        ),
        activity_kinds=(ACTIVITY_EXPERIMENT,),
        priority=10,
    ))
    registry.register(AgentSkill(
        key="interview-drill",
        title="面试训练",
        description="用聚焦的面试问题训练用户解释和推导当前 Topic。",
        instruction=(
            "围绕当前 Topic 做面试式训练。默认一次提出一个主要问题，先让用户回答；"
            "再指出回答中的正确点、遗漏或错误，并根据回答追问机制、复杂度、边界条件或工程取舍。"
            "不要一次性倾倒一串问题；必要时帮助用户组织更好的表达。"
            + _NO_STATE_MUTATION
        ),
        activity_kinds=(ACTIVITY_INTERVIEW,),
        priority=10,
    ))
    registry.register(AgentSkill(
        key="practice-coach",
        title="实践辅导",
        description="围绕当前 Task 的交付物和验收标准推进实践任务。",
        instruction=(
            "先明确实践目标，再参考当前 Task 提供的 deliverable、acceptance criteria、"
            "expected artifact 拆解最小可交付步骤；了解当前阻塞并提出可执行的下一步。"
            "你没有 Sandbox；不得声称文件已创建、项目已运行或实验已完成。"
            + _NO_EXECUTION + _NO_STATE_MUTATION
        ),
        activity_kinds=(ACTIVITY_PRACTICE,),
        priority=10,
    ))
    return registry
