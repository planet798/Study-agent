# Practice Workspace Design (UI-4)

> Project Portfolio / Evidence Workspace。回答“在做哪些项目、做到什么程度、
> 产出什么、还缺哪些能力？”不是 project dashboard。

## Overview（2026-10-04 精修）

- 状态筛选默认“全部未归档”，沿用未归档查询范围，不包含归档历史。
- 项目为轻量条目：名称 / 状态 / 查看项目 / 更多；类型与关联路线为次要信息，可换行。
- 四类指标独立：里程碑 x/y、成果数量、有效项目能力证据数量、学习准备度 x/y；禁止统一 project %。
- 编辑 / 归档 / 恢复进入菜单，沿用原服务与确认流程。
- 全库无项目 → 创建；当前状态无匹配 → 清除筛选；默认范围仅有归档项目 → 查看已归档；读取失败 → 重试。
- 四类指标读取失败独立显示“暂不可用”，未启用的模块显示“未启用”；不伪造零证据或零成果。
- 刷新与关闭详情保留概览滚动位置；切换筛选回到顶部。

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
