# Today Workspace Design (UI-3)

> Today = execution-focused learning surface；回答“今天学什么、为什么、怎么开始？”
> TodayPage 是纯 View；业务编排仍在 MainWindow。

## Information hierarchy

```
PageHeader     今日 · <日期>
PageBody
  Summary Metrics   待处理 · 预计时长
  Controls          路线筛选 · 完成统计 · ＋ 添加学习任务
  Phase context     当前阶段 / 今日目标
  Planner context   AI 规划状态 + 重新规划（SAInfoBanner）
  今日学习          active learning tasks
```

Skill / JD / Market 作为 Planner 的后台信号，不直接展示为 Today dashboard。

## TodayPage extraction

`app/ui/today_page.py::TodayPage` 负责 layout / widgets / visual state / UI signals：

- signals：`add_task_requested`、`route_filter_changed`、`replan_requested`
- View API：`set_summary_metrics` / `set_phase_context` / `set_planner_state` /
  `clear_tasks` / `add_task_widget` / `add_section_header` / `add_section_hint` /
  `set_empty_visible` / `set_scroll_visible`

MainWindow 保留 orchestration（service 调用、task handlers、assessment、worker、
refresh、scroll restore）与 compatibility aliases：
`scroll / list_container / list_layout / empty_hint / route_filter_combo /
route_stats_label / phase_container / phase_label / phase_goal_label /
planner_container / planner_status_label / planner_note_label / planner_replan_btn /
add_task_btn`。

## Summary metrics semantics

只从 `refresh()` 已获取的 `tasks` 推导，**不新增 DB query**：

| 指标 | 定义 |
|---|---|
| 待处理 | 非 historical review、非 cancelled 且 `status in (active, not_done)` 且匹配当前路线筛选 |
| 预计时长 | 上述待处理任务 `estimated_minutes` 之和 |
| Historical review tasks | 不展示、不计入任何 Today 指标或 completion metric |

cancelled 永不进入任何指标。数据不可靠时宁可不显示，也不发明统计。

## TaskWidget

保留 class、signals 与业务行为，重做视觉：

- 标签统一 `SATag`（无 `【】`）：Route=accent/neutral、Activity=info、
  Source=neutral。
- Agent enabled 的 active task 操作层级（`SAButton`）：开始/继续学习=primary、完成=secondary、开始验收=secondary、未完成=danger、移除今日任务=subtle。
- Agent dependencies 未注入时保持旧 fallback：完成=primary。Done / cancelled / not_done 均不出现 Agent start。
- Done ≠ Mastery：done 的正式任务仍显示“已完成”+ 验收入口。
- Historical review rows 不由 Today 渲染。
- 延期 ≥3 次保留 warning 语义（semantic warning，不只靠黄色）。

## Empty state

`SAEmptyState`：“今天还没有学习任务 / 可以添加任务，或等待学习计划生成” +
`添加学习任务` action。Planner 不可用时不承诺一定会生成计划。

## Scroll restore（不变）

`capture_today_view_state` / `restore_today_view_state` 保留原行为，
重试时机仍为 `0 / 16 / 60 / 160 / 400 / 800 ms`。UI-3 未修改。

## S1 — Daily Review retirement

Daily Review / Review Scheduler / Daily Retention 已从生产产品中移除。
Historical review rows/schema are retained for migration and history preservation only.
Review-like recall will be handled by future Agent contextual learning, not by scheduled review tasks.

## S3 — Today simplification

只有日期、两项 Summary、路线筛选/当前阶段、Planner 状态与说明、今日学习任务及手动添加入口。无任务时即使存在 JD/Skill 数据也显示学习空状态。`task_type=new` 历史语义不变。

## Agent-3 — Task-driven Agent Workspace

Today 的 active task 卡片可以「开始学习 / 继续学习」，进入 task-bound internal Agent Workspace；它不是 Sidebar page。返回 Today 不关闭 Session。Workspace 每个 user turn 构建一次小型只读 Task Context，tool loop 继续按需读取；模型调用在 worker thread 使用独立 DB connection。普通会话 UI 只显示 user 与最终 assistant plain text，不展示 tool protocol。

## S5 — Manual Learning

Today 的“今日学习”统一展示 Agent 规划任务、手动学习活动与知识学习；按钮仍为“添加学习任务”。对话框默认“知识学习”（关联 Topic/KP，可验收）；“学习活动”是一次性学习行为，不进入 Assessment。存在 active 学习路线时新任务要求选择路线；历史未分类 manual 行继续显示/操作。Study-Agent does not provide a generic Todo manager.
