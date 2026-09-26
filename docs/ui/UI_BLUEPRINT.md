# Study-Agent — UI Blueprint (UI-0)

> 基线 commit: `a36e688`
> 本文件定义 **UI-1 → UI-5** 的目标方向与实现边界。UI-0 **不实现**，只定义。
> 技术栈不变：**PySide6 + Qt Widgets**，不迁移 QML。
> 边界不变：`ui → services → database`。UI 只调用 service，禁止本阶段改任何
> DB / Planner / Scheduler / Mastery / Capability / Practice / Prompt 语义。

---

## 1. Product Visual Positioning

**定位**：AI-powered Learning & Career Workbench（个人学习与职业发展工作台）。

**视觉方向**：Fluent 2 / Windows 11 desktop productivity。

**关键词**：professional · restrained · modern · medium information density ·
desktop productivity · Windows native feel。

**Do**
- 以语义色 token 驱动，明暗双主题。
- 紧凑但可呼吸的间距；卡片式内容分区。
- 系统级字体（Segoe UI Variable / Microsoft YaHei UI / Noto Sans CJK SC）。
- 图标使用 Fluent System Icons（Regular 未激活 / Filled 激活）。

**Don't**
- 网页后台 Dashboard 风（大色块 KPI、渐变横幅）
- Material Design 主视觉（浮动操作按钮、水波纹）
- 大面积渐变 / 玻璃拟态 / 过度阴影
- 大量圆角胶囊
- Emoji 作为正式 icon
- 炫技动画（spring / 3D / heavy blur）

**参考工程**（只参考，不引入 dependency）
- `ejacques11/pyside6-fluent-ui`：semantic tokens、Fluent 2 architecture、QSS 组织、
  navigation shell、theme system、Light/Dark、可复用 Qt widgets。
- Microsoft WinUI Gallery / Microsoft Fluent 2：视觉规范。
- `zhiyiYo/PyQt-Fluent-Widgets`：**仅控件视觉参考**，不引入（PyQt 体系，不可作为核心依赖）。

---

## 2. App Shell Blueprint

### 2.1 目标结构

```
Workspace
├── Navigation Sidebar        (左导航)
└── Content
    ├── Page Header           (标题 / 副标题 / 主操作)
    └── Page Body             (各页面内容)
```

### 2.2 导航替换

当前顶部横排按钮：`今日 / 学习路线 / 实践项目 / 月总结 / AI 设置`
→ 目标左导航 **Sidebar**：

```
Study Agent            ← 品牌区
Today                  ← 一级导航
Learning Routes
Practice
Monthly
──────────────
Settings               ← 底部独立放置
```

### 2.3 尺寸与状态

| 状态 | 宽度 | 内容 |
|---|---|---|
| Expanded | 220–240 px | icon + 文本 + active 指示条 |
| Collapsed | 56–64 px | 仅 icon（tooltip 显示名称） |

- **active item**：左侧 3–4px accent 指示条 + `surface_selected` 背景 + Filled icon + 文本加粗。
- **hover**：`surface_hover` 背景，无位移。
- **collapsed**：图标居中，隐藏文本；Settings 沉底。
- **Settings 放置**：与主导航之间用分隔线隔开，固定在底部。
- 折叠状态持久化到 `QSettings`（UI-2 决定实现）。

### 2.4 页面兼容现有导航 API 的约束

当前 `MainWindow` 暴露 `nav_today_btn` / `nav_routes_btn` / `nav_practice_btn` /
`nav_monthly_btn` / `nav_ai_btn` 与 `_switch_page` / `_switch_to_routes` /
`_switch_to_practice` / `_switch_to_ai_settings`。UI-2 引入 Sidebar 时：
- 保留这些方法签名与“不可用则 statusBar 提示”的行为（页面可选注入的降级逻辑）；
- `QStackedWidget` 索引语义（0=Today, 1=Monthly, 其它追加）在 UI-2 可重构，
  但需同步更新依赖 `stack` 的测试（如有）。

---

## 2.5 MainWindow Responsibility Audit（专项）

`app/ui/main_window.py` = 1,856 LOC，是全 UI 最大技术债。**UI-0 不重写，只分类。**

### 1) 当前窗口整体结构
`QMainWindow` → central `QWidget` → `QVBoxLayout`：
`[顶部导航 QHBoxLayout]` + `[QStackedWidget]`；`QStackedWidget` 依序追加：
Today(0) → Monthly(可选) → Routes(可选) → Practice(可选) → AI Settings(可选)。
外加 `QStatusBar` 与 `QSystemTrayIcon`。

