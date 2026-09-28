# Study-Agent Architecture

> 面向 coding agent 的**精简**结构索引。目标是让 agent 用几个 `rg` / 定向读取
> 就能定位改动点，而不是反复整读 `main.py` / `main_window.py` / `schema.py`。
> 详细业务语义见 `README.md`；改代码前请对照 `docs/AGENT_GUIDE.md`。

## 1. 分层

```
app/
  main.py              # 入口：组装依赖 + 启动 QApplication / CLI 子命令（无业务逻辑）
  database/            # 数据层：connection / schema / 各领域 repository
  services/            # 业务逻辑层（最重要，~42 文件）
  ai/                  # LLM 客户端 / prompts / schema / planner
  ui/                  # PySide6 界面（page / dialog / worker）
  diagnostics/         # release migration / planner diagnostic（只读或发布工具）
  utils/               # date / single_instance 等
```

依赖方向：`ui → services → database`；`services → ai`；**database 不反向依赖 service**。
`main.py` 只做依赖注入（构造 repository / service / window）。

## 2. 核心领域模型（写给 agent）

```
study_plans ─ study_phases ─ study_topics ─┬─ knowledge_points (Assessment / Mastery)
                                           ├─ learning components
                                           └─ tasks
learning_routes (R1..R6 + JOB_PREP group) ── route-scoped plan / topic / task
```

- **Route → Phase → Topic → Activity → Task**：`canonical_route_service` 保证
  R1–R6 + JOB_PREP 存在；`study_plan_service` 管 phase/topic；`topic_learning_profile_service`
  管 topic 的 activity component；`task_service` / `daily_planner_service` 产 task。
- **Manual Learning (S5)**：产品不是通用 Todo Manager。Learning Activity（`source=manual, task_type=manual`）是一次性学习行为，不形成 Assessment/Mastery；Knowledge Learning（`task_type=new`）关联 Topic 或临时知识点，可验收。新建 UI 有 active route 时要求路线；历史 NULL-route manual 行保持可用，不做 schema migration。
- **Assessment / Mastery**：`assessment_service` → `knowledge_evidence` /
  `skill_service`。JD 30-day / market / skill priority 是 Planner 后台信号，不占据 Today UI。
- **Assessment evidence**：正式 Mastery 只由 Assessment 更新；weak points 与最近验收证据仍可供 Planner 使用。
- **Daily Review retired (S1)**：Daily Review / Review Scheduler / Daily Retention 已从生产产品中移除。
  Historical review rows/schema are retained for migration and history preservation only.
  Review-like recall will be handled by future Agent contextual learning, not by scheduled review tasks.
- **Capability**（独立维度，只由真实证据推导，不看 mastery 阈值）：
  `capability_service` + `capability_extractors` + `database/capability_repository`。
- **Practice**：`practice_project_service`（project / milestone / output）+
  `practice_evidence` + `practice_capability_service`。Level 5 只能由项目使用证据产生。
- **Planner Feedback / Readiness**：`practice_readiness` + `planner_feedback`
  给 Planner 提供确定性 Tier 排序；**不改 Scheduler**。
- **Global Scheduler**：`route_scheduler`（route allocation / fairness / budget
  ≤3 task & ≤180 min），**完全不读 Practice / Capability**。
- **JD / Market / Skill**：`jd_service` / `jd_summary_service` / `market_signal` /
  `skill_service`。JD 30-day / market / skill priority 是 Planner 后台信号，不占据 Today UI。
- **Today (S3)**：execution-focused learning surface；只呈现日期、当前阶段、Planner、路线筛选、两项 Summary、今日学习任务与添加入口。
- **AI Settings / Prompt**：`ai/config_service` + `ai/secrets` + `ai/prompt_registry`
  + `ai/prompt_defaults`；active definitions 有限，历史 override 保留在 DB。
- **Monthly retired (S2)**：Monthly UI、Summary/Stats services、Monthly AI 与 cache production path 已移除；`weekly_summaries` / `monthly_summaries` 仅为 LEGACY HISTORY，迁移与 verifier 继续保留。

## Agent core (Agent-1 through Agent-8 implemented)

Planner 决定学什么；Agent Runtime 围绕当前 Session 绑定的 Task 提供只读学习上下文与压缩后的对话连续性。

```text
Task → AgentSessionService → AgentRuntime → AgentToolRegistry
    → read-only Agent Tool → existing Service → Repository → SQLite
    → AgentModelClient → persistent assistant/tool messages
    → content-free Trace → deterministic Evaluation
```

