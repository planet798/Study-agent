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

### Route-level Capability = Evidence Coverage / Distribution（不做聚合）

`RouteProgressService` 明确**不计算 route average capability**。因此 Route Card 只展示：

- `capability_evidence_count`（有证据的知识点数量）；
- `capability_level_counts`（各 level 的计数分布）。

```
能力证据 Capability   [4 个知识点]  [L2 × 1]  [L3 × 2]  [L4 × 1]
```

没有证据时显示“暂无能力证据”。**禁止**取最高/最低/平均/中位数/第一个 KP，
**禁止**把某个 KP 的 capability 当作整条 route 的 capability，**禁止**造出
“Route 当前是 L3”这类业务模型不存在的事实。

## Route Status

`SAStatusBadge`：`active` / `paused`（暂停自动规划，Review 仍继续）/
`archived`（不再产生新学习与新 Review）。文案不模糊这两者。

## Route Detail（仍为 Dialog）

保持 `RouteDetailDialog`（降低行为风险），内部按 section 组织：

1. Overview `SACard`（目标 / 描述 / Priority / Planning State / Route Status /
   Current Phase / Current Topic）——不再用单个多行 `info_label`。
2. Learning Progress `SACard`（课程覆盖 `SAProgressBar`；学习活动 必需/可选）
   ——不再用 `【路线进度】` / `【学习活动】` 前缀。
3. Mastery & Review `SACard`（Mastery bar 或“暂无验收数据”；已验收 / 已掌握 /
   薄弱 / 今日到期 / 未来7天 / 逾期 / 最近复习）——不再用 `【掌握】`。
4. Capability `SACard`（evidence count + level distribution；下面逐 Knowledge Point
   Mastery 行 + Capability 行分离）
5. Curriculum（Phase `SACard` → Topic + 学习活动状态文本）
6. Skills
7. Curriculum Gap
8. Related Practice Projects
9. Practice Blockers（Topic / 当前能力 → 目标能力 / 下一步）

### Knowledge rows

由一行“名称+状态+Mastery+Capability”拆为两层：

```
<Knowledge Point>  <status>
Mastery：xx% / 暂无验收
Capability：Lx · <capability_label> / 暂无能力证据
弱点：…（如有）
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
