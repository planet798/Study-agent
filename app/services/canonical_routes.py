"""Canonical 六技术路线定义（Phase 1）。

这是 R1–R6 的唯一 canonical 定义来源（不依赖 display name 做系统身份）：

    求职准备 (JOB_PREP, group)
    ├─ R1_LLM_FUNDAMENTALS          R1 LLM Fundamentals
    ├─ R2_LLM_POST_TRAINING         R2 LLM Post-Training
    ├─ R3_LLM_INFRA                 R3 LLM Infra
    ├─ R4_AI_AGENT                  R4 AI Agent
    ├─ R5_RECOMMENDATION_SEARCH     R5 Recommendation & Search
    └─ R6_CS_FUNDAMENTALS           R6 CS Fundamentals

旧路线 `搜广推 + LLM` → route_key=LEGACY_SEARCH_LLM，archive 保留。

本模块只提供**数据定义**（route / phase / topic / skill 映射 / 迁移分类），
不含 SQL 与副作用；seed / migration 逻辑在
:mod:`app.services.canonical_route_service` 与
:mod:`app.services.route_migration_service`。
"""

from __future__ import annotations

from ..database.learning_route_repository import (
    ROUTE_KEY_JOB_PREP,
    ROUTE_KEY_LEGACY_SEARCH_LLM,
)

# ============================================================
# Route keys
# ============================================================

ROUTE_KEY_R1 = "R1_LLM_FUNDAMENTALS"
ROUTE_KEY_R2 = "R2_LLM_POST_TRAINING"
ROUTE_KEY_R3 = "R3_LLM_INFRA"
ROUTE_KEY_R4 = "R4_AI_AGENT"
ROUTE_KEY_R5 = "R5_RECOMMENDATION_SEARCH"
ROUTE_KEY_R6 = "R6_CS_FUNDAMENTALS"

CANONICAL_LEARNING_KEYS: tuple[str, ...] = (
    ROUTE_KEY_R1, ROUTE_KEY_R2, ROUTE_KEY_R3,
    ROUTE_KEY_R4, ROUTE_KEY_R5, ROUTE_KEY_R6,
)

ALL_CANONICAL_KEYS: tuple[str, ...] = (ROUTE_KEY_JOB_PREP,) + CANONICAL_LEARNING_KEYS

# Plan 的宽日期范围：canonical 路线按 phase.order_index 顺序推进，
# 不依赖绝对日期窗口（避免“阶段间隙/日期未到”导致无法生成任务）。
PLAN_START_DATE = "2026-01-01"
PLAN_END_DATE = "2099-12-31"

# ============================================================
# canonical routes
# ============================================================

# key -> {name, priority, goal, description}
CANONICAL_ROUTES: dict[str, dict] = {
    ROUTE_KEY_JOB_PREP: {
        "name": "求职准备",
        "priority": 3,
        "goal": "",
        "description": "正式技术学习体系的顶层分组。",
    },
    ROUTE_KEY_R1: {
        "name": "R1 LLM Fundamentals",
        "priority": 5,
        "goal": "理解 Transformer / Attention / Embedding 等 LLM 核心机制，能读源码、加载并推理主流模型。",
        "description": "LLM 基础机制：Transformer、Attention/MHA/GQA、Embedding、RoPE、FFN、KV Cache、Hugging Face、主流模型架构与推理。",
    },
    ROUTE_KEY_R2: {
        "name": "R2 LLM Post-Training",
        "priority": 5,
        "goal": "掌握 SFT / LoRA / RLHF / DPO / GRPO 等后训练与对齐方法，能构造数据、训练并评估。",
        "description": "LLM 后训练：SFT、Full Fine-Tuning、LoRA/QLoRA、RLHF、DPO、PPO、GRPO、蒸馏、数据构造、训练与评估。",
    },
    ROUTE_KEY_R3: {
        "name": "R3 LLM Infra",
        "priority": 3,
        "goal": "掌握 vLLM / 分布式训练 / 量化 / 部署，能优化显存、吞吐与延迟。",
        "description": "LLM 工程与基础设施：vLLM、PagedAttention、Continuous Batching、量化、混合精度、ZeRO、分布式训练、推理优化、部署。",
    },
    ROUTE_KEY_R4: {
        "name": "R4 AI Agent",
        "priority": 5,
        "goal": "能构建可评估的 Agent / RAG 系统，掌握工具调用、规划、记忆、检索与多 Agent 框架。",
        "description": "AI Agent：Function/Tool Calling、ReAct、Planning、Memory、RAG、Retrieval/Reranking、LangChain/LangGraph、Multi-Agent、Agent Harness。",
    },
    ROUTE_KEY_R5: {
        "name": "R5 Recommendation & Search",
        "priority": 4,
        "goal": "掌握推荐/搜索系统链路：数据特征、召回、排序、重排、评估，以及 LLM 与推荐/搜索的结合。",
        "description": "推荐与搜索：数据挖掘、LR/GBDT/XGBoost/DNN、DeepFM、DIN/DIEN、召回/排序/重排、BM25、Embedding Retrieval、LLM+Rec/Search。",
    },
    ROUTE_KEY_R6: {
        "name": "R6 CS Fundamentals",
        "priority": 3,
        "goal": "夯实计算机基础：数据结构与算法、C++、操作系统、计算机网络、数据库、Linux、Git、软件工程。",
        "description": "计算机基础：DSA、C++、OS、网络、数据库、Linux、Git、软件工程基础。",
    },
}