### 2) 当前 navigation 实现
5 个 `QPushButton`，全部 `objectName="PrimaryButton"`，点击直接 `lambda: self._switch_page(n)`
或专用方法。没有独立的 navigation 组件，按钮与业务方法同处一个类。

### 3) Today 页面是否混在 MainWindow
**完全混在内部**。Today 的标题、日期、区块标题、添加任务行、路线筛选、路线统计、
阶段信息、AI 规划状态、`QScrollArea` + 动态列表、空状态、技能概览、JD 趋势面板，
全部在 `_build_ui` 与 `refresh` 系列方法里，直接操作 `self.*`。没有 `TodayPage` 类。

### 4) 不应长期留在 MainWindow 的职责
- Today 页面全部渲染（标题/列表/空状态/职业面板）
- 路线筛选与统计
- 手动任务对话框编排
- 技能 / JD 面板渲染与候选处理
- 验收流程编排
- 规划状态展示与 replan
- `_switch_*` 导航

### 5) refresh / scroll / task rendering 的关系
`refresh(preserve_scroll)`：
1. 可选 `capture_today_view_state()`（只存 `verticalScrollBar().value()`）
2. `get_tasks_by_date` → `_today_tasks`
3. `_update_phase_info` / `_update_planner_info`
4. `_reload_route_filter` / `_selected_route_filter` / `_route_name_map`
5. `_clear_dynamic_list()`（销毁所有动态 widget，包含 stretch）
6. 按 status/type/route 过滤，重建「今日学习 / 职业面板」；历史 review task 不展示、不计数
7. 重新 `addStretch`
8. 计算 `empty_hint` / `scroll` 可见性
9. `restore_today_view_state()`：先 `clearFocus()`，再在
   `0/16/60/160/400/800 ms` 多次尝试恢复（clamp 到 maximum）

**必须保持行为不变**：过滤语义、cancelled 不进完成率分母、review 独立区块、
`addStretch` 顺序、scroll 的 6 次延迟恢复。

### 6) statusBar 使用
作为轻量 toast：`showMessage(msg, timeout)`。共约 20 处，覆盖添加任务、移除、延期、
replan、验收、JD、设置等反馈。UI-5 可替换为统一 `SAToast`，但消息文案与时限语义不变。

### 7) page switching
`_switch_page(index)` 保留“0=Today / 1=Monthly”的旧索引语义，越界/不可用时
`statusBar` 提示。`_switch_to_routes/practice/ai_settings` 在切换前调用对应 `page.refresh()`。
Sidebar 化时必须保留“不可用则提示且不切换”“切换前 refresh”的行为。

### 8) dialogs mounting
所有对话框都在 MainWindow 内 `import`（部分延迟）并 `dlg.exec()`；验收与 AI 复核使用
`QThread` worker，通过 `self._ai_workers` 持有引用，`finished` 时释放。

### 9) service dependency injection
`__init__` 接受 **30+ 个可选 service**，几乎全部 `if xxx is not None` 降级。
这是当前 UI 灵活性的来源，也是 MainWindow 臃肿的根因。UI-1 之后建议引入
`WorkspaceDependencies` 容器（dataclass）承接，但不改变 service 语义与降级行为。

### 10) styling responsibilities
`_apply_styles()` 把 `APP_STYLE` 设到 `QApplication`；Today 内联
`scroll.setStyleSheet("background: transparent;")`。MainWindow 不应承担主题管理，
应交给 `design/theme_manager.py`。

### 职责分类（用于拆分）

| 代号 | 职责 | 现状位置 | 未来归属 |
|---|---|---|---|
| **A** | App Shell | `_build_ui`(外壳)、`_build_tray`、`_apply_styles`、`run_app`、`closeEvent`、`_shutdown`、`_stop_ai_workers` | `MainWindow` / `AppShell` |
| **B** | Today Page | `_build_ui`(today 部分)、`refresh`、`_clear_dynamic_list`、`_add_section_*`、`_add_task_widget`、`_add_label`、`_add_skill_overview`、`_add_jd_*`、`_update_phase_info` | `TodayPage`（新建） |
| **C** | Navigation | 5 个 nav 按钮、`_switch_page`、`_switch_to_*` | `NavigationSidebar` + `NavigationController` |
| **D** | State / refresh | `current_date`、`_today_tasks`、`_task_widgets`、`_route_names`、`_route_filter_loading`、`TodayViewState`、`capture/restore_today_view_state`、`_reload_route_filter`、`_update_route_stats` | `TodayViewModel` / `TodayState`（纯状态，可测） |
| **E** | Dialog orchestration | 所有 `dlg = ...; dlg.exec()`、worker 创建/释放、`_on_*` 处理器 | `TodayController` / `RouteController` / `PracticeController` |
| **F** | Business interaction | `task_service.*`、`assessment_service.*`、`scheduler.generate`、`daily_planner_service.*`、`jd_summary_service.*`、`skill_service.*` | 控制器调用；**语义与调用顺序不得改变** |

