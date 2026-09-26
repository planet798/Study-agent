# Study-Agent — Final UI Acceptance (Windows)

在 Windows 实机执行：

```
.venv\Scripts\python.exe app\main.py
```

逐项记录 PASS / FAIL / NOTE。

## A. Shell
- Sidebar 主导航仅 Today / 学习路线 / 实践项目，设置固定在 footer。
- Sidebar expanded(228) / collapsed(60) 切换；collapsed 有 tooltip+accessibleName。
- Page Header 显示页面标题与副标题。

## B. Today
- Summary（待处理/预计时长）；当前阶段与 Planner 状态；今日学习任务、手动添加与学习空状态。JD/Skill/Market 仅作为 Planner 后台信号，不在 Today 展示。
- 任务卡标签（路线/活动/来源）、操作层级、Done≠Mastery、延期 ≥3 警告。
- Empty：「今天还没有学习任务」+ 添加。

## C. Learning Routes
- R1–R6 一眼可扫；分组 heading。
- 课程进度条；**Mastery 百分比条**；**Capability evidence count + Lx distribution**（非百分比）。
- Route Detail：概览 / 学习进度 / 掌握情况 / 能力证据（知识状态逐行）等 Card。

## D. Practice
- Project 卡：status badge / type tag / route tag；里程碑 / 成果 / 项目能力证据 / 学习准备度**分离**；无统一 project %。
- Detail：概览 / 项目范围 / 学习准备 / 里程碑 / 项目成果 / 项目能力证据。
- Readiness 只为 requirements ratio；能力缺口非红色；L5 只来自 active confirmed evidence。

## F. Settings
- 无 AI service 仍可切换主题（Appearance）。
- Model/API：profile 无 ●○；Key 绝不回显；连接测试不阻塞。
- Prompt：editor monospace；状态 tag；save/preview/reset。

## G. Dialogs
- Add Task / Assessment / Route Detail / Practice Detail / AI profile / Prompt preview：
  margins / spacing / button 层级一致；确认在右、取消在左；focus 可见。

## H. Light / Dark / System
- Settings 切换 Light→Dark→System→Light 实时生效，无需重启。
- 已打开 Dialog 新开后继承主题；无白底黑字/黑底黑字割裂。

## I. DPI（100% / 125% / 150% / 175%）
- 无文本裁切；按钮高度正常；ComboBox/Tab/Scrollbar/Sidebar/Prompt editor 正常。
- 窗口尺寸：1180×760 / 1440×900 / 1920×1080；无横向撑爆。

## J. Keyboard
- Tab 可遍历 Sidebar / Today actions / Settings；Enter/Space 激活；Escape 关闭 dialog；focus ring 可见。

## README 推荐截图（4 张）
Today（light）· Learning Routes（light）· Practice（light）· Today（dark）。

## S1 — Daily Review retirement
- Daily Review / Review Scheduler / Daily Retention 已从生产产品中移除。
- Historical review rows/schema are retained for migration and history preservation only.
- Review-like recall will be handled by future Agent contextual learning, not by scheduled review tasks.