- Agent-1 提供 task-bound Session、immutable multi-turn message persistence 与独立 `AgentModelClient`；Agent-1.1 verifier v5 保护 Agent 对话历史。
- Agent-2 提供 `app/agent/tools/` 中的 Tool Registry 与六个只读学习工具。工具只从 `AgentToolContext` 获取当前 session/task identity，所有参数 schema 均拒绝额外字段；不允许任意选择 task / route / topic / KP ID。
- Agent Runtime 通过 Service → Repository → SQLite 读取；Tool/Runtime 不直接访问 SQLite 或 Repository。工具只能读取，不能写 Task、Assessment、Mastery、Capability、Evidence 或 Practice。
- tool-call / tool-result 经 `AgentSessionService` 持久化；Runtime 从存储重建 `ModelMessage`，最多执行 4 个 tool rounds。无 Registry 时保持 Agent-1 no-tool 行为。
- legacy `AIClient`（`app/ai/interface.py`）保持不变，继续服务 Planner / Assessment / JD / TaskReview / Route Builder；Agent 使用独立的 `AgentModelClient.complete(ModelRequest)`。
- Agent-3：Today active Task → [开始学习]/[继续学习] → task-bound Session → internal Agent Workspace。`AgentTaskContextBuilder` 每 user turn 复用六个 read-only tools 构建一次 compact snapshot；`AgentTurnWorker` 在 worker thread 用 fresh SQLite connection 构建 Runtime 并执行，失败后从持久化 history reload。
- Agent Workspace 只是最后追加的 internal stack page，不在 `PageKey` / `PAGE_SPECS` / Sidebar 中；离开 Workspace 不关闭 Session；普通 UI 只显示 user 与最终 assistant 文本。
- Agent-4 提供静态 `AgentSkill` / `AgentSkillRegistry` / `AgentSkillSelector`：从同一 turn 的 `Task Context.task.activity_kind` 确定性选择教学策略，一轮选择一次、无模型分类调用、无 DB access、不授予 Tool 权限、不持久化 Skill key。
- Agent Skill prompt 是可信静态 instruction，置于 Task Context JSON 数据之前；Career `SkillService` / `skills` 表仍属于职业/技术技能域，二者严格分离。
- Agent-5 通过官方 `mcp>=2,<3` SDK 增加 per-user-turn stdio / Streamable HTTP MCP Tools。只有本地精确 `allowed_tools` 与 `readOnlyHint=True` 双重满足才暴露；MCP 是外部不可信数据，Native tools 始终保留且不允许 MCP mutation tools。
- MCP Context、Skill、Worker thread 和 Native Registry 语义保持；MCP 配置缺失/服务不可用不会禁用 Native 工具。
- Agent-6 添加 Task-ID-scoped `data/agent_workspaces/task_<id>/` 和五个 Sandbox tools。默认 Registry 仍只读；Sandbox 仅在显式 `sandbox` mutation scope 的 effective Registry 中可写。可选 Docker backend 无网络、只挂载当前 Task workspace、有资源/输出/时间界限；没有 host execution fallback。
- MCP Context、Agent Skill、Worker thread 和 Native Registry 语义保持；MCP/Sandbox 配置缺失或服务不可用不会禁用 Native 工具。MCP 仍 read-only；Sandbox 文件不等于 Task/Mastery/Capability/Practice evidence。
- Agent-7 添加 Session-scoped derived rolling Memory：`agent_messages` 仍 append-only；只摘要连续完整的历史 user turns，保留最近 4 个完整 turns 与当前 turn；summary 使用 `AgentModelClient`、`tools=()` 和严格字符上限。失败时使用上一份 summary + 安全完整-turn tail；Memory 不影响 Task Context / Skill / tools，不扫描 Sandbox。
- Agent Runtime 每 user turn 在 Task Context、Skill、MCP、Sandbox 之前准备一次固定 ConversationWindow；每个工具回合从同一边界重新加载新增原始消息。Workspace/UI 仍读取完整 `agent_messages`。
- 仍未实现：write tools / approvals。Agent-8 已增加 content-free Trace 与 deterministic Evaluation。Schema 为 v23 / fingerprint v5。详见 `docs/AGENT_ARCHITECTURE.md`、`docs/AGENT_MEMORY.md`、`docs/AGENT_TRACE_EVAL.md`、`docs/MCP.md` 与 `docs/SANDBOX.md`。

现有 `SkillService` / `skills` 表属于职业/技术技能域。Agent learning behavior 配置位于独立 `app/agent/skills/` 命名空间，绝不复用或重解释 Career Skill 表。详见 `docs/AGENT_ARCHITECTURE.md`。

