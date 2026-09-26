# Agent Architecture

> 本文档记录 Agent 运行时的分层、边界与分阶段演进。Agent 不是 Generic chatbot、不是另一个 Planner、不是 Todo/Mastery/Capability editor。

## 长期链路（目标）

```text
Learning Route → Phase → Topic → Learning Component → Task
→ Agent Study Session → Agent Runtime
→ Tools / Skills / MCP / Sandbox
→ Learning Interaction → Assessment / Evidence → Mastery / Capability
```

## Agent-1（已实现）

```text
Task
→ AgentSessionService   (app/agent/session.py)
→ AgentRuntime          (app/agent/runtime.py)
→ AgentModelClient      (app/ai/agent_protocol.py + agent_client.py)
→ OpenAI-compatible /chat/completions
```

数据流：

```text
tasks.id ──< agent_sessions (task_id, status ∈ {active, closed})
                └──< agent_messages (role, content, tool_*, metadata_json)
```

### 组件职责

| 组件 | 职责 | 明确不做 |
|---|---|---|
| `AgentRepository` (`app/database/agent_repository.py`) | agent_sessions / agent_messages 持久化 | 调 AI、决定 prompt、动 Mastery/Capability |
| `AgentSessionService` (`app/agent/session.py`) | task-bound session 创建/恢复/关闭；消息追加（不可编辑/删除） | 直接查 tasks raw SQL / TaskRepository |
| `AgentRuntime` (`app/agent/runtime.py`) | 组装 multi-turn `ModelRequest`、调用模型、持久化 assistant 回复 | 直接访问 SQLite/Repository、发送/执行 tools、写 Mastery/Capability、完成 Task |
| `AgentModelClient` (`app/ai/agent_protocol.py`) | provider-independent multi-turn 模型协议（含 tool 结构） | 取代或改写 legacy `AIClient` |
| `AdaptiveAgentModelClient` (`app/ai/agent_client.py`) | OpenAI-compatible 调用，复用现有 Profile/API 设置 | 新建第二套 API 设置、默认 `response_format=json_object` |

### 关键语义

- **Task-bound**：禁止 `task_id=NULL` 的 generic chat session；一个 Task 同时最多一个 `status='active'` session（partial unique index 强制）。closed 后可再次 `start_or_resume` 创建新 session。
- **Message immutable**：只提供 append / list；不提供 update / delete，历史交互可审计。
- **Multi-turn**：每轮按 message id 顺序发送 `system + user1 + assistant1 + ... + current user`，不是只发当前 prompt。
- **Persistence order**：校验 session → 写入 user message → 重载历史 → 构造请求 → `model.complete()` → 写入 assistant message。模型失败时 user message 保留、assistant 不写，抛 `AIServiceError`，不自动无限重试。
- **No-tool fail-safe**：`request.tools = ()`；若 provider 仍返回 `tool_calls`，Runtime 不执行、不伪造 tool result，抛 `AgentRuntimeError`，已写入的 user message 保留。
- **不了解上下文压缩**：Agent-1 完整加载 session history；token counting / summarization / compaction 属于后续 memory 阶段（代码中留有 TODO）。
- **Metadata 安全**：assistant message 只记录 `model` / `finish_reason` / `usage`；绝不保存 API key、Authorization、secret_ref 内容或完整 `RuntimeAIConfig`。

### 需要保持的边界

- legacy `AIClient` / `DeepSeekClient.chat()` / `AdaptiveAIClient.chat()` / `send_chat_request()` **零改动**，继续服务 Planner / Assessment / JD parse / TaskReview / Route Builder。
- Agent Tool 不得直接操作 Repository 或 raw SQLite：必须 `Agent Tool → existing Service → Repository → SQLite`。
- Agent 不能直接 set Mastery 或 Capability：只有 `Assessment → Mastery` 与真实 `Evidence → Capability`。
- `SkillService` / `skills` 表是职业/技术技能域；Agent Skills 必须使用独立命名（`AgentSkill`、`AgentSkillRegistry`、`agent/skills/`）。
- Sidebar 仍为 Today / Learning Routes / Practice / Settings。

## 未来演进

| 阶段 | 内容 |
|---|---|
| Agent-2 | Native Tool Registry + read-only learning tools（经 Service 访问） |
| Agent-3 | TaskContext Builder + Agent Workspace UI（Today Task → [开始学习]） |
| Agent-4 | Agent Skills（独立命名空间） |
| Agent-5 | MCP client |
| Agent-6 | Sandbox |
| Agent-7 | Memory / context compaction |
| Agent-8 | Trace / evaluation |

## 测试

- `tests/test_agent_model_client.py`：payload / 解析 / 错误 / sanitize / 动态 Profile / legacy AIClient 不变。
- `tests/test_agent_session.py`：task-bound、resume、隔离、close 语义、消息不可编辑。
- `tests/test_agent_runtime.py`：first/second turn、模型失败、blank、closed、隔离、tool_call fail-safe。
- `tests/test_agent_architecture.py`：边界与 vertical slice。