**可安全抽出（UI-2/UI-3）**：B、C、D、E 的结构性搬迁。
**必须保持行为完全不变**：F 的每个 service 调用及其顺序、refresh 的过滤/滚动逻辑、
worker 生命周期、托盘与关闭语义、迁移 gate（不在 UI 层）。

---

## 3. Today Page Blueprint

### 3.1 定位
回答唯一问题：**“今天我应该做什么？”**

### 3.2 目标结构

```
PageHeader   Today · Sep 22 · Tuesday
             [添加学习任务]
Today Summary
  Tasks 3     Expected 120 min     Reviews 2
Focus Tasks
  ┌─────────────────────────────┐
  │ R4 AI Agent · THEORY        │
  │ Planning 任务规划            │
  │ Practice blocker            │
  │ Study-Agent                 │
  │ 45 min          [开始学习] ⋯ │
  └─────────────────────────────┘
Review / secondary section
Small contextual info
```

- `SATag` 表示 route / activity / source；不再用 `【…】` 文本前缀。
- `⋯` 为 `SAIconButton`（溢出菜单：移除今日任务 / 验收等）。
- 主操作（开始学习 / 完成）使用主按钮；次级与危险操作降级。

### 3.3 当前审计
- **Current Purpose**：今天待执行任务（新知识 + 复习）+ 职业面板。
- **Current Layout**：标题/日期 → 区块标题 → 添加行(路线筛选) → 统计 → 阶段 → AI 规划状态 → 滚动卡片列表 → 空状态；列表内还会追加技能概览与 JD 趋势面板。
- **Primary User Action**：完成 / 开始学习。
- **Secondary Actions**：未完成（填原因）、延期、移除、开始验收、添加任务、重新规划、路线筛选。
- **Displayed Information**：任务标题/分类/优先级/来源/路线/活动/描述/预计时间/原因/延期警告；统计；阶段；技能三态；JD 趋势/候选/缺口。
- **Problems**：
  1. Today 与“职业情报面板（技能/JD/课程缺口）”混在一起，与“今天做什么”不相关。
  2. 页面无 Header 层级，`AppTitle` + `SectionTitle` 语义混乱。
  3. 7 种过滤/排序维度挤在一屏。
  4. 添加任务按钮与路线筛选同排，主次不清。
- **Information Hierarchy Problems**：任务卡信息未分层（meta/tag/desc/action 同权重）；日期与标题无主次；统计只有完成率。
- **Visual Problems**：标签全部 `ReviewTag` 同色；卡片无 hover；按钮数量多且都是方框。
- **Interaction Problems**：完成/未完成立即重建整页（会闪）；无 loading；无 toast 统一层；溢出操作未收纳。
- **Candidate Components**：`SAPageHeader`、`SAStatCard`、`SATag`、`SACard`、`SAIconButton`、`SAButton`、`SAEmptyState`、`SAInfoBanner`。
- **Must NOT Change Semantically**：过滤规则、cancelled 不计入分母、review 独立区块、完成≠掌握、移除≠删除、延期计数、scroll 恢复。

---

## 4. Learning Routes Blueprint

### 4.1 定位
Route Dashboard + Route Detail。路线是「长期学习结构」，不是任务列表。

### 4.2 Route Overview
每张 `SACard` 至少展示（**Mastery ≠ Capability**）：

| 字段 | 来源 | 说明 |
|---|---|---|
| Route name | `route.name` | 标题 |
| Current Phase | `route_plan_service` / `_current_phase_name` | 当前阶段名 |
| Current Topic | plan structure | 当前主题 |
| Next Activity | activity profile | 下一步学习活动 |
| Progress | `route_progress_service` | 课程覆盖 done/total |
| Mastery | `progress_service` | **独立指标**：验收/掌握率 |
| Capability | `capability_service` | **独立指标**：真实证据等级 |
| planning state | `route.planning_enabled` | 自动规划 启/停 |
| priority | `route.priority` | 用户可控优先级 |

状态：`active` / `paused`（planning 停）/ `archived`。
分组路线（`ROUTE_TYPE_GROUP`）单独渲染为 group card。

### 4.3 Route Detail
层级：**Route → Phase → Topic → Activity → Task**。
- 顶部：目标 / 描述 / priority / planning / status + 动作（添加阶段、AI 生成计划、暂停/恢复、归档）。
- 进度区：课程覆盖 / 已验收 / 已掌握 / 待复习 / 薄弱。
- Knowledge 行：`Mastery：xx%` 与 `Capability：xxx` **分列**，不得合并为同一进度条。
- 复习状态：今日到期 / 未来 7 天 / 逾期 / 最近复习。
- Phase 卡片：phase → topic → activity chips。
- 关联：技能、课程缺口、关联项目、Practice blocker。

