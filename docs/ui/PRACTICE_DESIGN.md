# Practice Workspace Design (UI-4)

> Project Portfolio / Evidence Workspace。回答“在做哪些项目、做到什么程度、
> 产出什么、还缺哪些能力？”不是 project dashboard。

## Overview
- Controls：状态筛选 + `SAButton`（ADD icon）“新建项目”。
- Project Cards（`SACard(interactive)`）。
- `SAEmptyState`：“还没有实践项目 / 创建项目，把学习路线、技能和 Topic 转化为可验证成果。”

文案已修正（不再声称“项目不参与能力等级判定”）：
“实践项目用于沉淀真实成果与能力证据；学习准备度可帮助确定项目相关知识的下一学习步骤。”
secondary：“项目证据不会直接改变 Mastery；显式确认的项目使用证据可形成 PROJECT 级能力证据。”

## Project Card
`SAStatusBadge`（planned / in_progress / completed / archived，沿用现有 status semantics）
+ project type `SATag` + related route `SATag`（route 名，不显示数字 id）。
四类进度**必须分离，不合并为统一 project %**：
`里程碑：x / y`、`成果：N`、`项目能力证据：N`、`学习准备度：x / y 已满足`（有 readiness 时）。

## Detail（保留 PracticeProjectDetailDialog）
Cards：概览（状态/类型/目标/描述/里程碑/成果，`_value_pair`）→ 关联学习路线 →
关联技能 → 关联 Topic → 学习准备 → 里程碑 → 项目成果 → 项目能力证据。移除旧 `【】` 前缀。

- **Readiness**：`学习准备度 x / y 已满足`；`drives_planner` → “参与项目相关学习优先级”；
  否则“仅展示准备度，不驱动 Planner”。Readiness ≠ Capability ≠ Milestone。
- **Milestone**：`#order title` + 状态文案（待开始/进行中/已完成），推进/编辑/删除不变。
- **Output**：type `SATag` + title + uri；编辑/删除不变。
- **Capability Evidence**：active → “已确认项目使用证据” + outputs + usage；撤销历史 →
  中性文案“曾有已撤销证据”。只有 active confirmed evidence 支持 PROJECT / L5；
  关联 Topic / 完成项目 / 有 Output / Milestone 都不暗示 L5。确认项目使用（eligible 才 enabled）、
  查看证据、撤销证据行为与条件不变。

Scroll preservation 的 timer 未修改。
