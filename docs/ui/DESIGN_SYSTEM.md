# Study-Agent Design System (UI-1)

> Fluent 2 foundation：semantic tokens + theme + QSS + icons + foundation components。
> UI-1 只做基础层；页面 redesign 属于 UI-2+。业务语义零改动。

## 目录

```
app/ui/design/
    colors.py         # Light / Dark semantic color tokens
    typography.py     # role → QFont
    spacing.py        # 固定 spacing scale
    radius.py         # SMALL / MEDIUM / LARGE / PILL
    shadows.py        # NONE / LOW / MEDIUM（默认不启用）
    tokens.py         # 汇总 render map
    icons.py          # Fluent icon registry + 单色 tint
    theme_manager.py  # ThemeMode + apply + QSS render
    styles/light.qss  # QSS template（placeholder）
    styles/dark.qss
    icons/*.svg       # 最小 Fluent icon 集（MIT，见 icons/LICENSE）
    theme_preferences.py  # QSettings 主题偏好（appearance/theme）
app/ui/components/
    button.py         # SAButton / SAIconButton
    card.py           # SACard
    section_header.py # SASectionHeader
    tag.py            # SATag
    status_badge.py   # SAStatusBadge
    empty_state.py    # SAEmptyState
    navigation.py     # SANavigationItem / SANavigationSidebar (UI-2)
    page_header.py    # SAPageHeader (UI-2)
app/ui/app_shell.py   # AppShell + PageKey / PageSpec registry (UI-2)
```

## Color tokens

业务只使用 semantic name，不写 hex。Light 品牌 accent 保留 `#2c6fbb`。

| 组 | tokens |
|---|---|
| surface | `background` `surface` `surface_alt` `surface_hover` `surface_pressed` `surface_selected` |
| text | `text_primary` `text_secondary` `text_tertiary` `text_disabled` `text_on_accent` |
| border | `border` `border_subtle` `border_strong` |
| accent | `accent` `accent_hover` `accent_pressed` `accent_disabled` |
| status | `success` `success_background` `warning` `warning_background` `danger` `danger_background` `info` `info_background` |
| misc | `focus` `overlay` |

Dark 是独立设计的一套 neutral/accent，不是 Light 反色；status 语义两主题一致。
Light/Dark key 完全一致（有测试保证）。

## Typography

`DISPLAY · TITLE_LARGE · TITLE · SUBTITLE · BODY · BODY_SECONDARY · CAPTION · MONOSPACE`

- `font_for(role) -> QFont`（组件内应用；不是所有字号都靠 QSS px）。
- 无衬线：`Segoe UI Variable → Segoe UI → Microsoft YaHei UI → Noto Sans CJK SC`。
- 等宽：`Cascadia Mono → Consolas`（仅 Prompt editor / JSON preview 等）。
- 不打包 / 提交字体文件。

## Spacing / Radius / Shadow

- spacing：`XS=4 SM=8 MD=12 LG=16 XL=20 XXL=24 XXXL=32 SECTION=40 PAGE=48`。
- radius：`SMALL=4 MEDIUM=8 LARGE=12 PILL=999`（pill 仅 Tag / Badge）。
- shadow：`NONE / LOW / MEDIUM`；**SACard 默认不启用阴影**，只用 border。

## ThemeManager

```python
from app.ui.design.theme_manager import ThemeManager, ThemeMode

tm = ThemeManager.instance()
tm.apply(qapp)                 # 应用 current_mode（默认 Light）
tm.set_theme(ThemeMode.DARK)   # 已 apply 过则立即重渲染
tm.tokens()                    # 当前 effective theme 的 token map
tm.theme_changed.connect(slot)  # slot(effective_theme: str)
```

- `ThemeMode`: `LIGHT / DARK / SYSTEM`。SYSTEM 读取 `QStyleHints.colorScheme()`，
  不可用时 fallback Light。
- `apply()` 幂等：重复 apply 不累积 QSS / palette 状态；Light→Dark→Light 回到完全相同的 QSS。
- `theme_changed` **只在 effective theme 真正变化时** emit；重复 apply 同一主题不会广播。
- QSS 渲染结果按主题做 `lru_cache`，避免每个窗口构造都重新解析模板。
- UI-1 无 Settings Appearance 页面、无 DB persistence；仅 runtime。
  UI-2 起：主题偏好用 QSettings（`appearance/theme`，见 `theme_preferences.py`），
  仍不写 DB。

