"""Canonical Topic Learning Component 配置（Phase 2）。

为 R1–R6 的 canonical Topic 定义“学习方式 profile”：

    theory / code_reading / experiment / interview / practice

原则：
- 不是所有 Topic 都要 5 种；按技术性质配置（见 Phase 2 需求 §15/§16/§50）；
- practice 主要是 optional（真正的 PracticeProject 在 Phase 4）；
- 这里只是**首次 seed 的初始值**：用户之后修改由 prompt_override 式的
  “不覆盖” 规则保护（见 TopicLearningProfileService.ensure_profile_from_spec）。

本模块只提供数据；seed 逻辑在 CanonicalRouteService / TopicLearningProfileService。
"""

from __future__ import annotations

from .learning_activity import (
    ACTIVITY_CODE_READING,
    ACTIVITY_EXPERIMENT,
    ACTIVITY_INTERVIEW,
    ACTIVITY_PRACTICE,
    ACTIVITY_THEORY,
)

R = True    # required
O = False   # optional

# 所有 Topic 的最小安全默认（没有显式 profile 时）
DEFAULT_PROFILE: list[tuple[str, bool]] = [(ACTIVITY_THEORY, R)]

# 每条 route 的默认风格
ROUTE_DEFAULT_PROFILES: dict[str, list[tuple[str, bool]]] = {
    "R1_LLM_FUNDAMENTALS": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "R2_LLM_POST_TRAINING": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "R3_LLM_INFRA": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "R4_AI_AGENT": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "R5_RECOMMENDATION_SEARCH": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "R6_CS_FUNDAMENTALS": [
        (ACTIVITY_THEORY, R), (ACTIVITY_INTERVIEW, R),
    ],
}

# 逐 Topic 覆盖（按名称精确匹配，不做 fuzzy）
CANONICAL_TOPIC_PROFILES: dict[str, list[tuple[str, bool]]] = {
    # ---------- R1 ----------
    "Transformer：Attention / MHA / FFN": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_INTERVIEW, O),
    ],
    "LayerNorm / RMSNorm": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "Tokenizer 与分词": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "RoPE 位置编码": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "KV Cache 原理": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "Qwen / LLaMA 架构：GQA / SwiGLU": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "Embedding Fundamentals": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "模型评估基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "VLM / 多模态基础（扩展）": [
        (ACTIVITY_THEORY, O),
    ],
    # ---------- R2 ----------
    "LoRA / QLoRA": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_INTERVIEW, O),
    ],
    "SFT 指令微调": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "微调数据构造": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "RLHF": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_INTERVIEW, O),
    ],
    "DPO": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "PPO": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_INTERVIEW, O),
    ],
    "GRPO": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "模型蒸馏": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "后训练训练与评估": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "后训练评估与 Badcase": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    # ---------- R3 ----------
    "vLLM 与 PagedAttention": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "Continuous Batching": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "KV Cache 管理与推理优化": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "Quantization": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "Mixed Precision": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "GPU 显存 / 吞吐 / 延迟": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Serving / Deployment": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Docker": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "CUDA / GPU Computing 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "ZeRO": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "分布式训练": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    # ---------- R4 ----------
    "Function Calling / Tool Calling": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_INTERVIEW, O),
    ],
    "ReAct 推理与行动": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_INTERVIEW, O),
    ],
    "Planning 任务规划": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Memory / State 状态管理": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "RAG 全流程搭建": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_PRACTICE, O),
    ],
    "Chunking 分块策略": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Embedding Retrieval for RAG": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Hybrid Retrieval for RAG": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "RAG Retrieval Fusion / RRF": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "RAG Reranker": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Agent 实现与多步编排": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_PRACTICE, O),
    ],
    "LangChain": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "LangGraph": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, O),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_PRACTICE, O),
    ],
    "Multi-Agent": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Deep Research": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Agent Harness": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Pi / Codex CLI / OpenClaw / Hermes": [
        (ACTIVITY_THEORY, O), (ACTIVITY_EXPERIMENT, R),
    ],
    "Agent Evaluation / LLM-as-Judge": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    # ---------- R5 ----------
    "推荐系统整体架构": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "协同过滤基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "Embedding Recall / 向量召回": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "双塔召回 Two-Tower": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "多路召回与 Candidate Generation": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Ranking 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "CTR 预估基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "Wide & Deep / DeepFM 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_INTERVIEW, O),
    ],
    "LR": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "GBDT / XGBoost": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "DNN": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "DIN / DIEN": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "搜索系统": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
    ],
    "BM25 与搜索召回": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "搜索结果融合 / RRF": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Embedding Recall / Search Retrieval": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Rerank / 重排基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R),
    ],
    "搜索 / 推荐 Reranking": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "用户画像与特征工程": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "推荐系统评估指标": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "推荐系统 Badcase 分析": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "推荐 / 搜索评估与 Badcase": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "LLM + Recommendation 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_CODE_READING, R),
        (ACTIVITY_EXPERIMENT, R), (ACTIVITY_PRACTICE, O),
    ],
    "推荐 / 搜索数据分析 SQL": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "数据挖掘": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    # ---------- R6 ----------
    "Python 语法与基础练习": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Python 面向对象与常用库": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "软件工程基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "数据结构与算法": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_INTERVIEW, R),
    ],
    "操作系统": [
        (ACTIVITY_THEORY, R), (ACTIVITY_INTERVIEW, R),
    ],
    "计算机网络": [
        (ACTIVITY_THEORY, R), (ACTIVITY_INTERVIEW, R),
    ],
    "数据库 / SQL 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_INTERVIEW, R),
    ],
    "Linux 常用命令与工具链": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "Git 版本控制与协作流程": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
    ],
    "C++ 基础": [
        (ACTIVITY_THEORY, R), (ACTIVITY_EXPERIMENT, R),
        (ACTIVITY_INTERVIEW, R),
    ],
}


def profile_for_topic(
    topic_name: str, route_key: str | None
) -> list[tuple[str, bool]]:
    """返回某 Topic 的初始 profile（显式覆盖优先，其次 route 默认，最后 theory）。"""
    if topic_name in CANONICAL_TOPIC_PROFILES:
        return list(CANONICAL_TOPIC_PROFILES[topic_name])
    if route_key in ROUTE_DEFAULT_PROFILES:
        return list(ROUTE_DEFAULT_PROFILES[route_key])
    return list(DEFAULT_PROFILE)
