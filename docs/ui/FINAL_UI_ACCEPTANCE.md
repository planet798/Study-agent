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
- R1–R6 轻量条目；分组标题与数量；当前阶段 / 知识点 / 下一步清晰可扫。
- 课程进度条；Mastery 百分比摘要或暂无验收；Capability 有证据知识点数量。等级分布在详情，非百分比。
- 查看路线常驻；更多菜单可调整 / 暂停或恢复规划 / 归档，历史与归档限制不变。
- 详情：顶部摘要 → 折叠路线信息 → 课程结构 → 折叠掌握与能力 → 辅助分区。
- 当前阶段默认展开；刷新保留折叠选择与滚动；手动滚动后不被旧恢复请求拉回。
- 无计划创建入口不重复；读取失败不显示为零；全部课程完成不显示为已掌握。
- 窄弹窗 / 长名称 / 深浅主题 / 高 DPI 无横向溢出；菜单与折叠按钮键盘可达。

## D. Practice
- 默认筛选“全部未归档”；轻量项目条目，名称 / 状态 / 查看项目 / 更多，长路线名称换行。
- 里程碑 / 成果 / 项目能力证据 / 学习准备度分离，无统一 project %；读取失败不冒充零。
- 首次空态创建入口唯一；筛选无结果可清除；仅归档项目可直接查看归档；失败可重试。
- Detail：顶部摘要 / 折叠项目信息 / 展开里程碑与成果 / 折叠学习准备、证据与项目关联。
- 里程碑按钮明确“开始”“标记完成”；重置在菜单。成果地址可选择复制。
- 确认 / 查看 / 撤销证据与资格原因齐全，只有 active confirmed evidence 支持 PROJECT / L5。
- 有历史引用的成果删除与关键字段编辑仍受保护；用户看到错误后可继续使用界面。
- 刷新不跳位、切换筛选回顶部、折叠状态保持、用户滚动取消旧恢复、关闭无延迟操作。
- 长名称 / 多路线 / 多成果 / 深浅主题 / 高 DPI / 键盘菜单及折叠无裁切。

## F. Settings
- 三标签页保持，外观独立于模型服务；窄窗口列表/详情纵向，操作自动换行。
- Agent 说明及各 Prompt 未保存内容切换/刷新/离开返回不丢失；保存失败保留草稿。
- 保存成功显示读回结果，放弃修改读取已保存版本，恢复默认沿用原确认。
- 草稿无自动落盘，API Key 不进入缓存；预览明确使用已保存版本，不采用草稿。
- 模型配置的当前 / 无配置 / 不可用 / 处理中 / 成功 / 失败状态明确；次级操作菜单保持原资格。
- Key 不回显，登录与连接测试保持异步，重复测试与关闭后的迟到回调受到保护。

## G. 高频 Dialogs
- 模型配置、修改 Key、重命名、Prompt 预览、记忆管理/编辑、手动学习、未完成原因、学习验收：
  margins / spacing / button 层级一致；确认在右、取消在左；focus 可见。
- 长正文可滚动；操作及错误反馈保持可达；字段值、模态行为和校验规则保持。
- Key 默认遮挡，显示/隐藏按钮支持键盘与可访问名称。
- 验收结果在同一正文区域，完成后可见；关闭正在判题的弹窗无 QThread 退出异常。
- 路线与实践页维持已验收结果；低频编辑/证据弹窗后续单独评估。

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