### 4.4 当前审计
- **Problems**：`RouteDetailDialog.refresh()` 单体方法承载 10+ 区块；全部用裸 `QLabel` + `TaskMeta`，无卡片分组；`_secondary` 工厂与 practice 重复；能力与掌握同行文本、易混。
- **Visual**：所有详情文本同字号同色；缺少 section 分隔。
- **Candidate Components**：`SAPageHeader`、`SACard`、`SASectionHeader`、`SAProgressBar`（progress）、`SAStatusBadge`（active/paused/archived）、`SATag`、`SAButton`。
- **Must NOT Change Semantically**：route_key 稳定、system/user-owned 分离、单 active plan、Mastery/Capability 独立、archive 不自动恢复。

---

## 5. Practice Blueprint

### 5.1 定位
Project / Practice Portfolio Workspace（真实成果组合）。

### 5.2 Project Card 字段（**四项必须分开，不得合并成一个 percentage**）

| 维度 | 含义 | 组件 |
|---|---|---|
| **Readiness** | 学习准备度（项目相关 topic 的能力要求是否满足） | `SAProgressBar` + 文本 `satisfied/total` |
| **Milestone Progress** | 里程碑完成情况 | `SAProgressBar`（milestone） |
| **Outputs** | 产出物数量/类型 | `SATag` 列表 / 计数 |
| **Capability Evidence** | 真实项目使用证据（PROJECT） | `SAStatusBadge` + 计数 |

其它：project title、status、related routes、skills、topics。

状态：`planned` / `in_progress` / `completed` / `archived`。

### 5.3 当前审计
- **Problems**：`_project_card` 只展示 name/status/... 少量信息；详情 dialog 7 个 section 用裸文本堆叠，Readiness / Milestone / Output / Evidence 视觉上无区分；`_FILTERS` 与卡片 status 文案各自维护。
- **Candidate Components**：`SAPageHeader`、`SAFilterChips`/`SASegmentedControl`、`SACard`、`SAStatCard`、`SASectionHeader`、`SAProgressBar`、`SAStatusBadge`、`SAEmptyState`、`SAFormField`。
- **Must NOT Change Semantically**：evidence 冻结字段、撤销用 `is_active=0`、Level 5 只由显式项目使用证据产生、Practice 不驱动 Scheduler、readiness 只在 in_progress 时驱动 Planner。

---

## 6. Monthly Blueprint

### 6.1 定位
轻量复盘，**不做 BI 大屏**。

### 6.2 目标区块
1. Route activity（per-route：完成任务 / 课程覆盖 / 验收 / 掌握 / 复习 / 薄弱）
2. Learning time / tasks（总任务、完成、完成率、延期、预计/实际分钟、学习天数、连续）
3. Mastery progression
4. Capability progression
5. Practice progress（本月新增 PROJECT 证据计数 + Topic 名）
6. AI monthly insight（`ai_summary` JSON；不可用则显示本地统计）

### 6.3 数据可得性（现有 Service vs NOT AVAILABLE YET）

| 指标 | 现状 | 来源 |
|---|---|---|
| 总任务 / 完成 / 完成率 / 延期 | ✅ 已有 | `stats_service` |
| 预计 / 实际分钟 / 学习天数 / 连续 | ✅ 已有 | `stats_service` |
| 分类排行榜 / best / worst / most invested / most postponed | ✅ 已有 | `stats_service` |
| Route 维度（done/covered/assessment/mastered/review/weak） | ✅ 已有 | `stats_service` route_stats |
| 本月项目能力证据计数 | ✅ 已有 | `practice_capability_service.list_active_created` |
| AI 解读 | ✅ 已有（可缺） | `summary_service.ai_summary` |
| **Mastery 月度“变化量/趋势”** | ❌ **NOT AVAILABLE YET** | 现有无月度快照/差分 API |
| **Capability 月度“等级提升趋势”** | ❌ **NOT AVAILABLE YET** | 现有只有当前等级，无历史序列 |
| **Practice milestone 月度趋势** | ❌ **NOT AVAILABLE YET** | 无时间序列 API |
| **跨月对比 / 环比** | ❌ **NOT AVAILABLE YET** | 无对比 API |

> 规则：**UI-0 不新增任何业务 API**。上表标记 NOT AVAILABLE YET 的指标，UI-4
> 只能显示占位（`SAEmptyState` / “暂不可用”），不得在 UI 层自行计算业务值。