## App Shell QSS（UI-2）

Additional selectors（全 semantic token，无 magic hex）：
`QWidget#SANavigationSidebar`、`QPushButton#SANavigationItem`（`:hover/:pressed/:checked/:focus/:disabled`、`[collapsed="true"]`）、
`QFrame#SADivider`、`QWidget#SAPageHeader`、`QFrame#SAPageHeaderDivider`、
`QLabel#SAPageTitle`、`QLabel#SAPageSubtitle`、`QLabel#SABrandTitle`。
纯 `QWidget` 需要 `WA_StyledBackground` 才会绘制 QSS 背景（Sidebar / PageHeader / Workspace 已设置）。

## QSS rendering

Qt QSS 没有 CSS variables，因此：

```
semantic tokens → ThemeManager.render_qss(template, map) → setStyleSheet
```

- 模板使用 `{ { token } }` 双花括号 placeholder，`render_qss` 用正则做安全替换
  （**不用 `str.format`**，避免与 QSS `{}` 冲突）。
- 未知 placeholder 抛 `KeyError`；替换后仍残留 `{ {` 抛 `ValueError`。
- `light.qss` / `dark.qss` 必须有相同的 placeholder 集合（parity 测试）。
- QSS 覆盖：QWidget/QMainWindow/QDialog、QPushButton、QLineEdit/TextEdit/PlainTextEdit、
  QComboBox、QCheckBox/QRadioButton、QTabWidget/QTabBar、QListWidget/QTreeWidget、
  QScrollArea/QScrollBar、QMenu、QToolTip、QStatusBar、QProgressBar，以及语义 label 与组件。
- Focus：所有交互控件有统一 `:focus` 边框（`{{focus}}`），Light/Dark 均可辨认。

## Component variants

| Component | API |
|---|---|
| `SAButton` | `variant`: primary / secondary / subtle / danger；`size`: small / medium |
| `SAIconButton` | `icon_name` + variant subtle/danger；强制 tooltip + accessibleName；不固定死宽 |
| `SACard` | variant: default / interactive / selected；默认无阴影 |
| `SASectionHeader` | title + 可选 subtitle + 可选 trailing |
| `SATag` | neutral / accent / success / warning / danger / info（metadata） |
| `SAStatusBadge` | active / paused / archived / planned / in_progress / completed / warning |
| `SAEmptyState` | 可选 icon + title + description + 可选 action |

`SAButton` 内部处理 Windows `ButtonText` palette 兜底（secondary/primary/danger），
**新组件不得再调用 `apply_secondary_button_text`**；主题切换时组件自动重着色。

## Icon rules

- 统一 Fluent System Icons：**Regular = 未激活 / Filled = 激活**。
- 单色可 tint：同一 SVG 资产按 semantic foreground 着色，**没有 light/dark 两套资产**。
- `icon(name, size, color, filled)` / `pixmap(...)`；`IconName` 枚举声明可用集合。
- 不用 emoji / Unicode symbol / Qt StandardPixmap。
- 只 vendoring 最小 SVG 集 + MIT attribution（`design/icons/LICENSE`）。

## Legacy compatibility

- `styles.py::APP_STYLE` 仍在，且**代理到 Light theme 渲染结果**；
  legacy objectName selectors 继续生效（`PrimaryButton` `SecondaryButton` `DangerButton`
  `TaskCard` `TaskTitle` `TaskMeta` `SectionTitle` `EmptyHint` `ReviewTag` ...）。
- `styles.py::apply_secondary_button_text` 仍在，委托给 `SAButton` 内部的同一实现；
  旧调用点不动，UI-3/UI-4 渐进迁移后再删除。
- `MainWindow._apply_styles()` 改为 `ThemeManager.instance().apply(app)`；
  布局 / 导航 / 信号 / scroll / service 调用完全不变。

## Do / Don't

**Do**
- 用 semantic token；用组件；用 `font_for(role)`；用 spacing scale。
- 错误文本用 `QLabel[role="danger"]` 或 `SAInfoBanner`（后续）。

**Don't**
- 不在业务文件写 `setStyleSheet("color: #xxxxxx")`。
- 不在 light.qss / dark.qss 写死 hex。
- 不做 pill 泛滥、不默认加阴影、不引入第三方 UI 框架依赖。
- 不改业务 Service / DB / Planner / Scheduler / Mastery / Capability / Practice / Prompt 语义。
