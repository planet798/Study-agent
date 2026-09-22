# Study-Agent App Shell (UI-2)

> Fluent 2 左侧导航 + Workspace（PageHeader + StackedWidget）+ runtime 主题切换。
> UI-2 只改外壳，不改各页面内部信息架构 / 业务逻辑。

## Architecture

```
MainWindow
└── AppShell (app/ui/app_shell.py)
    ├── SANavigationSidebar (app/ui/components/navigation.py)
    │   ├── Brand            [glyph] Study Agent
    │   ├── collapse toggle  SAIconButton(MENU)
    │   ├── Today / Learning Routes / Practice / Monthly
    │   ├── stretch
    │   └── divider + Settings   (footer)
    └── Workspace
        ├── SAPageHeader     (app/ui/components/page_header.py)
        └── QStackedWidget   (Today=0, Monthly=1, Routes, Practice, Settings)
```

`MainWindow` 不再创建顶部 5 个 `QPushButton`；`self.stack` 现在来自 `AppShell`。

## Sidebar anatomy

- 宽度：expanded **228px**，collapsed **60px**（`setFixedWidth`，不做任意 resize）。
- `SANavigationItem` 继承 `QPushButton` → 原生 click / text / isEnabled / checkable /
  Tab + Enter/Space。
- 选中：`surface_selected` + 左侧 3px accent indicator + **Filled** icon + 加粗文本。
- 未选中：**Regular** icon + `text_secondary`；hover `surface_hover`；pressed `surface_pressed`；
  disabled `text_disabled`。
- collapsed：只显示 icon（`setText("")`），每项保留 tooltip + `accessibleName`；
  brand 只显示 glyph；collapse 按钮 tooltip/accessibility 在“收起/展开侧栏”间切换。
- Settings 通过 stretch + divider 沉底，不与主导航混排。

## Page registry

`app/ui/app_shell.py`：

```python
class PageKey(str, Enum):
    TODAY / ROUTES / PRACTICE / MONTHLY / SETTINGS

@dataclass(frozen=True)
class PageSpec:
    key, title, subtitle, icon, footer=False

PAGE_SPECS: tuple[PageSpec, ...]   # 固定顺序
PAGE_SPECS_BY_KEY: dict[str, PageSpec]
```

| key | title | subtitle | icon |
|---|---|---|---|
| today | 今日 | 今天的学习计划（运行时替换为日期） | Home |
| routes | 学习路线 | 管理学习路线与能力进度 | Book |
| practice | 实践项目 | 项目与实践证据 | ClipboardTask |
| monthly | 月度回顾 | 学习复盘 | Calendar |
| settings | 设置 | 模型、Prompt 与外观设置 | Settings（footer） |

注意：`class PageKey(str, Enum)` 在 Python 3.11+ 下 `str(member)` 返回 `"PageKey.X"`；
统一用 `components.navigation.key_value()` 归一化，不要直接 `str(enum)`。

## PageHeader

`SAPageHeader`：title + 可选 subtitle + 可选 leading icon + 可选 trailing actions。
`AppShell.set_page_header(key)` 按 registry 更新；Today 的 subtitle 由运行时日期覆盖。

- Today 的 `date_label` **就是** PageHeader 的 subtitle QLabel
  （`page_header.subtitle_label()`）；日期 provider 逻辑不变。
- 各一级页面最外层重复标题已移除（Routes / Practice / Monthly / AI Settings），
  section 内部结构未动。

## Page switching

`sidebar.page_requested(str)` → `MainWindow._on_nav_requested(key)`：

- today → `_switch_page(0)`（Today）
- monthly → `_switch_page(1)`
- routes → `_switch_to_routes()`（先 `routes_page.refresh()`）
- practice → `_switch_to_practice()`（先 refresh）
- settings → `_switch_to_ai_settings()`（先 refresh）

不可用页面：对应 item `setEnabled(False)`，顺序不变；守卫逻辑保留 statusBar 降级提示。

## Theme preference

- 模块：`app/ui/design/theme_preferences.py`。
- 后端：**QSettings**（不是 DB），key `appearance/theme`，值 `system` / `light` / `dark`；
  默认 `system`。org/app = `StudyAgent` / `StudyAgent`。
- 启动顺序（避免闪主题）：

  ```
  load_theme_mode(QSettings) → ThemeManager.set_theme() → _apply_styles()/apply(app) → show
  ```

- Settings 页面顶部有最小“外观”section（`QComboBox`：跟随系统 / 浅色 / 深色），
  选择后写 QSettings + `ThemeManager.set_theme()`，立即生效。
- 测试使用隔离 `QSettings`（临时 ini）+ conftest autouse `isolated_qsettings`，
  **绝不写真实用户配置**。

## System theme

`ThemeManager` 在 `apply()` 时安装 `QStyleHints.colorSchemeChanged` 监听：
当 `current_mode == SYSTEM` 且系统深浅变化 → 自动重新 apply。Qt / 平台不支持时静默降级
（行为与旧版一致，只在 apply 时读取一次）。

`apply()` 幂等：QSS/palette 每次重渲染；`theme_changed` **只在 effective theme 真正变化时**
emit（重复 apply 不重复广播）。

## Accessibility

- 每个 navigation item：`accessibleName` = 标题；collapsed 时 tooltip = 标题。
- collapse 按钮：`accessibleName` + tooltip（收起/展开）。
- 键盘：Tab 可进入 nav item，Enter/Space 激活；focus 有 `:focus` 边框。
- selected 不只靠颜色：accent indicator + Filled icon + 加粗文本 多重线索。

## Compatibility

- 旧 `nav_today_btn / nav_routes_btn / nav_practice_btn / nav_monthly_btn / nav_ai_btn`
  保留为 **Sidebar item 别名**；`nav_layout` = `sidebar.items_layout`。
- 旧 `_switch_page / _switch_to_routes / _switch_to_practice / _switch_to_ai_settings`
  与 `*_page_index` 语义保留（Today=0、Monthly=1，其余追加）。
- 生产 UI **只有一套可见导航**（左侧 Sidebar），不存在旧顶部导航。
- 标签变化（已同步更新测试）：`月总结 → 月度回顾`、`AI 设置 → 设置`。