def _t(name: str, minutes: int = 45, priority: int = 2):
    return {"name": name, "minutes": minutes, "priority": priority}


# ============================================================
# canonical phases / topics
# ============================================================
#
# 顺序即学习顺序（order_index）。MOVE 类 topic 的名称与旧 _DEFAULT_PHASES
# **完全一致**，以便 migration 直接把旧 topic reparent 到对应 phase。
# 新建 topic（含 SPLIT_NEW 的路线专用 Topic）也在这里定义；seed 按名称幂等创建。

CANONICAL_PHASES: dict[str, list[dict]] = {
    ROUTE_KEY_R1: [
        {
            "name": "深度学习底座",
            "goal": "能独立搭建并训练最小模型，理解张量/自动求导/数据加载。",
            "topics": [
                _t("PyTorch 张量与自动求导（Tensor / autograd）", 60, 3),
                _t("最小线性回归训练闭环（y=2x+1）", 60, 3),
                _t("Dataset 与 DataLoader", 60, 2),
                _t("nn.Module 与模型搭建", 60, 2),
            ],
        },
        {
            "name": "Transformer 核心机制",
            "goal": "理解 Attention / 位置编码 / 归一化 / 分词 / KV Cache 的机制与实现。",
            "topics": [
                _t("Transformer：Attention / MHA / FFN", 60, 3),
                _t("LayerNorm / RMSNorm", 45, 2),
                _t("Tokenizer 与分词", 45, 2),
                _t("RoPE 位置编码", 45, 2),
                _t("KV Cache 原理", 45, 2),
            ],
        },
        {
            "name": "模型加载与推理",
            "goal": "能用 Hugging Face 加载主流模型并完成推理与采样。",
            "topics": [
                _t("Hugging Face Transformers", 60, 2),
                _t("generate / sampling 解码策略", 45, 2),
                _t("Qwen / LLaMA 架构：GQA / SwiGLU", 60, 1),
            ],
        },
        {
            "name": "表示学习与检索基础",
            "goal": "理解 Embedding 的语义与相似度计算。",
            "topics": [
                _t("Embedding Fundamentals", 45, 2),
            ],
        },
        {
            "name": "评估基础",
            "goal": "建立客观评估与 badcase 分析意识。",
            "topics": [
                _t("模型评估基础", 45, 2),
            ],
        },
        {
            "name": "扩展（可选）",
            "goal": "按方向了解多模态等扩展内容，不作为核心前置。",
            "topics": [
                _t("VLM / 多模态基础（扩展）", 60, 1),
            ],
        },
    ],
    ROUTE_KEY_R2: [
        {
            "name": "监督微调",
            "goal": "能构造数据并完成 SFT 指令微调。",
            "topics": [
                _t("SFT 指令微调", 60, 3),
                _t("LLaMA-Factory", 45, 2),
                _t("微调数据构造", 60, 2),
            ],
        },
        {
            "name": "参数高效微调",
            "goal": "掌握 LoRA/QLoRA/PEFT 等参数高效微调方法。",
            "topics": [
                _t("LoRA / QLoRA", 60, 3),
                _t("PEFT", 45, 2),
            ],
        },
        {
            "name": "对齐与偏好优化",
            "goal": "理解 RLHF / DPO / PPO / GRPO 与模型蒸馏。",
            "topics": [
                _t("RLHF", 60, 2),
                _t("DPO", 60, 2),
                _t("PPO", 60, 2),
                _t("GRPO", 60, 2),
                _t("模型蒸馏", 45, 1),
            ],
        },
        {
            "name": "训练与评估",
            "goal": "完成后训练训练闭环并做客观评估。",
            "topics": [
                _t("后训练训练与评估", 60, 2),
                _t("后训练评估与 Badcase", 45, 2),
            ],
        },
    ],
    ROUTE_KEY_R3: [
        {
            "name": "推理服务",
            "goal": "能用 vLLM 部署模型并理解 Continuous Batching。",
            "topics": [
                _t("vLLM 与 PagedAttention", 60, 2),
                _t("Continuous Batching", 45, 2),
                _t("Serving / Deployment", 60, 1),
            ],
        },
        {
            "name": "推理优化",
            "goal": "掌握量化 / 混合精度 / 显存与吞吐延迟优化、KV Cache 管理。",
            "topics": [
                _t("推理优化基础", 45, 2),
                _t("KV Cache 管理与推理优化", 45, 2),
                _t("Quantization", 45, 2),
                _t("Mixed Precision", 45, 2),
                _t("GPU 显存 / 吞吐 / 延迟", 45, 2),
            ],
        },
        {
            "name": "分布式训练",
            "goal": "理解 DDP / ZeRO / DeepSpeed 分布式训练。",
            "topics": [
                _t("DDP / ZeRO / DeepSpeed（先理解）", 60, 1),
                _t("ZeRO", 60, 1),
                _t("分布式训练", 60, 1),
            ],
        },
        {
            "name": "部署工程",
            "goal": "掌握容器化部署基础。",
            "topics": [
                _t("Docker", 60, 1),
            ],
        },
        {
            "name": "GPU 计算",
            "goal": "了解 GPU 计算与 CUDA 基础。",
            "topics": [
                _t("CUDA / GPU Computing 基础", 60, 1),
            ],
        },
    ],
    ROUTE_KEY_R4: [
        {
            "name": "Agent 基础协议",
            "goal": "掌握工具调用、ReAct、规划与记忆。",
            "topics": [
                _t("Function Calling / Tool Calling", 60, 3),
                _t("ReAct 推理与行动", 45, 3),
                _t("Planning 任务规划", 45, 2),
                _t("Memory / State 状态管理", 45, 2),
            ],
        },
        {
            "name": "RAG 系统",
            "goal": "能搭建 RAG 检索链路并做融合与重排。",
            "topics": [
                _t("RAG 全流程搭建", 60, 3),
                _t("Chunking 分块策略", 45, 2),
                _t("Embedding Retrieval for RAG", 45, 2),
                _t("Hybrid Retrieval for RAG", 45, 2),
                _t("RAG Retrieval Fusion / RRF", 30, 1),
                _t("RAG Reranker", 45, 2),
            ],
        },
        {
            "name": "Agent 工程与框架",
            "goal": "能用主流框架实现多步 / 多 Agent 与深度研究型 Agent。",
            "topics": [
                _t("Agent 实现与多步编排", 60, 3),
                _t("LangChain", 45, 2),
                _t("LangGraph", 45, 2),
                _t("Multi-Agent", 45, 2),
                _t("Deep Research", 45, 1),
                _t("Agent Harness", 45, 1),
                _t("Pi / Codex CLI / OpenClaw / Hermes", 30, 1),
            ],
        },
        {
            "name": "Agent 评估",
            "goal": "能对 Agent / RAG 做客观评估与 badcase 分析。",
            "topics": [
                _t("Agent Evaluation / LLM-as-Judge", 45, 2),
            ],
        },
    ],
    ROUTE_KEY_R5: [
        {
            "name": "数据与特征",
            "goal": "掌握推荐/搜索数据分析与特征工程、用户画像。",
            "topics": [
                _t("推荐 / 搜索数据分析 SQL", 45, 2),
                _t("数据挖掘", 60, 2),
                _t("用户画像与特征工程", 45, 2),
            ],
        },
        {
            "name": "推荐系统基础",
            "goal": "理解推荐系统整体架构与协同过滤。",
            "topics": [
                _t("推荐系统整体架构", 60, 3),
                _t("协同过滤基础", 60, 2),
            ],
        },
        {
            "name": "召回",
            "goal": "掌握向量召回、双塔与多路召回。",
            "topics": [
                _t("Embedding Recall / 向量召回", 60, 2),
                _t("双塔召回 Two-Tower", 60, 2),
                _t("多路召回与 Candidate Generation", 60, 2),
                _t("Embedding Recall / Search Retrieval", 45, 2),
            ],
        },
        {
            "name": "排序",
            "goal": "掌握从 LR/GBDT 到 DNN/DeepFM/DIN 的排序模型。",
            "topics": [
                _t("Ranking 基础", 60, 3),
                _t("CTR 预估基础", 60, 2),
                _t("Wide & Deep / DeepFM 基础", 60, 2),
                _t("LR", 45, 2),
                _t("GBDT / XGBoost", 60, 2),
                _t("DNN", 45, 2),
                _t("DIN / DIEN", 60, 1),
            ],
        },
        {
            "name": "搜索系统",
            "goal": "理解搜索系统、BM25 与结果融合。",
            "topics": [
                _t("搜索系统", 60, 2),
                _t("BM25 与搜索召回", 45, 2),
                _t("搜索结果融合 / RRF", 30, 1),
            ],
        },
        {
            "name": "重排与评估",
            "goal": "掌握重排与推荐/搜索评估、badcase 分析。",
            "topics": [
                _t("Rerank / 重排基础", 45, 2),
                _t("搜索 / 推荐 Reranking", 45, 2),
                _t("推荐系统评估指标", 45, 2),
                _t("推荐系统 Badcase 分析", 45, 2),
                _t("推荐 / 搜索评估与 Badcase", 45, 1),
            ],
        },
        {
            "name": "LLM × 推荐",
            "goal": "理解 LLM 与推荐/搜索的结合。",
            "topics": [
                _t("LLM + Recommendation 基础", 60, 2),
            ],
        },
    ],
    ROUTE_KEY_R6: [
        {
            "name": "编程与工程基础",
            "goal": "熟练 Python 与软件工程基础。",
            "topics": [
                _t("Python 语法与基础练习", 45, 3),
                _t("Python 面向对象与常用库", 45, 2),
                _t("软件工程基础", 45, 1),
            ],
        },
        {
            "name": "数据结构与算法",
            "goal": "掌握常见数据结构与算法，支撑面试与工程。",
            "topics": [
                _t("数据结构与算法", 60, 2),
            ],
        },
        {
            "name": "系统基础",
            "goal": "掌握操作系统、计算机网络与数据库。",
            "topics": [
                _t("操作系统", 60, 1),
                _t("计算机网络", 60, 1),
                _t("数据库 / SQL 基础", 45, 1),
            ],
        },
        {
            "name": "工具链",
            "goal": "熟练 Linux 与 Git。",
            "topics": [
                _t("Linux 常用命令与工具链", 30, 2),
                _t("Git 版本控制与协作流程", 30, 1),
            ],
        },
        {
            "name": "C++",
            "goal": "掌握 C++ 基础。",
            "topics": [
                _t("C++ 基础", 60, 1),
            ],
        },
    ],
}


