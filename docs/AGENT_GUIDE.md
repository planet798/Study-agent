# AGENT GUIDE — Study-Agent

> 给 coding agent 的**导航 + 工作流**手册。先读本文件，再按“任务类型”表定位文件，
> 用 `rg -n` 找 symbol、定向读区间，避免整读大文件。项目结构与不变式见
> `docs/ARCHITECTURE.md`。

最新进度主要参考仓库 Markdown 文档，先读 [`CODEX_HANDOFF.md`](../CODEX_HANDOFF.md)。
旧聊天中的“下一步”不是当前任务授权；已完成阶段不能据此重新实施。
个性化阶段完成记录与当前检出版本的差异见 [`PERSONALIZATION.md`](PERSONALIZATION.md)。

## 0. 开发流程规则（永久生效，务必遵守）

```
开发中（每次编辑后）      → 只跑 targeted tests（单文件 / -k）
一个逻辑单元完成          → 跑相关 regression 文件
整个任务结束              → full pytest 只跑一次
full 失败                 → 只重跑失败测试，修复
修好后                    → 最后再 full 一次
```

**禁止**：每个小改动后都跑 full pytest。当前 full suite 约 20–38 分钟，
反复全量运行是开发变慢的最大原因之一。

上述测试流程用于功能开发；纯文档同步只核对事实、路径、命令和 `git diff --check`，
不为文档修改运行功能测试或声称重新验证历史测试结果。

常用命令：

```bash
.venv/bin/pytest tests/test_<module>.py -q            # targeted
.venv/bin/pytest tests/test_a.py tests/test_b.py -q   # related regressions
.venv/bin/pytest -m "not slow" -q                     # broad 开发回归（只排除显式 expensive cases）
.venv/bin/pytest -m migration -q                      # 只跑迁移
.venv/bin/pytest -m ui -q                             # Qt widget/qapp/event loop
.venv/bin/pytest -m threaded -q                       # 线程生命周期/资源
.venv/bin/pytest -m integration -q                    # 多生产子系统组合
.venv/bin/pytest -q                                   # 任务收尾 full（默认仍跑全量）
```

> 注意：pytest 不在 PATH 上，必须用 `.venv/bin/pytest`。

## 1. Context Efficiency（省 token = 省时间）

- 优先 `rg -n "<symbol>" app tests` 定位，再 `read` 指定行区间。
- **不要**每次重新整读：`app/main.py`(1600 行)、`app/ui/main_window.py`(1856 行)、
  `app/database/schema.py`(1419 行)，除非任务确实需要完整文件。
- 不要重复跑 `git status` / `git diff`：一次改动批量做完再统一查看。
- 改动前先看本文件对应任务的“禁止顺手改”列，避免波及其它领域。

## 2. 任务类型 → 文件 → 测试

### Planner / 每日规划
- production：`app/services/daily_planner_service.py`、`app/services/planner_feedback.py`、
  `app/ai/planner.py`、`app/ai/planner_context.py`、`app/ai/prompts.py`
- 数据：`app/database/repository.py`、`app/database/study_plan_repository.py`
- 测试：`tests/test_daily_planner_service.py`、`test_planner_feedback.py`、
  `test_planner_feedback_ai.py`、`test_daily_planner_dynamic_priority.py`、
  `test_planner_evidence.py`、`test_planner_gate_coverage.py`
- 不变式：硬 gate（phase / prerequisite / 去重）优先于 Tier；AI 不得越 Component；
  Scheduler 不读 Practice / Capability。

### Practice / 项目 / 产出
- production：`app/services/practice_project_service.py`、`practice_readiness.py`、
  `practice_capability_service.py`、`practice_evidence.py`
- 数据：`app/database/practice_repository.py`
- 测试：`tests/test_practice_project_service.py`、`test_practice_readiness.py`、
  `test_practice_capability_service.py`、`test_practice_evidence_history.py`、
  `test_practice_ui.py`
- 不变式：被 evidence 用过的 Output / Project-Topic 禁止物理删除；撤销用
  `is_active=0`；Level 5 只由显式确认的项目使用证据产生。

### Capability
- production：`app/services/capability_service.py`、`capability.py`、
  `capability_extractors.py`
- 数据：`app/database/capability_repository.py`
- 测试：`tests/test_capability_service.py`、`test_capability_schema.py`、
  `test_capability_backfill.py`、`test_practice_capability_*`
