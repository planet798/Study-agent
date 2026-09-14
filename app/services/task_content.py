"""Planner 任务内容质量：生成“可直接执行”的学习任务描述（Phase 修复）。

背景：AI/规则生成的任务 description 原来往往只有标题或一句话，
无法直接指导学习。这里提供：
- build_topic_task_content(): 按主题名生成结构化、可执行的任务内容；
- has_actionable_content(): 判断一段 description 是否“足够可执行”。

格式（复用现有 task.description 字段，不新增 schema）：
  【学习目标】
  【具体学习事项】(3~5 条编号、可直接照做)
  【实践】(理论讲清核心机制 / 编码给最小实践)
  【完成标准】(几个可检查的完成标志)
  【客观验收】(如何验收)

原则：内容简洁、能直接开工；不是教材、不超过可读长度；
同一主题多次生成结果确定（同一输入 → 同一输出）。
"""

from __future__ import annotations

import re

__all__ = [
    "build_topic_task_content",
    "build_retention_task_content",
    "has_actionable_content",
]

# 可执行判断：至少包含“完成标准”且 ≥3 条编号/要点
_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+", re.MULTILINE)


def has_actionable_content(description: str | None) -> bool:
    """一段任务描述是否“可执行”（含完成标准 + 至少 3 条学习事项/要点）。"""
    d = (description or "").strip()
    if len(d) < 80:
        return False
    if "完成标准" not in d:
        return False
    items = _ITEM_RE.findall(d)
    return len(items) >= 3


# ---------------------------------------------------------------------------
# 主题→学习内容模板（关键词子串匹配；{T} 为主题名）
# ---------------------------------------------------------------------------

_GENERIC = {
    "goal": "围绕「{T}」建立可执行的学习闭环：先理解核心机制，再动手跑通最小示例。",
    "items": [
        "明确「{T}」要解决的问题、核心概念与输入输出",
        "梳理「{T}」的关键组件/字段/机制，画一张结构或流程草图",
        "用 Python/命令实现一个最小可运行示例并跑通",
        "把「{T}」与相邻概念对比，找出 2 个常见误区",
        "修改一个参数或组件，观察行为变化并记录",
    ],
    "practice": "写一个最小可运行示例（{T}），跑通后做一次小幅修改并复现差异。",
    "criteria": [
        "能画出/口述 {T} 的结构与流程",
        "能运行最小示例并复现关键行为",
        "能说出 2 个常见误区及其原因",
        "能独立做一次修改并解释影响",
    ],
    "verify": [
        "运行示例并把关键输出/结果贴出来",
        "回答 2~3 道关于 {T} 核心机制的客观验收题",
    ],
}

