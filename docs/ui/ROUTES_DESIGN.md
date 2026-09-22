# Learning Routes Design (UI-3)

> Routes 页面回答：**“各条路线进行到哪里、接下来学什么、掌握与能力分别如何？”**

## Overview（Route Dashboard）

- 分组（如“求职准备”）用 `SACard` 作为 group heading，不与普通路线卡片混淆。
- 每条 canonical/learning route 一张 `SACard(variant="interactive")`。

### Route Card 信息层级

```
[名称]  [route_key 前缀]                         [状态徽标]
当前阶段：…
当前 Topic：…
下一步：…（必需但未完成的学习活动）
优先级：…　自动规划：…
课程进度 ▓▓▓▓░ done/total
掌握度 Mastery ▓▓▓░░ xx%        （无 assessment 时为“暂无验收数据”）
能力 Capability  [Lx · label]      待复习 N
[查看路线] [调整] [暂停/恢复自动规划] [归档]
```

- 优先：名称 / 当前阶段 / 当前 Topic / 下一步。
- 其次：课程进度 / Mastery / Capability。
- 再次：状态 / Priority / Review due。
- 复杂技能 / Gap / Projects 仍在 Route Detail。

## Mastery vs Capability（关键）

- **Mastery** 是连续度量 → `SAProgressBar`（百分比）；无验收数据显示“暂无验收数据”。
- **Capability** 是离散等级 → `SATag` + 现有 service 提供的 label；
  **绝不画成百分比**，**绝不与 Mastery 共用同一进度条**。
- Route Card 的 Capability 取该路线知识证据中现有的 `capability_label`
  （来自 `RouteProgressService`），不重新定义 level。

## Route Status

`SAStatusBadge`：`active` / `paused`（暂停自动规划，Review 仍继续）/
`archived`（不再产生新学习与新 Review）。文案不模糊这两者。

## Route Detail（仍为 Dialog）

保持 `RouteDetailDialog`（降低行为风险），内部按 section 组织：

1. Overview（目标 / 描述 / 优先级 / 自动规划 / 状态 / 路由进度 / 掌握）
2. Learning Progress（Topic 完成 / 学习活动）
3. Mastery & Review（验收证据 / 已掌握 / 薄弱 / 今日到期 / 未来 7 天 / 逾期）
4. Capability（逐 Knowledge Point：Mastery 行 + Capability 行分离）
5. Curriculum（Phase `SACard` → Topic + 学习活动状态文本）
6. Skills
7. Curriculum Gap
8. Related Practice Projects
9. Practice Blockers（Topic / 当前能力 → 目标能力 / 下一步）

### Knowledge rows

由一行“名称+状态+Mastery+Capability”拆为两层：

```
<Knowledge Point>  <status>
Mastery：xx%
Capability：<label / 暂无能力证据>
```

右侧保留 actions：查看证据 / 记录实验成果（显示条件不变）。

### Learning activity tags

不再用 `✓ ○ ◇ ✔ ★ ☆`。改用纯文本状态：
`理论 · 已完成` / `实验 · 必需` / `面试 · 可选`。

## 不显示内部 ID

普通 UI 不显示 `route_id / topic_id / knowledge_point_id / component_id`；
route key 仅作为小 metadata（`R1` / `R2` …）。

## 边界

Practice blocker 表示“项目需求正在驱动下一步学习”，不显示为错误/失败。
Route 的 pause / archive / priority / planning 语义与任何 service 计算均未改变。