### 6.4 当前审计
- **Problems**：统计以 8 行纯文本 `TaskMeta` 呈现，无卡片/无视觉层级；AI 解读需滚到最底部；`_add_pair` 用 `setFixedWidth(120)`。
- **Candidate Components**：`SAPageHeader`（月份选择）、`SAStatCard`、`SASectionHeader`、`SAProgressBar`、`SAEmptyState`、`SATag`。
- **Must NOT Change Semantically**：统计口径、AI 总结 JSON 字段、只计数不平均 Capability。

---

## 7. Settings Blueprint

### 7.1 目标结构

```
Settings
├── Appearance      (Light / Dark / System)
├── Model & API     (AI profiles / provider / model / base URL / secret status / connection test)
└── Prompt Manager  (prompt list / editor / variables / preview / reset override)
```

当前是 `QTabWidget` 两 tab（模型/API、Prompt 管理）。目标拆三个 section，Appearance 为新增。

### 7.2 分组职责
- **Appearance**：主题选择；写入 theme_manager + QSettings；即时切换。
- **Model & API**：Profile 列表 + 详情；`●/○` active 标记改为 `SAStatusBadge` / Filled icon；Key 只显示状态，不回显；连接测试后台线程与结果内联。
- **Prompt Manager**：tree + editor + variables + preview + reset。**Prompt 编辑器与最终 Prompt 预览使用 monospace**；其它普通 UI 不大量 monospace。

### 7.3 当前审计
- **Problems**：emoji `👁` 作为密码显隐图标；`●/○` 文本标记 active；无 Appearance；`QTabWidget` 原生外观；editor 无 mono。
- **Candidate Components**：`SAPageHeader`、`SASegmentedControl`（tab）、`SAListItem`、`SAFormField`、`SAButton`、`SAStatusBadge`、`SAInfoBanner`、`SAIconButton`。
- **Must NOT Change Semantically**：Key 只写 keyring、绝不回显；Prompt override 立即生效；变量校验失败不写库；预览用已保存模板。

---

## 8. Design System（目标目录与职责）

**UI-0 只定义，不创建 production implementation。**

```
app/ui/design/
    tokens.py          # 汇总导出；theme-neutral token 名 → 值
    colors.py          # semantic color roles（含 light/dark 两套取值）
    typography.py      # font family / size / weight / line-height roles
    spacing.py         # 4/8/12/16/20/24/32/40/48
    radius.py          # small / medium / large
    shadows.py         # 少量层级（见 §8.4）
    icons.py           # Fluent icon 名 → QIcon（未激活 Regular / 激活 Filled）
    theme_manager.py   # 当前 theme、切换、QSS 生成/加载、信号通知
styles/
    light.qss          # 由 token 生成或手写；只含选择器，不含 magic hex
    dark.qss
```

| 模块 | 职责 | 不负责 |
|---|---|---|
| `tokens.py` | 唯一 token 命名来源 | 具体 hex（交给 colors.py） |
| `colors.py` | role → 颜色，按 light/dark | 控件样式 |
| `typography.py` | role → `QFont`/QSS 片段 | 控件 |
| `spacing.py` | role → int | 布局逻辑 |
| `radius.py` | role → int | 控件 |
| `shadows.py` | `QGraphicsDropShadowEffect` 工厂，少量层级 | 色彩 |
| `icons.py` | 图标查找与着色 | 主题状态 |
| `theme_manager.py` | 应用/切换主题、发信号、注入 QSS | 业务 |
| `styles/*.qss` | 选择器样式，引用 token 展开值 | 组件结构 |

---

## 9. Semantic Tokens Proposal

### 9.1 Color（role taxonomy）

```
background
surface / surface_alt / surface_hover / surface_pressed / surface_selected
text_primary / text_secondary / text_disabled / text_on_accent
border / border_subtle
accent / accent_hover / accent_pressed
success / warning / danger / info
（overlay / scrim，用于 dialog）
```

Fluent 2 语义优先。**UI-0 不决定大量 hex**；light/dark 的具体值在 UI-1 由
Fluent 2 规范取值后填入 `colors.py`。

### 9.2 Typography

```
display · title_large · title · subtitle · body · body_secondary · caption · monospace
```

（对应现状：22 / 17 / 15 / 14 / 13 / 12 + 新增 display 与 mono。）

### 9.3 Spacing

```
4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48
```

### 9.4 Radius

```
small (≈4–6) · medium (≈8) · large (≈12)
```

### 9.5 Shadow

只保留少数层级，例如 `shadow_1`（卡片微浮起）、`shadow_2`（弹层）。
禁止多层模糊叠加。