# ============================================================
# 旧 Topic 迁移分类
# ============================================================
#
# MOVE：保留旧 topic id，reparent 到 (route_key, phase_name)。
# SPLIT_NEW：旧 Topic 保持 LEGACY；新 route-specific Topic 由 CANONICAL_PHASES 创建。
# MANUAL_REVIEW / KEEP_LEGACY：保留在旧路线，不迁移。

LEGACY_MOVE_MAP: dict[str, tuple[str, str]] = {
    # R6
    "Python 语法与基础练习": (ROUTE_KEY_R6, "编程与工程基础"),
    "Python 面向对象与常用库": (ROUTE_KEY_R6, "编程与工程基础"),
    "Linux 常用命令与工具链": (ROUTE_KEY_R6, "工具链"),
    "Git 版本控制与协作流程": (ROUTE_KEY_R6, "工具链"),
    # R1
    "PyTorch 张量与自动求导（Tensor / autograd）": (ROUTE_KEY_R1, "深度学习底座"),
    "最小线性回归训练闭环（y=2x+1）": (ROUTE_KEY_R1, "深度学习底座"),
    "Dataset 与 DataLoader": (ROUTE_KEY_R1, "深度学习底座"),
    "nn.Module 与模型搭建": (ROUTE_KEY_R1, "深度学习底座"),
    "Transformer：Attention / MHA / FFN": (ROUTE_KEY_R1, "Transformer 核心机制"),
    "LayerNorm / RMSNorm": (ROUTE_KEY_R1, "Transformer 核心机制"),
    "Tokenizer 与分词": (ROUTE_KEY_R1, "Transformer 核心机制"),
    "RoPE 位置编码": (ROUTE_KEY_R1, "Transformer 核心机制"),
    "Hugging Face Transformers": (ROUTE_KEY_R1, "模型加载与推理"),
    "generate / sampling 解码策略": (ROUTE_KEY_R1, "模型加载与推理"),
    "Qwen / LLaMA 架构：GQA / SwiGLU": (ROUTE_KEY_R1, "模型加载与推理"),
    # R4
    "Function Calling / Tool Calling": (ROUTE_KEY_R4, "Agent 基础协议"),
    "ReAct 推理与行动": (ROUTE_KEY_R4, "Agent 基础协议"),
    "Planning 任务规划": (ROUTE_KEY_R4, "Agent 基础协议"),
    "Memory / State 状态管理": (ROUTE_KEY_R4, "Agent 基础协议"),
    "RAG 全流程搭建": (ROUTE_KEY_R4, "RAG 系统"),
    "Chunking 分块策略": (ROUTE_KEY_R4, "RAG 系统"),
    "Agent 实现与多步编排": (ROUTE_KEY_R4, "Agent 工程与框架"),
    # R5
    "推荐系统整体架构": (ROUTE_KEY_R5, "推荐系统基础"),
    "协同过滤基础": (ROUTE_KEY_R5, "推荐系统基础"),
    "Embedding Recall / 向量召回": (ROUTE_KEY_R5, "召回"),
    "双塔召回 Two-Tower": (ROUTE_KEY_R5, "召回"),
    "多路召回与 Candidate Generation": (ROUTE_KEY_R5, "召回"),
    "Ranking 基础": (ROUTE_KEY_R5, "排序"),
    "CTR 预估基础": (ROUTE_KEY_R5, "排序"),
    "Wide & Deep / DeepFM 基础": (ROUTE_KEY_R5, "排序"),
    "推荐系统评估指标": (ROUTE_KEY_R5, "重排与评估"),
    "Rerank / 重排基础": (ROUTE_KEY_R5, "重排与评估"),
    "用户画像与特征工程": (ROUTE_KEY_R5, "数据与特征"),
    "推荐系统 Badcase 分析": (ROUTE_KEY_R5, "重排与评估"),
    "LLM + Recommendation 基础": (ROUTE_KEY_R5, "LLM × 推荐"),
    # R2
    "LoRA / QLoRA": (ROUTE_KEY_R2, "参数高效微调"),
    "PEFT": (ROUTE_KEY_R2, "参数高效微调"),
    "SFT 指令微调": (ROUTE_KEY_R2, "监督微调"),
    "LLaMA-Factory": (ROUTE_KEY_R2, "监督微调"),
    # R3
    "vLLM 与 PagedAttention": (ROUTE_KEY_R3, "推理服务"),
    "Continuous Batching": (ROUTE_KEY_R3, "推理服务"),
    "Docker": (ROUTE_KEY_R3, "部署工程"),
    "DDP / ZeRO / DeepSpeed（先理解）": (ROUTE_KEY_R3, "分布式训练"),
    "推理优化基础": (ROUTE_KEY_R3, "推理优化"),
}