_MODULES = [
    (
        ("function calling", "tool calling", "function_call", "tool_call",
         "tools/schema"),
        {
            "goal": "理解并亲手跑通 Function Calling 的「请求→tool call→工具执行→tool result→最终回答」完整闭环。",
            "items": [
                "理解 Function Calling 的完整数据流：请求→tool call→工具执行→tool result→最终回答",
                "说清 tools / schema / arguments / tool result 各字段的作用与约束",
                "用 Python 实现一个最小 calculator tool（加减乘除、校验入参）",
                "实际调用一次模型，完成一次真实的 tool calling 闭环",
                "能解释 Function Calling 与“让模型直接执行代码”的关键区别（受控、可校验、安全）",
            ],
            "practice": (
                "写最小代码：定义 tools schema → 调用模型 → 解析 tool_calls → "
                "执行工具 → 把 tool result 回填 → 输出最终回答。"
            ),
            "criteria": [
                "能画出并口述完整数据流（请求→tool call→执行→result→回答）",
                "能运行最小 Demo，并看到模型确实调用了 tool",
                "能解释 tools/schema/arguments/tool result 各自作用",
                "能独立修改或新增一个 tool 并跑通",
            ],
            "verify": [
                "运行最小 Demo 复现一次真实的 tool call",
                "回答 2~3 道关于字段含义与数据流的客观验收题",
            ],
        },
    ),
    (
        ("react", "推理与行动", "reasoning and action", "推理行动"),
        {
            "goal": "理解并实现 ReAct 的「推理→行动→观察」循环及其终止条件。",
            "items": [
                "理解 ReAct 的 Thought→Action→Observation 循环，说明循环何时结束",
                "说清 ReAct 与纯 CoT（只推理不行动）的区别与适用场景",
                "实现一个最小 ReAct 循环：模型产出 thought/action → 执行 → 观察回填",
                "设计能收敛的停止条件（最大步数 / 出现答案判定）",
                "分析 ReAct 的失败模式：死循环、错误工具、误导观察",
            ],
            "practice": (
                "用 Python 写一个最小 ReAct loop（含 max_steps 与终止判断），"
                "跑通一个多步任务。"
            ),
            "criteria": [
                "能画出循环状态图并解释终止条件",
                "能运行最小 ReAct Demo 完成一个多步任务",
                "能解释 ReAct 与 CoT / Function Calling 的区别",
                "能修改停止条件或提示词并观察行为变化",
            ],
            "verify": [
                "运行多步 Demo 并展示 Thought/Action/Observation 记录",
                "回答循环终止与观察回填的客观验收题",
            ],
        },
    ),
    (
        ("planning", "规划", "任务规划", "计划"),
        {
            "goal": "理解并实现「先拆解计划 → 逐步执行 → 失败回退重规划」的任务规划。",
            "items": [
                "理解 Planning 与单步执行的区别：先产出步骤列表再执行",
                "说清拆解步骤的原则：可执行、有依赖、可回退",
                "用 Python 实现一个最小 planner：把目标拆成步骤并按序执行",
                "处理执行失败：重试 / 重规划 / 放弃 三种路径",
                "对比 Planning + ReAct 的适用场景与取舍",
            ],
            "practice": (
                "写一个最小任务规划 + 执行器（可含步骤依赖检测），"
                "演示一次“失败后重规划”。"
            ),
            "criteria": [
                "能说出拆解步骤的原则",
                "能运行最小 Planning Demo",
                "能解释失败后的重试/重规划/放弃策略",
                "能独立给一个新目标写一份 3~5 步执行计划",
            ],
            "verify": [
                "运行 Demo 展示 计划→执行→失败→重规划 的过程",
                "回答关于分解与回退策略的客观验收题",
            ],
        },
    ),
    (
        ("rag", "检索增强", "检索增强生成"),
        {
            "goal": "端到端搭一个最小 RAG：切块→索引→检索→拼装→生成。",
            "items": [
                "理解 RAG 流水线各环节：切块/索引/检索/提示拼装/生成",
                "说清“检索后再生成”与“直接生成”的区别及何时 RAG 必要",
                "用 Python 实现最小 RAG：文档切块 → embedding/keyword 检索 → top-k → 拼提示 → 模型回答",
                "改变 top-k 或块大小，观察回答质量变化",
                "指出 RAG 常见坑：分块边界、检索噪声、未引用来源",
            ],
            "practice": (
                "用自备小语料实现最小 RAG Demo，跑通“带上下文作答”并引用资料。"
            ),
            "criteria": [
                "能画出 RAG 数据流（检索→拼装→生成）",
                "能运行最小 RAG Demo 并引用资料作答",
                "能解释向量检索与关键词检索的区别",
                "能调一个参数（top-k / 块大小）并复现质量变化",
            ],
            "verify": [
                "用自备语料跑通检索作答并展示引用来源",
                "回答 RAG 各环节作用的客观验收题",
            ],
        },
    ),
    (
        ("embedding", "向量化", "语义向量"),
        {
            "goal": "理解并亲手计算「文本→向量→相似度」的最小闭环。",
            "items": [
                "理解 embedding 是什么：把离散文本映射为稠密向量",
                "说清相似度度量（余弦/内积）和一次调用返回的张量形状",
                "用 Python 对若干文本生成 embedding 并做相似度排序",
                "观察语义相近与无关文本的距离差异",
                "说明 embedding 为什么能支撑检索与推荐",
            ],
            "practice": (
                "写最小脚本：多文本 → embedding → 余弦相似度 → top-k 排序。"
            ),
            "criteria": [
                "能解释向量形状与维度含义",
                "能运行最小相似度 Demo 得到合理排序",
                "能解释余弦 vs 内积的选择",
                "能换一批语料复现排序",
            ],
            "verify": [
                "跑通相似度排序并贴出排序结果",
                "回答 embedding 原理的客观验收题",
            ],
        },
    ),
    (
        ("transformer", "attention", "attention/mha", "self-attention", "mha"),
        {
            "goal": "弄懂 attention 的数学并亲手实现一次前向，与库结果对照。",
            "items": [
                "理解 scaled dot-product attention：Q·Kᵀ/√d_k → softmax → V",
                "说清 Q/K/V、掩码，以及为什么除以 √d_k",
                "用 PyTorch/NumPy 实现最小 attention 前向并对比库输出",
                "实现 multi-head 并验证输出形状",
                "能解释多头并行捕捉不同子空间的动机",
            ],
            "practice": (
                "实现 attention 前向 + 与现有实现（库/layer）的数值对照。"
            ),
            "criteria": [
                "能口述/手推公式每一步",
                "能运行最小实现并与库结果一致",
                "能解释 Q/K/V 与掩码的作用",
                "能改 head 数 / 维度并复现行为",
            ],
            "verify": [
                "数值对照结果记录在案",
                "回答 Q/K/V 与缩放因子等客观验收题",
            ],
        },
    ),
    (
        ("pytorch", "tensor", "autograd", "张量", "自动求导", "dataset",
         "dataloader", "nn.module", "线性回归", "训练闭环", "梯度"),
        {
            "goal": "亲手跑通一个可训练的最小模型闭环（数据→模型→loss→反向→更新）。",
            "items": [
                "理解 Tensor 与 autograd：前向自动记录，loss.backward() 产生梯度",
                "说清 Dataset / DataLoader 的角色与 batch 概念",
                "用 nn.Module 搭一个最小模型，用优化器训练几个 epoch",
                "观察 loss 是否下降，并会排查“梯度不上”的常见原因",
                "记录并保存每轮 loss，说明收敛 / 不收敛",
            ],
            "practice": (
                "写最小训练闭环（如线性拟合），每个 epoch 打印 loss，"
                "保存一份 loss 记录。"
            ),
            "criteria": [
                "能解释 自动求导 → 参数更新 的完整流程",
                "能运行最小训练并看到 loss 下降",
                "能改学习率 / epoch 并观察变化",
                "说清 Dataset→DataLoader→模型→loss→backward→step 链路",
            ],
            "verify": [
                "跑通训练并贴出 loss 记录",
                "回答自动求导与训练流程的客观验收题",
            ],
        },
    ),
    (
        ("lora", "qlora", "sft", "微调", "peft", "指令微调"),
        {
            "goal": "理解参数高效微调（LoRA/SFT）的原理并跑一个最小微调。",
            "items": [
                "理解 SFT 目标与数据格式（instruction / input / output）",
                "说清 LoRA 为什么只训练低秩增量而冻结原权重",
                "用 PEFT 给一个模型加 LoRA 并微调几步",
                "对比微调前后在目标行为上的差异",
                "能解释 LoRA vs 全量微调在内存/效果上的取舍",
            ],
            "practice": (
                "用 PEFT 实现最小 LoRA 微调（小数据集 / 几 step），"
                "记录训练前后的一次生成对比。"
            ),
            "criteria": [
                "能解释 LoRA 的秩与增量更新的含义",
                "能运行最小 LoRA 微调",
                "能对同一输入对比微调前后的输出",
                "能说出 LoRA 冻结权重、只训增量的原因",
            ],
            "verify": [
                "跑通微调并给出前后对比输出",
                "回答 LoRA/SFT 的客观验收题",
            ],
        },
    ),
    (
        ("agent", "智能体"),
        {
            "goal": "实现最小 Agent：循环调用工具直到完成任务。",
            "items": [
                "理解 Agent 循环：模型→选定工具→执行→观察→再次决策",
                "说清工具描述如何被模型用来「选择工具」",
                "实现 2 个工具 + 一个最小 Agent 循环",
                "处理工具调用错误与多步依赖",
                "讨论 Agent 护栏：终止条件 / 工具白名单 / 成本",
            ],
            "practice": (
                "实现最小 Agent（如 calc/search 风格两个工具），"
                "完成一个需要多步调用才能解的任务。"
            ),
            "criteria": [
                "能画出 Agent 循环图",
                "能运行最小 Agent Demo 解决一个多步任务",
                "能解释模型如何根据工具描述选工具",
                "能说出 2 个护栏手段",
            ],
            "verify": [
                "运行多步 Agent Demo 并展示工具调用序列",
                "回答 Agent 循环与工具的客观验收题",
            ],
        },
    ),
]


