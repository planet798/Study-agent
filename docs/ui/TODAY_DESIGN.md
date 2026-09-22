# Today Workspace Design (UI-3)

> Today 页面回答唯一问题：**“今天我应该做什么？”**
> TodayPage 是纯 View；业务编排仍在 MainWindow。

## Information hierarchy

```
PageHeader     今日 · <日期>
PageBody
  Summary Metrics   待处理 · 预计时长 · 今日复习
  Controls          路线筛选 · 完成统计 · ＋ 添加学习任务
  Phase context     当前阶段 / 今日目标
  Planner context   AI 规划状态 + 重新规划（SAInfoBanner）
  Focus             今日学习（new tasks）
  Review            今日复习（review tasks）
  Career Signals    技能概览 / JD 趋势 / 候选 / Gap（secondary）
```

职业信号统一放在任务之后，视觉权重低于任务区（提示 `职业信号` 区块标题）。

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
| 待处理 | `status != cancelled` 且 `status in (active, not_done)` 且匹配当前路线筛选 |
| 预计时长 | 上述待处理任务 `estimated_minutes` 之和 |
| 今日复习 | `task_type == "review"` 且 `status in (active, not_done)`、匹配筛选 |

cancelled 永不进入任何指标。数据不可靠时宁可不显示，也不发明统计。

## TaskWidget

保留 class、signals 与业务行为，重做视觉：

- 标签统一 `SATag`（无 `【】`）：Route=accent/neutral、Activity=info、
  Source=neutral、Review=info(每日巩固)/warning(到期复习)。
- 操作层级（`SAButton`）：完成=primary、开始验收=secondary、
  未完成=danger、延期=secondary、移除今日任务=subtle。
- Done ≠ Mastery：done 的正式任务仍显示“已完成”+ 验收入口。
- 正式 Review 不提供 remove（规则不变）。
- 延期 ≥3 次保留 warning 语义（semantic warning，不只靠黄色）。

## Empty state

`SAEmptyState`：“今天还没有学习任务 / 可以添加任务，或等待学习计划生成” +
`添加学习任务` action。Planner 不可用时不承诺一定会生成计划。

## Scroll restore（不变）

`capture_today_view_state` / `restore_today_view_state` 保留原行为，
重试时机仍为 `0 / 16 / 60 / 160 / 400 / 800 ms`。UI-3 未修改。
