# AGENT GUIDE — Study-Agent

> 给 coding agent 的**导航 + 工作流**手册。先读本文件，再按“任务类型”表定位文件，
> 用 `rg -n` 找 symbol、定向读区间，避免整读大文件。项目结构与不变式见
> `docs/ARCHITECTURE.md`。

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

常用命令：

```bash
.venv/bin/pytest tests/test_<module>.py -q            # targeted
.venv/bin/pytest tests/test_a.py tests/test_b.py -q   # related regressions
.venv/bin/pytest -m "not slow" -q                     # 快速迭代（跳过迁移/集成）
.venv/bin/pytest -m migration -q                      # 只跑迁移
.venv/bin/pytest -m ui -q                             # 只跑 GUI/offscreen
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

### Assessment / Mastery / Review
- production：`app/services/assessment_service.py`、`review_service.py`、
  `skill_service.py`、`knowledge_evidence.py`、`app/database/assessment_repository.py`
- 测试：`tests/test_assessment_*.py`、`test_review_service.py`、`test_skill_service.py`

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

### JD / Skill / Market
- production：`app/services/jd_service.py`、`jd_summary_service.py`、`market_signal.py`、
  `skill_service.py`
- 测试：`tests/test_jd_*.py`、`test_market_skill_priority.py`、`test_skill_service.py`

## 3. 测试基础设施（本轮新增）

- `tests/conftest.py::conn` → `get_fresh_connection()`：直接建当前 v20 schema，
  **不重放 v2..v20**（约 280ms → 个位数 ms）。与真实迁移在空库上的 schema 逐字一致。
- `app/database/connection.py::get_connection()`：**production 真实路径**，仍走
  `migrate()`。改动它要非常谨慎。
- markers：`slow` / `migration` / `ui` / `integration`，见 `pytest.ini`。
  默认 full suite 语义不变。

## 4. 快速自检清单（提交改动前）

1. 只改了目标领域文件？（对照 §2 的“禁止顺手改”）
2. 相关 targeted tests 过了吗？相关 regression 过了吗？
3. 是否触及 schema / migration？若是，跑 migration 测试且保持真实路径。
4. 是否整读了大文件？能否换成 `rg -n` + 定向读？
5. 任务收尾跑一次 full pytest。
