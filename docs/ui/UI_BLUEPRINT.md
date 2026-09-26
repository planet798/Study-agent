# Study-Agent — UI Blueprint (UI-0)

> Historical UI-0 design audit at commit `a36e688` (not the canonical current product; see `docs/PRODUCT_BASELINE.md`).
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

Current navigation: Today / Learning Routes / Practice; Settings stays in the footer.
→ 目标左导航 **Sidebar**：

```
Study Agent            ← 品牌区
Today                  ← 一级导航
Learning Routes
Practice
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
`nav_ai_btn` 与 `_switch_to_today` / `_switch_to_routes` /
`_switch_to_practice` / `_switch_to_ai_settings`。UI-2 引入 Sidebar 时：
- 保留现存导航的兼容语义；
- `QStackedWidget` 只包含当前启用的 Today / Routes / Practice / Settings 页面，
  但需同步更新依赖 `stack` 的测试（如有）。

---

## 2.5 Historical UI-0 MainWindow audit

The original five-button top navigation and Career/Review/Monthly pages described in UI-0 are retired. Current shell uses Sidebar Today / Learning Routes / Practice / Settings (footer). `TodayPage` owns the layout; `MainWindow` orchestrates services and task actions. This archived blueprint is not a source of new product requirements: see `docs/PRODUCT_BASELINE.md`.

## 3. Today Page Blueprint

### 3.1 定位
回答唯一问题：**“今天我应该做什么？”**

### 3.2 当前结构（S6 冻结）

```text
PageHeader             Today · 日期
Today Summary          待处理 · 预计时长
Route Filter           路线筛选 · 当日任务状态
Current Phase          当前阶段 / 阶段目标
Planner State          规划状态 / 说明 / 重新规划
今日学习                学习任务卡片
添加学习任务            学习活动 / 知识学习
```

没有 Review / Monthly / Career dashboard。Mastery 只由 Assessment 更新；Skill/JD/Market 数据保留为 Planner 后台信号。手动学习不是 Generic Todo（见 `docs/PRODUCT_BASELINE.md`）。

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
- 进度区：课程覆盖 / 掌握度 / 已验收 / 已掌握 / 薄弱 / 最近验收。
- Knowledge 行：`Mastery：xx%` 与 `Capability：xxx` **分列**，不得合并为同一进度条。
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

## 6. Monthly blueprint retired (S2)

Monthly is not a current page or feature. The legacy Monthly blueprint is intentionally omitted from the active design specification.

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
| `SAStatCard` | 指标卡（label + value + 可选 delta） | default / compact | Today |
| `SATag` | 标签（route/activity/source/status） | neutral / accent / success / warning / danger | Today、Routes、Practice |
| `SAProgressBar` | 进度条（单一指标） | default / success / warning | Routes、Practice |
| `SAProgressRing` | 环形进度（掌握率等） | small / medium | Routes |
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
No route / No task / No project / No AI profile / No assessment。

### 12.2 当前支持 vs 缺失

| 状态 | 当前覆盖 | 缺失 |
|---|---|---|
| Loading | 验收有 statusBar “正在生成验收题”、AI 复核 dialog 有 loading_label、连接测试有 worker | 列表级 skeleton 无 |
| Empty | Today `EmptyHint`、Routes “暂无已归档”、Practice “暂无实践项目” | 无 icon/action 的统一空状态 |
| Error | `QErrorMessage`/红字、多数 service 异常被 `except` 吞成空 | 无统一 error banner；无 retry |
| Disabled | `setEnabled(False)`（页不可用/按钮） | 无 disabled 视觉规范 |
| Success | statusBar 文案 | 无成功 banner/toast 组件 |
| Warning | 延期警告、缺能力 | 无统一 warning 组件 |
| API unavailable | Today “AI 不可用”、验收“AI 未配置” | 分散在文本 |
| No route / No task / No project / No AI profile | 有零散提示 | 不统一 |
| No assessment | “暂无验收数据” | 文本 |

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
| 历史顶部按钮横排 | 已由 Sidebar 取代（UI-2） |
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
UI-4  Practice / Settings  ← done (PRACTICE_DESIGN.md / SETTINGS_DESIGN.md)；Monthly 已由 S2 退役
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
1. **历史 UI-0 审计**：当时 MainWindow 含 Career 面板；S3 已移除，TodayPage/AppShell 已独立。
2. **无 token / 无主题层**：QSS 全是 magic hex，无法实现 Light/Dark；必须先在 UI-1 建 token，否则所有页面改造都会再次硬编码。
3. **`objectName` 是唯一样式契约，且被 31 个测试文件依赖**：样式与结构重构前必须确定兼容策略，否则 UI 改动会大面积破坏测试。
4. **`apply_secondary_button_text` 散布 31 个调用点**：不改会造成主题切换时颜色不同步；必须在 UI-1 收敛进 `SAButton`。

### P1 — UI-1/UI-2 必须解决
5. 顶部横排导航 → 左 Sidebar，含 active/hover/collapsed 状态与可用性降级。
6. 页面缺统一 `PageHeader`，标题层级（AppTitle/SectionTitle）混用。
7. 状态体系缺失：无统一 loading / empty / error / disabled / success 组件。
8. 错误色 18 处内联 + `QErrorMessage` 误用 → 统一 `SAInfoBanner`/error text 组件。
9. 原生控件（ComboBox/Tab/List/Tree/Splitter/ScrollBar）未样式化，视觉不统一。
10. （S3 已完成）Today 页面仅呈现学习执行内容，JD / Skill / Market 留作 Planner 信号。
11. Routes/Practice 详情 dialog 单方法堆叠 7–10 个 section，需组件化。
12. Mastery / Capability / Readiness / Milestone / Output / Evidence 视觉未分离，存在被用户误解为同一进度的风险。

### P2 — 后续 polish
13. 死样式 `StatsBar` / `StatsTitle` / `StatsValue`。
14. emoji/Unicode icon 全量替换为 Fluent icons。
15. 固定像素尺寸（dialog / fixedHeight / fixedWidth）清理，适配 DPI。
16. accessibility：accessibleName / focus indicator / setBuddy / tab order。
17. 统一 toast 替代散落 statusBar（UI-5）。
18. monospace 仅用于 Prompt editor / JSON preview。
19. （历史规划项）Monthly 指标占位设计，S2 后不实施。
20. 微交互与动效收敛（含 scroll restore 重试次数）。

---

## 19. 明确不做（UI-0 约束）

不改 DB schema / migration / Service 语义 / Planner / Scheduler / Capability /
Mastery / Practice evidence / Prompt semantics；不引入 `pyside6-fluent-ui` 或
`PyQt-Fluent-Widgets`；不大规模拆 `main_window.py`；不改 scroll restore 行为；
不修改用户数据；不为 UI audit 跑 full pytest。
