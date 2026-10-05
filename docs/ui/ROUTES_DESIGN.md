# Learning Routes Design (UI-3 · 2026-10 概览与详情精修)

> Routes 页面回答：**“各条路线进行到哪里、接下来学什么、掌握与能力分别如何？”**

## Overview（Route Dashboard）

- 分组（如“求职准备”）采用轻量标题与数量摘要。
- 每条路线采用中性分隔线条目，常驻“查看路线”和“更多”菜单。

### Route Overview 信息层级

名称 / route key / 状态 / 查看路线 / 更多 → 当前阶段 → 当前知识点 → 下一步。
课程进度保留进度条；掌握度为百分比或“暂无验收数据”；能力证据只显示有证据的知识点数量。
等级分布保留在详情；优先级是次要 metadata。调整 / 暂停或恢复规划 / 归档进入菜单。

无路线时显示集中空态及创建入口。无计划、课程已完成、读取暂不可用有独立文案。
刷新及关闭详情后恢复概览滚动位置，手动滚动会取消旧恢复请求。

## Mastery vs Capability（关键）

- **Mastery** 是连续度量 → 概览显示百分比，详情使用 `SAProgressBar`；无验收数据显示“暂无验收数据”。
- **Capability** 是离散等级 → `SATag` + 现有 service 提供的 label；
  **绝不画成百分比**，**绝不与 Mastery 共用同一进度条**。

### Route-level Capability = Evidence Coverage / Distribution（不做聚合）

`RouteProgressService` 明确**不计算 route average capability**。因此详情只展示：

- `capability_evidence_count`（有证据的知识点数量）；
- `capability_level_counts`（各 level 的计数分布）。

```
能力证据 Capability   [4 个知识点]  [L2 × 1]  [L3 × 2]  [L4 × 1]
```

没有证据时显示“暂无能力证据”。**禁止**取最高/最低/平均/中位数/第一个 KP，
**禁止**把某个 KP 的 capability 当作整条 route 的 capability，**禁止**造出
“Route 当前是 L3”这类业务模型不存在的事实。

## Route Status

`SAStatusBadge`：`active` / `paused`（暂停自动规划）/
`archived`（不再产生新学习任务）。

## Route Detail（仍为 Dialog）

保留 `RouteDetailDialog` 的构造与打开方式。初始 880×720 逻辑像素，受可用屏幕尺寸限制。
正文使用轻量分区，不套叠封闭卡片：

1. 顶部摘要：目标 / 状态 / 当前阶段 / 当前知识点 / 下一步 / 课程覆盖进度条 /
   掌握度与能力证据数量。所有数值沿用现有服务，不统一或改写原有计算口径。
2. 路线信息（默认折叠）：目标 / 描述 / 优先级 / 自动规划 / 状态。
3. 课程结构：必需与可选学习活动统计；阶段标题显示完成数量，知识点显示名称 /
   预计时长 / 活动状态。第一个有未完成知识点的阶段默认展开，其余折叠；全部完成时默认折叠。
4. 掌握与能力（默认折叠）：Mastery bar / 验收与薄弱统计 / 最近验收 /
   Capability evidence count 与 L1–L5 distribution / 每个知识点的独立 Mastery、Capability。
5. 关联技能 / 课程缺口 / 关联实践项目 / 项目学习需求（各自默认折叠，标题显示数量）。

顶部常驻添加阶段；AI 生成、暂停/恢复规划、归档进入更多菜单。
阶段添加知识点仍有直接入口，删除阶段与知识点移入菜单；学习组成仍可从知识点菜单打开。
无计划时保留路线资料，仅在集中空态中显示手动创建与 AI 生成，避免重复入口。
两个创建入口居中纵向排列，使用原生垂直布局计算完整高度，保证短窗口滚动后按钮仍完整可见。
读取失败独立显示“暂不可用”，不冒充无数据或零进度。

刷新期间保留本次打开的折叠选择与滚动位置，不持久化新的偏好。
用户拖动、滚轮、滚动按键或折叠分区会取消待执行的位置恢复；关闭或删除弹窗也会使其失效。
AI worker 的退出等待、结果失效保护及已有服务确认流程保持。

实现职责：`routes_page.py` 组织查询与服务操作；`route_overview_widgets.py` 和
`route_detail_sections.py` 只显示快照、发出操作信号；`route_scroll.py` 管理恢复生命周期。

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

## 验收场景（2026-10 精修）

- 概览查看 / 暂停 / 恢复 / 归档及恢复菜单，保留路线隔离与历史。
- 当前阶段展开、其余折叠；刷新保留用户选择；全部完成不宣称已掌握。
- 折叠后重新展开仍可编辑学习组成、删除无历史知识点，已有历史仍拒绝删除。
- 无计划创建入口只有一组；无路线、无知识点、无验收、读取失败分别展示。
- 概览与详情刷新滚动恢复；新恢复覆盖旧恢复，用户拖动与关闭取消恢复。
- 深浅主题、长名称、窄弹窗、多阶段；Windows DPI 100%/125%/150%/175% 需实机复核。