- 不变式：Capability 只由真实证据推导；不修改 mastery / review / scheduler。

### Route / Canonical / Curriculum
- production：`app/services/canonical_route_service.py`、`canonical_routes.py`、
  `learning_route_service.py`、`study_plan_service.py`、`route_plan_service.py`、
  `app/database/learning_route_repository.py`
- 测试：`tests/test_canonical_routes.py`、`test_canonical_state_preservation.py`、
  `test_learning_routes.py`、`test_study_plan_service.py`、`test_route_data_isolation.py`
- 不变式：route_key 稳定；system vs user-owned 分离；单 active plan。

### Scheduler（全局多路线）
- production：`app/services/route_scheduler.py`
- 测试：`tests/test_multi_route_scheduler.py`、`test_phase6_regression.py`
- 不变式：budget ≤3 task & ≤180 min；fairness；**完全不读 Practice / Capability**。

### Manual Learning (S5)
- Study-Agent does not provide a generic Todo manager. 新建 UI：学习活动（一次性，无 KP/验收）与知识学习（Topic/KP，可验收）；有 active route 时要求选择 route，无路线时安全降级。
- 生产用 `ManualTaskService.create_learning_activity()`；`create_todo()` 仅为 legacy compatibility alias。历史 `task_type=manual, route_id=NULL` 不删除、不补路线，状态机与 PastTaskConfirmation 保持不变。测试：`tests/test_manual_learning_semantics.py`。

### Assessment / Mastery（Daily Review retired）
- production：`app/services/assessment_service.py`、`skill_service.py`、
  `knowledge_evidence.py`、`app/database/assessment_repository.py`
- Daily Review / Review Scheduler / Daily Retention 已从生产产品中移除；历史表和字段只用于迁移与历史保全。未来 recall 是 Agent contextual learning 方向，不是已实现功能。
- 测试：`tests/test_assessment_*.py`、`test_review_retirement.py`、`test_skill_service.py`