---

## 10. Reusable Components Proposal

目标目录 `app/ui/components/`。**UI-0 不实现全部。**

| Component | 职责 | Variants | 使用页面 |
|---|---|---|---|
| `SAButton` | 统一按钮（含 palette 兜底） | primary / secondary / danger / ghost | 全部 |
| `SAIconButton` | 图标按钮 | normal / subtle | Today、Sidebar、Settings |
| `SACard` | 卡片容器（圆角/边框/hover） | default / interactive | Today、Routes、Practice |
| `SAStatCard` | 指标卡（label + value + 可选 delta） | default / compact | Today、Monthly |
| `SATag` | 标签（route/activity/source/status） | neutral / accent / success / warning / danger | Today、Routes、Practice |
| `SAProgressBar` | 进度条（单一指标） | default / success / warning | Routes、Practice |
| `SAProgressRing` | 环形进度（掌握率等） | small / medium | Routes、Monthly |
| `SASectionHeader` | 区块标题 + 可选 action | default | 全部 |
| `SAEmptyState` | 空状态（icon + title + hint + action） | default / compact | 全部 |
| `SAInfoBanner` | 行内提示 | info / warning / danger / success | Settings、Today |
| `SASearchBox` | 搜索输入 | default | Routes、Practice |
| `SASegmentedControl` | 分段切换（tab/filter） | default | Settings、Practice |
| `SADialog` | 对话框基类（标题/内容/按钮槽） | info / form | 全部 dialog |
| `SAFormField` | label + control + error 行 | text / password / textarea | dialog、Settings |
| `SAListItem` | 列表项（icon + title + subtitle + trailing） | default / selectable | Settings、Sidebar |
| `SAStatusBadge` | 状态徽标 | active / paused / archived / done / blocked | Routes、Practice |

约定：组件只关心视觉与交互，不调用 service。

---

## 11. Icon Strategy

### 11.1 当前审计

| Current icon | Location | Purpose | Future Fluent icon |
|---|---|---|---|
| `👁` | `ai_settings_dialogs.PasswordLineEdit` | 密码显隐 | `Eye` / `EyeOff`（Regular） |
| `●` / `○` | `ai_settings_page` profile 列表 | active 标记 | `Filled` pin / `SAStatusBadge` + `CheckmarkCircle` |
| `✓` | `manual_task_dialog`、`practice_page`、`routes_page` | 满足 / 完成 | `CheckmarkCircle` |
| `⚠` | `practice_page` | 能力缺口 | `Warning` |
| `★` / `☆` | `route_dialogs` | 优先级 | `StarFilled` / `Star`（Filled/Regular） |
| `✔` | `routes_page` activity chip | 完成活动 | `Checkmark` |
| `◇` | `routes_page` activity chip | 可选活动 | `Circle` / `Diamond` |
| `↑` / `↓` | `topic_learning_dialog` | 排序 | `ArrowUp` / `ArrowDown` |
| 手绘“学” | `main_window._tray_icon` | 托盘/窗口图标 | 品牌 SVG（自有） |
| `< 上一月` / `下一月 >` | `summary_pages` | 月份导航 | `ChevronLeft` / `ChevronRight` |
| `＋` 前缀 | 多处按钮文本 | 新建 | `Add` |
| `⋯`（规划中） | Today blueprint | 溢出菜单 | `MoreHorizontal` |
| Unicode 全角空格 `　` | 多处文本排版 | 伪列对齐 | 布局对齐（不用字符间距） |

### 11.2 目标
- 统一使用 **Microsoft Fluent System Icons**。
- **Regular = 未激活**；**Filled = 激活 / 选中**。
- 图标通过 `design/icons.py` 提供 `QIcon`，按当前主题着色（`text_primary` / `text_secondary` / `accent`）。
- **UI-0 不下载任何 icon 文件**，只给 mapping proposal。

### 11.3 状态与主题
Filled 只用于表达状态（选中/完成/激活），不用于装饰；同一上下文不混用两种风格。

---

## 12. State Strategy

### 12.1 需要的状态
Loading / Empty / Error / Disabled / Success / Warning / API unavailable /
No route / No task / No project / No AI profile / No assessment / No monthly data。

### 12.2 当前支持 vs 缺失