def build_topic_task_content(name: str, description: str = "") -> str:
    """按主题名生成结构化、可执行的任务内容；同一输入输出确定。"""
    T = (name or "").strip() or (description or "").strip() or "该主题"
    low = T.lower()
    info = None
    for kws, tpl in _MODULES:
        if any(k in low for k in kws):
            info = tpl
            break
    src = info or _GENERIC

    def _fmt(s: str) -> str:
        return s.replace("{T}", T)

    lines = ["【学习目标】", _fmt(src["goal"]), "", "【具体学习事项】"]
    for i, item in enumerate(src["items"], 1):
        lines.append(f"{i}. {_fmt(item)}")
    lines += ["", "【实践】", _fmt(src["practice"]), ""]
    lines.append("【完成标准】")
    lines += [f"- {_fmt(c)}" for c in src["criteria"]]
    lines += ["", "【客观验收】"]
    lines += [f"- {_fmt(v)}" for v in src["verify"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 每日巩固（Daily Retention）复习内容
# ---------------------------------------------------------------------------

def build_retention_task_content(
    name: str, description: str = "", weak_points: list[str] | None = None
) -> str:
    """每日巩固的轻量复习内容；同一输入输出确定（不调用 AI）。

    结构：【复习目标】【快速回忆】【最小实践】【完成标准】。
    复用主题名 / 描述 + 既有主题模板；约 10~20 分钟可完成，不是一整套新课。
    """
    T = (name or "").strip() or (description or "").strip() or "该知识点"
    low = T.lower()
    info = None
    for kws, tpl in _MODULES:
        if any(k in low for k in kws):
            info = tpl
            break
    if info:
        practice = info["practice"].replace("{T}", T)
        hints = [i.replace("{T}", T) for i in info["items"][:3]]
    else:
        practice = (
            f"写一个 10 行以内的最小示例或推导，重现「{T}」的关键行为，"
            "并说明预期结果。"
        )
        hints = []

    lines = [
        "【复习目标】",
        f"用 10~20 分钟快速巩固「{T}」的短期记忆。本次只是每日巩固，"
        "不代表已掌握，也不产生验收结论。",
        "",
        "【快速回忆】",
        f"- 先不看笔记，口述/写出「{T}」的核心概念与整体流程。",
        "- 再回答以下问题：",
        f"  1. 「{T}」要解决的核心问题是什么？",
        f"  2. 「{T}」的关键步骤/机制有哪些？",
        "  3. 哪一处最容易记错，或容易与相邻概念混淆？",
    ]
    if hints:
        lines.append("- 对照检查（回忆不出的重点看）：")
        lines += [f"  · {h}" for h in hints]
    if weak_points:
        lines.append(
            "- 上次验收的薄弱点："
            + "；".join(str(w) for w in weak_points[:3])
        )
    lines += [
        "",
        "【最小实践】",
        practice,
        "",
        "【完成标准】",
        "- 能不看笔记独立解释核心机制",
        "- 能完成上面的最小实践并说明结果",
        "- 能指出一个易错点",
    ]
    return "\n".join(lines)