# 旧 Topic -> 它被拆分去的 route（仅用于 preview 分类/报告；不迁移旧 Topic）
LEGACY_SPLIT_MAP: dict[str, tuple[str, ...]] = {
    "KV Cache": (ROUTE_KEY_R1, ROUTE_KEY_R3),
    "Embedding 与向量检索": (ROUTE_KEY_R1, ROUTE_KEY_R4, ROUTE_KEY_R5),
    "BM25 与混合检索": (ROUTE_KEY_R4, ROUTE_KEY_R5),
    "RRF 排序融合": (ROUTE_KEY_R4, ROUTE_KEY_R5),
    "Reranker 重排序": (ROUTE_KEY_R4, ROUTE_KEY_R5),
    "Evaluation / Badcase / LLM-as-Judge": (
        ROUTE_KEY_R1, ROUTE_KEY_R2, ROUTE_KEY_R4, ROUTE_KEY_R5
    ),
    "SQL 数据分析基础": (ROUTE_KEY_R5, ROUTE_KEY_R6),
    "C/C++ / CUDA（方向确定后深入）": (ROUTE_KEY_R3, ROUTE_KEY_R6),
    "VLM / 多模态基础": (ROUTE_KEY_R1,),
}

# 明确保留在 LEGACY、不迁移的旧 Topic
LEGACY_KEEP_TOPICS: tuple[str, ...] = (
    "VLA / World Model（了解）",
)