| 状态 | 当前覆盖 | 缺失 |
|---|---|---|
| Loading | 验收有 statusBar “正在生成验收题”、AI 复核 dialog 有 loading_label、连接测试有 worker | 列表级 skeleton 无 |
| Empty | Today `EmptyHint`、Routes “暂无已归档”、Practice “暂无实践项目”、Monthly “暂无…数据” | 无 icon/action 的统一空状态 |
| Error | `QErrorMessage`/红字、多数 service 异常被 `except` 吞成空 | 无统一 error banner；无 retry |
| Disabled | `setEnabled(False)`（页不可用/按钮） | 无 disabled 视觉规范 |
| Success | statusBar 文案 | 无成功 banner/toast 组件 |
| Warning | 延期警告、缺能力 | 无统一 warning 组件 |
| API unavailable | Today “AI 不可用”、验收“AI 未配置” | 分散在文本 |
| No route / No task / No project / No AI profile | 有零散提示 | 不统一 |
| No assessment | “暂无验收数据” | 文本 |
| No monthly data | “暂无…数据” | 文本 |

### 12.3 未来组件
`SAEmptyState`、`SAInfoBanner`、`SASkeleton`（可选）、`SALoadingOverlay`（可选）、`SAToast`（UI-5）。
每个页面/列表必须显式处理：loading / empty / error / disabled。

---

## 13. Theme Strategy

- `theme_manager.py` 管理 `Light / Dark / System` 三态；System 使用
  `QStyleHints.colorScheme()`（Qt 6.5+）或回退 Light。
- 主题切换：重新生成/加载 QSS + 更新 palette + 广播信号，所有 `SA*` 组件响应。
- QSS 中不得出现 magic hex；必须由 token 展开。
- 现有 `APP_STYLE` 在 UI-2 之后退出历史舞台（保留兼容期）。
- 必须保证：深浅两套下 `text_primary` / `text_secondary` / `border` /
  status 色都满足对比度；语义色不随主题反转含义。

---

## 14. DPI / Responsive Strategy

### 14.1 高风险（当前硬编码）

| 位置 | 风险 |
|---|---|
| `MainWindow.setMinimumSize(560,460)` / `resize(800,650)` | 150% DPI 下内容拥挤；窗口偏小 |
| 所有 dialog `resize(W,H)` | 固定尺寸，高 DPI 或大字体时截断 |
| `setFixedHeight(70/60/80/90/56/…)*` | 多语言/字体缩放时文字截断 |
| `summary_pages` `setFixedWidth(120)` | 长 key 或大字体溢出 |
| `ai_settings_dialogs` `setFixedWidth(40)`、`topic_learning_dialog` `setFixedWidth(30)` / `setMinimumWidth(80)` | ICON/排序按钮在缩放时失配 |
| 顶部 5 个按钮横排 | 窗口变窄时换行/拥挤（将改左侧栏解决） |
| QSS px padding/font-size | Qt 的 px 在高 DPI 下按 devicePixelRatio 缩放，但固定字号无法随系统字体设置变化 |

### 14.2 目标
- 优先使用布局 stretch / sizeHint / `QSizePolicy`，避免 fixed px。
- 图标按钮用 `setIconSize` + padding，不用 fixed width。
- 在 Windows 上至少验证 **100% / 125% / 150% / 175%** 与 1440×900 / 1920×1080。
- UI-5 引入统一缩放策略（字体 role 而非固定 px 字号）。

---

## 15. Accessibility Strategy

### 15.1 当前
- 键盘导航：Qt 默认 tab order 存在，但无显式 `setTabOrder`；卡片内按钮可 tab。
- focus indicator：依赖系统/QSS，`QPushButton` 无 `:focus` 样式；输入框有 `:focus` border。
- tooltip：少量（Sidebar 折叠时需要新增）。
- `accessibleName` / `accessibleDescription`：**全 UI 未设置**。
- contrast：`#95a5a6`(EmptyHint) / `#b0b7bf`(disabled) 在白底对比不足。
- color-only meaning：延误黄、完成绿、错误红仅靠颜色；标签文字有辅助。
- disabled：仅文字变灰。

### 15.2 优先级
**P0**
- 折叠 Sidebar 的 icon-only 项必须有 `accessibleName` + tooltip。
- 所有 icon-only `SAIconButton` 必须 `accessibleName`。
- focus indicator 在深浅主题都可辨识（`:focus` outline）。

**P1**
- 表单 `SAFormField` label 与 control 建立 `setBuddy`。
- error 文本不只靠颜色（前置 icon/文字）。
- 关键操作按钮 `accessibleName`。

**P2**
- 完整 `setTabOrder` 审计。
- 对比度按 WCAG AA 全面复核。
- 屏幕阅读器描述补全。

**UI-0 不实现。**

---

## 16. Animation Proposal

**允许**（100–200ms）：hover、sidebar collapse、page transition、toast、progress。
**禁止**：spring、3D、heavy blur、large motion。

现状：scroll restore 在 `0 / 16 / 60 / 160 / 400 / 800 ms` 多次重试。
**本阶段不修改**；UI-5 可评估收敛为更少的重试，但必须先保证滚动不跳。