## 3. 关键不变式（CRITICAL INVARIANTS）

1. **Route 身份**用稳定 `route_key`（如 `R3_LLM_INFRA`），不用显示名。
   旧「搜广推 + LLM」为 `LEGACY_SEARCH_LLM`，archive 保留、不删除、不复用。
2. **canonical seed 所有权**：system-owned metadata 可同步；user-owned runtime state
   （`status` / `priority` / `planning_enabled` / `archived_at`）**绝不被 seed 覆盖**。
   archive 不自动 restore。
3. **同一 route 只能有一个 active plan**（v15 partial unique index）。
4. **Mastery / Capability 分离**；Assessment 是 Mastery 正式更新路径。Review schema/rows 仅作为 legacy history 保留。
5. **Practice 不反作用于 Scheduler / priority / budget**；route.priority 只由用户控制。
6. **历史完整性**：已产生过 evidence 的 Output / Project-Topic 禁止物理删除；
   冻结 `output_type/description/uri/details_json`，撤销用 `is_active=0` + `revoked_at`。
7. **Migration Gate**：正式库 `user_version < SCHEMA_VERSION` 时 GUI 拒绝启动（退出码 3），
   必须走 `db-release backup/inventory/migrate/verify`。
8. **release / legacy 迁移只走真实 `migrate_stepwise()` 路径**，不得使用
   `initialize_fresh_database()`（后者仅用于全新空库）。
9. **Verifier 历史保留是子集语义**（fingerprint v5；Agent Session/Message immutable history 亦受保护）：
   before IDs 必须仍是 after 的子集且 immutable 字段不变；after 新增业务行合法。
   不能要求正式库迁移后冻结不增长。
10. **不改 schema 语义**：新增表/列 = 新 migration + 提升 `SCHEMA_VERSION`；
   测试快路径只是「预置等价 schema」，不是新的迁移逻辑。

## 4. DB schema 版本（v23）

- `PRAGMA user_version` 持久化版本；`SCHEMA_VERSION = 23`（`app/database/schema.py`）。
- `_MIGRATIONS`: v2..v23 幂等迁移；`migrate_stepwise(conn, on_step=...)` 暴露逐级过程。
- 空库真实路径：`create_schema()`（基础表） + v2..v23 逐级执行。
- **测试快路径**：`initialize_fresh_database(conn)` → 运行时从真实迁移反推当前完整
  DDL + 种子，一次性建好并写 `user_version=23`，**不重放**历史迁移。
  与真实路径在空库上的结果逐字一致（含 `learning_routes` 种子）。
- 关键历史节点：v12 canonical routes seed / v13 KP route 唯一 / v15 单 active plan /
  v16 legacy theory backfill / v18 capability / v19 practice evidence / v20 requirements /
  v21 agent_sessions + agent_messages / v22 `agent_session_memory`（derived memory）/ v23 `agent_turn_traces`, `agent_trace_events`, `agent_turn_evaluations`（operational telemetry）。
- Memory / Trace / Evaluation 表进入 verifier `GROWTH_TABLES` 与 inventory，不进入 immutable `HISTORY_TABLES` / fingerprint；`FINGERPRINT_VERSION` 保持 v5，原始 Agent Session/Message history 仍受保护。

## 5. 依赖边界（不要越界）

| 想改的东西 | 允许触碰 | 禁止顺手改 |
|---|---|---|
| Planner 排序 | `daily_planner_service`, `planner_feedback`, `planner_context` | `route_scheduler`, `practice_*` |
| Scheduler | `route_scheduler` | Practice / Capability |
| Capability | `capability_*`, `capability_repository` | Mastery / Scheduler |
| Practice | `practice_*`, `practice_repository` | Mastery / route.priority / Scheduler |
| Migration | `schema`, `route_migration_service`, `diagnostics/release_migration` | 业务 service 行为 |
| UI | 对应 `ui/*.py` | 业务 service（UI 只调用） |
| AI prompt | `ai/prompts`, `ai/prompt_defaults`, `ai/prompt_registry` | Planner 硬 gate |

## 6. 测试与数据库

- `tests/conftest.py::conn` 使用 **fast fresh path**（`get_fresh_connection`，
  `synchronous=OFF`），只建当前 schema，不重放迁移。
- migration / WAL / release / verifier 测试必须用 `get_connection()` 或
  `migrate_stepwise()` **真实路径**。
- pytest markers：`slow` / `migration` / `ui` / `integration`（见 `pytest.ini`）。
  默认 `pytest -q` 仍跑完整 suite。