### Agent core (Agent-1 through Agent-11 + Workspace-1 + Learning Shell-1)
- production：`app/agent/context.py`、`app/agent/runtime.py`、`app/agent/memory/{policy,compactor}.py`、`app/agent/trace/{models,collector,service}.py`、`app/agent/eval/evaluator.py`、`app/agent/approval/{service,tools,provider}.py`、`app/database/agent_approval_repository.py`、`app/database/agent_{memory,trace,evaluation}_repository.py`、`app/agent/skills/{base,registry,selector,learning}.py`、`app/agent/tools/{base,registry,learning}.py`、`app/agent/mcp/{config,client,tools,provider}.py`、`app/agent/sandbox/{config,workspace,backend,tools,provider}.py`、`app/ui/agent_workspace_page.py`、`app/ui/agent_message_widget.py`、`app/ui/agent_task_context_card.py`、`app/ui/agent_composer.py`、`app/ui/components/flow_layout.py`、`app/ui/ai_worker.py::AgentTurnWorker`、`app/main.py::build_agent_runtime`。
- tests：Agent-11 Workspace status/error/diagnostic/worker lifecycle; Agent-10 Assessment/Note Approval repository/service/tools/runtime/UI/integration/privacy; Agent-9 completion approval/worker/registry; Agent-8 Trace repository/collector/runtime/privacy 与 deterministic Evaluation; Agent-7 Memory repository/compaction; Agent-4 skills/runtime、Agent-5 MCP config/client/discovery/tools/runtime、Agent-6 Sandbox config/workspace/permissions/tools/backend/runtime，以及 Agent-3 Workspace 与 Agent-1/2 regressions。
- 不变式：每 user turn Memory window / Task Context / Skill 各准备一次；Memory before MCP/Sandbox；tool loop 固定边界、每轮 reload 边界之后的新消息；Skill 确定性选择一次；MCP scope 只在 worker turn 存活。
- Memory 是 Session-scoped derived prefix summary；按 `role=user` 分组，只摘要连续完整旧 turns，当前 turn 永不 summary，默认保留最近 4 个完整历史 turns。原始 `agent_messages` append-only 且 UI 仍显示全部；summary failure fail-soft，当前 turn 超硬上限则受控失败且已保存 user 保留。
- 三个 Approval Tools（完成任务、开始/恢复正式验收、保存有界学习笔记）仅写 pending 元数据；Workspace 用户明确点击批准后才由 worker fresh connection 执行现有 TaskService/AssessmentService/LearningOutcomeService。审批表不复制笔记正文；批准时重新验证 immutable Tool Call。Approval Requests/Events 属于 v6 指纹保护的授权历史；不解析聊天批准、不记忆权限。详见 `docs/AGENT_APPROVALS.md`。
- Trace/Evaluation 是 content-free derived operational telemetry：Trace 只存受控 metadata、时长、usage/counts/flags，绝不复制 prompt/message/tool/MCP/Sandbox 正文；Evaluation 只由 Trace + Events 确定性运行规则，无 LLM judge、质量分、学习推断或业务写入。Trace/Eval persistence failure fail-open。
- Agent Workspace 是 stack 内部页，不是静态 PageKey/PageSpec；Learning Shell-1 在 Sidebar 显示动态学习会话（最近 10 个 active Session + 必要时当前 Session），按 session_id 直接打开，origin Task 的状态不决定 Session 可访问性，session.task_id 保持 immutable。Worker 仅接收 db_path/runtime_factory/session_id/user_text，在线程内构造 fresh connection/runtime；离开页面不关闭 Session。Workspace-1 新增 Task-scoped 显式文件 Workspace 绑定（none/managed/local），与内部 Agent Session 页面不是同一个概念：`app/services/workspace_service.py` → `app/database/task_workspace_repository.py`；UI compact header selector/menu 经 MainWindow/service 操作绑定，Runtime 每 turn 取得冻结的 `AgentWorkspaceSpec`，SandboxProvider 不查数据库。无绑定无文件 Tools；managed bounded read/write/mkdir（无 sandbox.json 也可用）；local 仅 list/read 且敏感路径拒绝；绝对 host path 仅 UI 可见，不进模型/Trace。详见 `docs/WORKSPACES.md`。Agent-11 状态只读本地配置（不连 MCP、不探测 Docker），异常映射安全有限文案；同一 Session 中 turn 与 approval execution 串行，退出时等待 worker。`agent-diagnostic` 在 GUI 启动前只读运行。详见 `docs/AGENT_PRODUCTION.md`。Session task-bound；Native Tools 仍只读/Service-only；MCP 故障不禁用 Native Tools；legacy `AIClient.chat()` 与 Agent-1 no-tool 路径保持。
- Agent Skills 是 `app/agent/skills/` 下的静态学习策略；严禁与 Career `SkillService` / `SkillRepository` / `skills` 表混用。Agent-5 MCP 是本地 exact allowlist + server `readOnlyHint=True` 双 gate，只读、不访问 SQLite。Agent-6 仅通过 `sandbox` mutation scope 改写 task workspace；默认 Registry 仍只读，无 host-shell fallback、无 Application/DB mutation scope。Memory 不读取 Task/Practice repositories、MCP client 或 Sandbox filesystem；Eval 不读 conversation/AI，Trace 不授予 Tool 权限。详见 `docs/AGENT_MEMORY.md`、`docs/AGENT_TRACE_EVAL.md`、`docs/MCP.md` 与 `docs/SANDBOX.md`。
- UX-1 消息呈现：User 消息与 Approval Card 恒为 PlainText；Assistant 消息在 `app/ui/agent_message_widget.py` 用 Qt 原生 `QTextDocument`（`MarkdownNoHTML | MarkdownDialectGitHub`）安全渲染 Markdown，禁用 raw HTML、外部链接打开与所有 resource fetch（http/file/qrc）。DB 仍存原始 Markdown，渲染不改 persistence / Memory / Trace / Approval。默认渐进式交互教学由 system prompt 与 learning Skills 指导，不是硬 token 上限。详见 `docs/AGENT_PRODUCTION.md`。
- 当前 Learning Shell-1 信息层级：全局 `SAPageHeader` 显示 Session title + route secondary text；页面内为紧凑 route/activity/duration metadata、可展开任务信息、Workspace selector/menu、capability chips、conversation、待确认操作 section、bottom composer（`Ctrl+Enter` 发送 / `Enter` 换行；近上限才显示计数）。返回 Today 使用 Sidebar，不再有返回按钮；绝对路径仅 tooltip/menu 可查看和复制。capability chips 只表达本地 configuration 快照，不得声称“在线”；same-session reload 保留未发送 draft，切换 Session 才清空。approval 文案由 application 静态映射产生，PlainText，模型不可控制。UX-3：新 Assistant 回复定位到消息首行；忙碌期间用户阅读历史则保留位置并显示“新回复 ↓”；代码/表格安全换行，正文按 viewport 缩放，消息仍只有外层 scrollbar。详见 `docs/AGENT_PRODUCTION.md`。