---

## 17. UI Phase Roadmap（固定）

```
UI-0  Audit + Blueprint                ← done (b32bee2)
UI-1  Design Tokens / ThemeManager / Icon system / Foundation Components  ← done (bdec9a0)
UI-2  App Shell / Sidebar / Page Header / Light-Dark-System  ← done (21b4092, 详见 APP_SHELL.md)
UI-3  Today / Learning Routes  ← done (TODAY_DESIGN.md / ROUTES_DESIGN.md)
UI-3.1 Route capability & detail semantics  ← done (e4ac592)
UI-4  Practice / Monthly / Settings  ← done (PRACTICE_DESIGN.md / MONTHLY_DESIGN.md / SETTINGS_DESIGN.md)
UI-5  States / Micro-interactions / DPI / Accessibility / Polish  ← done (UI5_AUDIT.md / FINAL_UI_ACCEPTANCE.md)
```

### UI-1 首批建议实现（详细）
1. `design/colors.py` + `typography.py` + `spacing.py` + `radius.py` + `shadows.py` + `tokens.py`（Fluent 2 light/dark 取值）。
2. `design/theme_manager.py`：注册/切换/信号 + QSS 注入；先只做 Light，Dark 占位。
3. `design/icons.py`：Fluent icon 名 → QIcon（可先内置少量必要图标，不引入外部 dependency）。
4. `styles/light.qss` / `dark.qss`：从 token 展开，替换 `APP_STYLE` 中的 magic hex。
5. Foundation 组件：`SAButton`（**把 `apply_secondary_button_text` 收进组件**）、`SACard`、`SASectionHeader`、`SAStyledLabel`/`SATag`、`SAEmptyState`。
6. 兼容层：保留 `objectName="SecondaryButton"` 等测试依赖，直到组件化测试同步更新。

---

## 18. Issue List（阻塞与优先级）

### P0 — 会阻塞 UI 重构
1. **MainWindow 单体 1,856 LOC**：Today 页面、导航、业务编排、职业面板全糅在一起，不做结构性拆分无法安全引入 App Shell / Sidebar。
2. **无 token / 无主题层**：QSS 全是 magic hex，无法实现 Light/Dark；必须先在 UI-1 建 token，否则所有页面改造都会再次硬编码。
3. **`objectName` 是唯一样式契约，且被 31 个测试文件依赖**：样式与结构重构前必须确定兼容策略，否则 UI 改动会大面积破坏测试。
4. **`apply_secondary_button_text` 散布 31 个调用点**：不改会造成主题切换时颜色不同步；必须在 UI-1 收敛进 `SAButton`。

### P1 — UI-1/UI-2 必须解决
5. 顶部横排导航 → 左 Sidebar，含 active/hover/collapsed 状态与可用性降级。
6. 页面缺统一 `PageHeader`，标题层级（AppTitle/SectionTitle）混用。
7. 状态体系缺失：无统一 loading / empty / error / disabled / success 组件。
8. 错误色 18 处内联 + `QErrorMessage` 误用 → 统一 `SAInfoBanner`/error text 组件。
9. 原生控件（ComboBox/Tab/List/Tree/Splitter/ScrollBar）未样式化，视觉不统一。
10. Today 页面信息架构：职业面板与“今天做什么”混排，需重排 hierarchy。
11. Routes/Practice 详情 dialog 单方法堆叠 7–10 个 section，需组件化。
12. Mastery / Capability / Readiness / Milestone / Output / Evidence 视觉未分离，存在被用户误解为同一进度的风险。

### P2 — 后续 polish
13. 死样式 `StatsBar` / `StatsTitle` / `StatsValue`。
14. emoji/Unicode icon 全量替换为 Fluent icons。
15. 固定像素尺寸（dialog / fixedHeight / fixedWidth）清理，适配 DPI。
16. accessibility：accessibleName / focus indicator / setBuddy / tab order。
17. 统一 toast 替代散落 statusBar（UI-5）。
18. monospace 仅用于 Prompt editor / JSON preview。
19. Monthly 缺失指标的 NOT AVAILABLE YET 占位设计。
20. 微交互与动效收敛（含 scroll restore 重试次数）。

---

## 19. 明确不做（UI-0 约束）

不改 DB schema / migration / Service 语义 / Planner / Scheduler / Capability /
Mastery / Practice evidence / Prompt semantics；不引入 `pyside6-fluent-ui` 或
`PyQt-Fluent-Widgets`；不大规模拆 `main_window.py`；不改 scroll restore 行为；
不修改用户数据；不为 UI audit 跑 full pytest。