# 需要人工确认的旧 Topic（默认 KEEP_LEGACY，除非在 MOVE/SPLIT 中）
LEGACY_MANUAL_REVIEW_TOPICS: tuple[str, ...] = (
    "VLA / World Model（了解）",
)


# ============================================================
# Skill → canonical Route 确定性映射（允许 N:N）
# ============================================================

CANONICAL_SKILL_ROUTE_MAP: dict[str, tuple[str, ...]] = {
    # R6
    "Python": (ROUTE_KEY_R6,),
    "Linux": (ROUTE_KEY_R6,),
    "Git": (ROUTE_KEY_R6,),
    "C++": (ROUTE_KEY_R6,),
    "SQL": (ROUTE_KEY_R5, ROUTE_KEY_R6),
    # 基础 / 共享
    "PyTorch": (ROUTE_KEY_R1, ROUTE_KEY_R2, ROUTE_KEY_R5),
    "Transformer": (ROUTE_KEY_R1, ROUTE_KEY_R2),
    "LLM 基础": (ROUTE_KEY_R1, ROUTE_KEY_R2, ROUTE_KEY_R4),
    "Hugging Face": (ROUTE_KEY_R1, ROUTE_KEY_R2),
    # R2
    "SFT": (ROUTE_KEY_R2,),
    "LoRA / QLoRA": (ROUTE_KEY_R2, ROUTE_KEY_R3),
    "PEFT": (ROUTE_KEY_R2,),
    "后训练 / 对齐": (ROUTE_KEY_R2,),
    "模型蒸馏": (ROUTE_KEY_R2,),
    # R1 / R4 / R5 共享
    "Embedding": (ROUTE_KEY_R1, ROUTE_KEY_R4, ROUTE_KEY_R5),
    "RAG": (ROUTE_KEY_R4,),
    "Agent": (ROUTE_KEY_R4,),
    "模型评估": (ROUTE_KEY_R1, ROUTE_KEY_R2, ROUTE_KEY_R4, ROUTE_KEY_R5),
    # R5
    "推荐系统基础": (ROUTE_KEY_R5,),
    "Recall": (ROUTE_KEY_R5,),
    "Ranking": (ROUTE_KEY_R5,),
    "CTR": (ROUTE_KEY_R5,),
    "Rerank": (ROUTE_KEY_R4, ROUTE_KEY_R5),
    "用户画像": (ROUTE_KEY_R5,),
    "LLM + Recommendation": (ROUTE_KEY_R4, ROUTE_KEY_R5),
    # R3
    "vLLM": (ROUTE_KEY_R3,),
    "分布式训练底层": (ROUTE_KEY_R3,),
    "复杂推理优化": (ROUTE_KEY_R3,),
    "Docker": (ROUTE_KEY_R3, ROUTE_KEY_R6),
    "CUDA": (ROUTE_KEY_R3, ROUTE_KEY_R6),
    "MoE": (ROUTE_KEY_R1, ROUTE_KEY_R3),
}

