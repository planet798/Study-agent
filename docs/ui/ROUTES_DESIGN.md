# Learning Routes Design (UI-3 · 2026-10 概览精修)

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

保持 `RouteDetailDialog`（降低行为风险），内部按 section 组织：

1. Overview `SACard`（目标 / 描述 / Priority / Planning State / Route Status /
   Current Phase / Current Topic）——不再用单个多行 `info_label`。
2. Learning Progress `SACard`（课程覆盖 `SAProgressBar`；学习活动 必需/可选）
   ——不再用 `【路线进度】` / `【学习活动】` 前缀。
3. Mastery `SACard`（掌握度 bar 或“暂无验收数据”；已验收 / 已掌握 /
   薄弱 / 最近验收）。不读取或显示任何 review schedule。
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