### Migration / Schema（**高危，必须真实路径**）
- production：`app/database/schema.py`、`app/database/connection.py`、
  `app/services/route_migration_service.py`、`app/diagnostics/release_migration.py`
- 测试：`tests/test_schema_migrations.py`、`test_six_route_migration.py`、
  `test_activity_migration.py`、`test_route_migration.py`、
  `test_migration_verifier_regression.py`、`test_release_migration_hardening.py`、
  `test_end_to_end_learning_system.py`
- 规则：新增 schema = 新 `_migrate_vN` + 提升 `SCHEMA_VERSION`；迁移测试必须用
  `get_connection()` / `migrate_stepwise()`，**不得**用 `initialize_fresh_database()`。
- Migration Gate 行为不得放宽。

### UI（PySide6 / offscreen）
- production：`app/ui/main_window.py`、`routes_page.py`、`practice_page.py`、
  `ai_settings_page.py`、各 `*_dialogs.py`、`task_widget.py`
- 测试：`tests/test_main_window.py`、`test_*_ui.py`、`test_today_scroll_preservation.py`、
  `test_practice_ui.py`、`test_ui_integration.py`
- 不变式：UI 只调用 service，**禁止**顺手修改业务 Service 逻辑。

### AI / Prompt / Settings
- production：`app/ai/client.py`、`config_service.py`、`prompt_registry.py`、
  `prompt_defaults.py`、`prompts.py`、`schemas.py`、`secrets.py`
- 测试：`tests/test_ai_client.py`、`test_prompt_registry.py`、`test_ai_profiles.py`、
  `test_ai_settings_ui.py`

### Monthly Summary retired (S2)
- Monthly Dashboard / SummaryService / StatsService / Monthly AI prompt 与 cache writer 已退出生产产品；历史 `weekly_summaries` / `monthly_summaries` 表及用户 prompt override 仅保留兼容；Agent-1 已把 SCHEMA_VERSION 提升到 21（仅新增 agent 表）。
- Route Detail / Practice 页面不依赖 Monthly；JD 30-day trend 使用独立的 `JdSummaryService`，必须保留。

### Today (S3)
- Today = execution-focused learning surface：日期、阶段、Planner、路线筛选、两项 Summary、今日学习任务及添加入口；无学习任务时按学习内容判断 empty。JD / Market / Skill 为 Planner 后台信号，不在 Today 展示。
- Routes 保留 SkillService；CLI 保留 add-jd / add-jd-summary / jd-trends。相关回归 `tests/test_today_simplification.py`。

### JD / Skill / Market
- production：`app/services/jd_service.py`、`jd_summary_service.py`、`market_signal.py`、
  `skill_service.py`
- 测试：`tests/test_jd_*.py`、`test_market_skill_priority.py`、`test_skill_service.py`

## 3. 测试基础设施（本轮新增）

- `tests/conftest.py::conn` → `get_fresh_connection()`：直接建当前 v27 schema,
  **不重放 v2..v27**（约 280ms → 个位数 ms）。与真实迁移在空库上的 schema 逐字一致。
- `app/database/connection.py::get_connection()`：**production 真实路径**，仍走
  `migrate()`。改动它要非常谨慎。
- markers：`slow` / `migration` / `ui` / `integration` / `threaded`，见 `pytest.ini`。分类显式写在 module/class/function；UI fixture hook 识别 qtbot/qapp。slow 表示真实成本，不等同于全部 migration/integration/Qt。
  默认 full suite 语义不变。

## 4. 快速自检清单（提交改动前）

1. 只改了目标领域文件？（对照 §2 的“禁止顺手改”）
2. 相关 targeted tests 过了吗？相关 regression 过了吗？
3. 是否触及 schema / migration？若是，跑 migration 测试且保持真实路径。
4. 是否整读了大文件？能否换成 `rg -n` + 定向读？
5. 任务收尾跑一次 full pytest。