# 需要人工确认的 skill（不自动绑定；Phase 1 不改）
SKILL_ROUTE_NEEDS_REVIEW: dict[str, str] = {
    "VLM": "R1–R6 无明确归属；VLM Topic 目前为 R1 optional 扩展。",
    "LangChain / LangGraph": "Phase 1 统一映射到 Agent 技能，不单独建 skill。",
}


# ============================================================
# Canonical 额外 Skill（修复 alias 指向不存在 skill 的问题）
# ============================================================
#
# JD alias 词典里已有 "moe" / "peft" / post-training 词汇，
# 但 skill_pool 不一定有目标 skill。这里作为正式产品定义补齐。

CANONICAL_EXTRA_SKILLS: dict[str, dict] = {
    "PEFT": {
        "tier": "A",
        "category": "core",
        "prerequisites": ["SFT", "LoRA / QLoRA"],
    },
    "MoE": {
        "tier": "B",
        "category": "core",
        "prerequisites": ["Transformer", "LLM 基础"],
    },
    "后训练 / 对齐": {
        "tier": "A",
        "category": "core",
        "prerequisites": ["SFT", "LLM 基础"],
    },
    "模型蒸馏": {
        "tier": "B",
        "category": "core",
        "prerequisites": ["SFT", "LLM 基础"],
    },
}


def phase_names(route_key: str) -> tuple[str, ...]:
    return tuple(p["name"] for p in CANONICAL_PHASES.get(route_key, []))


def topic_names(route_key: str) -> tuple[str, ...]:
    out: list[str] = []
    for phase in CANONICAL_PHASES.get(route_key, []):
        for topic in phase["topics"]:
            out.append(topic["name"])
    return tuple(out)


# ============================================================
# SPLIT_NEW / 新增 Topic → Skill 的显式链接
# ============================================================
#
# 仅供 canonical seed 幂等补充 skills.linked_topics（缺失才加，不删除）。
# MOVE 的旧 topic_id 不变，历史关系自然保留，无需在此声明。
# 旧「Embedding 与向量检索」的 topic_id **不会**被复用来代表 R1/R4/R5。

CANONICAL_TOPIC_SKILL_LINKS: dict[str, tuple[str, ...]] = {
    # ---- R1 ----
    "KV Cache 原理": ("LLM 基础",),
    "Embedding Fundamentals": ("Embedding",),
    "模型评估基础": ("模型评估",),
    "VLM / 多模态基础（扩展）": ("VLM",),
    # ---- R2 ----
    "微调数据构造": ("SFT",),
    "RLHF": ("后训练 / 对齐",),
    "DPO": ("后训练 / 对齐",),
    "PPO": ("后训练 / 对齐",),
    "GRPO": ("后训练 / 对齐",),
    "模型蒸馏": ("模型蒸馏",),
    "后训练训练与评估": ("后训练 / 对齐",),
    "后训练评估与 Badcase": ("模型评估",),
    # ---- R3 ----
    "KV Cache 管理与推理优化": ("vLLM",),
    "Serving / Deployment": ("vLLM",),
    "Quantization": ("复杂推理优化",),
    "Mixed Precision": ("复杂推理优化",),
    "GPU 显存 / 吞吐 / 延迟": ("复杂推理优化",),
    "ZeRO": ("分布式训练底层",),
    "分布式训练": ("分布式训练底层",),
    "CUDA / GPU Computing 基础": ("CUDA",),
    # ---- R4 ----
    "Embedding Retrieval for RAG": ("RAG", "Embedding"),
    "Hybrid Retrieval for RAG": ("RAG",),
    "RAG Retrieval Fusion / RRF": ("RAG",),
    "RAG Reranker": ("Rerank", "RAG"),
    "LangChain": ("Agent",),
    "LangGraph": ("Agent",),
    "Multi-Agent": ("Agent",),
    "Deep Research": ("Agent",),
    "Agent Harness": ("Agent",),
    "Pi / Codex CLI / OpenClaw / Hermes": ("Agent",),
    "Agent Evaluation / LLM-as-Judge": ("Agent", "模型评估"),
    # ---- R5 ----
    "推荐 / 搜索数据分析 SQL": ("SQL",),
    "数据挖掘": ("推荐系统基础",),
    "Embedding Recall / Search Retrieval": ("Recall", "Embedding"),
    "LR": ("CTR",),
    "GBDT / XGBoost": ("CTR",),
    "DNN": ("CTR",),
    "DIN / DIEN": ("CTR",),
    "搜索系统": ("Ranking",),
    "BM25 与搜索召回": ("Recall",),
    "搜索结果融合 / RRF": ("Ranking",),
    "搜索 / 推荐 Reranking": ("Rerank",),
    "推荐 / 搜索评估与 Badcase": ("模型评估",),
    # ---- R6 ----
    "数据库 / SQL 基础": ("SQL",),
    "C++ 基础": ("C++",),
}
