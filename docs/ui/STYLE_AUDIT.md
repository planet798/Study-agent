# Study-Agent — Style System Audit (UI-0)

> 基线 commit: `a36e688`
> 审查对象: `app/ui/styles.py`（192 LOC，含单一 `APP_STYLE`）以及全 `app/ui/` 中的
> `setStyleSheet` / `setObjectName` / 内联颜色 / 尺寸写法。
> 结论先行：**当前不存在 Design System，只有一份单体 QSS + 约定俗成的 `objectName`。**

---

## 1. 现状

`app/ui/styles.py` 只导出三样东西：

| 导出 | 类型 | 说明 |
|---|---|---|
| `SECONDARY_TEXT_COLOR = "#2c6fbb"` | 常量 | 供 palette workaround 使用（`#2c6fbb` 同时也是 QSS 主色） |
| `APP_STYLE` | `str` | 全应用唯一 QSS，注入到 `QApplication.setStyleSheet` |
| `apply_secondary_button_text(button)` | 函数 | Windows 原生按钮文字色 workaround |

注入点唯一：`MainWindow._apply_styles()` → `app.setStyleSheet(APP_STYLE)`。
没有 `theme_manager`、没有 light/dark 切换、没有动态 palette。

QSS 覆盖的控件：
`QWidget`、`QMainWindow`、`QDialog`、`QFrame#TaskCard`、`QPushButton`、
`QTextEdit`、`QLineEdit`、`QErrorMessage`、`QStatusBar`。

**完全没有样式化的控件**（走系统原生外观）：
`QComboBox`、`QScrollArea`（除 MainWindow 内联透明背景）、`QTabWidget` /
`QTabBar`、`QGroupBox`、`QListWidget`、`QTreeWidget`、`QSplitter`、`QCheckBox`、
`QRadioButton`、`QMenu`、`QToolTip`、`QPlainTextEdit`、`QFormLayout` 标签、
`QProgressBar`、滚动条 `QScrollBar`。

---

## 2. `setObjectName` selector 清单（当前事实上的 token）

全 `app/ui/` 共 **18 种** `objectName`（按出现次数）：

| objectName | 次数 | 用途 | QSS 是否定义 |
|---|---|---|---|
| `TaskMeta` | 87 | 次级/辅助文本（事实上的 `body_secondary`） | ✅ |
| `SectionTitle` | 31 | 区块标题 | ✅ |
| `SecondaryButton` | 31 | 次级按钮 | ✅ + palette workaround |
| `PrimaryButton` | 9 | 主按钮 | ✅ |
| `EmptyHint` | 7 | 空状态文案 | ✅ |
| `TaskTitle` | 6 | 卡片/条目标题 | ✅ |
| `TaskCard` | 6 | 卡片容器 | ✅（含 `[done]` / `[postponing]` 属性） |
| `ReviewTag` | 5 | 标签（复习/来源/路线/活动复用同一 name） | ✅ |
| `AppTitle` | 5 | 页面主标题 | ✅ |
| `QErrorMessage` | 3 | 错误文本 | ⚠️ 用 Qt 内置类名当 objectName |
| `PostponeButton` | 3 | 延期按钮 | ✅ |
| `PostponeWarning` | 2 | 延期警告 | ✅ |
| `DangerButton` | 2 | 危险按钮 | ✅ |
| `TaskReason` | 1 | 未完成原因 | ✅ |
| `TaskDesc` | 1 | 卡片描述 | ✅ |
| `DoneBadge` | 1 | 已完成徽标 | ✅ |
| `AppDate` | 1 | 页面日期 | ✅ |
| `StatsBar` / `StatsTitle` / `StatsValue` | 0* | QSS 定义了但代码中已无引用 | ✅（**死样式**） |

\* `StatsBar` / `StatsTitle` / `StatsValue` 在 `styles.py` 有规则，但全 `app/ui/`
已无 `setObjectName` 使用——历史遗留死代码。

### 2.1 Historical Review tag retired
Daily Review / Review Scheduler / Daily Retention 已退出生产 UI。TaskWidget 只展示路线、学习活动与任务来源标签；历史 review rows 不由 Today 渲染。

### 2.2 `QErrorMessage` 不是 `objectName`
`QErrorMessage` 在 Qt 中是**控件类名**。把它当 `objectName` 使用，导致该规则实际
同时命中了所有真正的 `QErrorMessage` 实例和标了该 name 的 `QLabel`——语义混乱，属于 workaround。

---

## 3. 颜色重复（全 `app/ui/` 硬编码 hex）

| 颜色 | 出现 | 语义（实际用途） | 应归属 token |
|---|---|---|---|
| `#e74c3c` | 18 | 错误红字（多数在 `setStyleSheet` 内联） | `danger` |
| `#2c6fbb` | 9 | 主色 / 链接 / 次级按钮 / 强调 | `accent` |
| `#ffffff` | 3 | 卡片/输入背景 | `surface` |
| `#eef2f6` | 3 | 按钮底 / 状态栏底 | `surface_alt` |
| `#d5dbe2` | 3 | 输入/按钮边框 | `border` |
| `#b7791f` | 3 | 延期警告 | `warning` |
| `#1f3a5f` | 3 | 标题深蓝 | `text_primary`（偏蓝） |
| `#e0e4e8` | 2 | 卡片边框 | `border_subtle` |
| `#b0b7bf` | 2 | disabled 文字 | `text_disabled` |
| `#7f8c8d` | 2 | 次级文字 | `text_secondary` |
| `#5d6d7e` | 2 | 卡片描述 | `text_secondary` |
| 其它 16 个单次色 | 16 | hover / pressed / done 底 / warning 底 / 危险 hover 底 / 选中背景 / 托盘蓝 … | 见下表 |

**全 UI 共约 27 个不同 hex**，且没有命名。浅色主题下尚可，但无法做 dark，
也无法保证 contrast。

内联 `setStyleSheet("color: #e74c3c;")` 出现 **18 次**（`route_dialogs`×4、
`practice_dialogs`×4、`dialogs`×2、`manual_task_dialog`×1、`capability_dialog`×1、
`assessment_dialog`×1、`past_task_dialog`×1、`career_dialogs`×1，其余在 `styles.py`）。
这是最典型的“散布式修复”：同一个错误文本样式在 10 个文件里各写一遍。

---

## 4. 间距 / 圆角 / 字号 / padding 重复

### 4.1 Radius
| 值 | 用处 | 建议 token |
|---|---|---|
| `8px` | `QFrame#TaskCard`、`StatsBar` | `radius.medium` |
| `6px` | `QPushButton`、`QTextEdit`/`QLineEdit` | `radius.small` |
| `14`（代码绘制） | 托盘图标圆角矩形 | icon 内部 |

无 `large` 等更高层级。

### 4.2 Font-size
| 值 | 用处 | 建议 token |
|---|---|---|
| `22px` | `#AppTitle` | `title_large` |
| `17px` | `#SectionTitle` | `title` |
| `15px` | `#TaskTitle`、`#EmptyHint` | `subtitle` / `body` |
| `14px` | `QWidget` 全局、`StatsTitle`/`StatsValue` | `body` |
| `13px` | `#AppDate`、`#TaskDesc` | `body_secondary` |
| `12px` | `#TaskMeta`、`#ReviewTag`、`#TaskReason`、`#PostponeWarning` | `caption` |

### 4.3 Padding / spacing
- QSS: `padding: 6px 14px`（按钮）、`padding: 6px`（输入）、`padding: 30px`（空状态）、`padding-top: 8px`（区块标题）。
- 布局代码内的 magic number（不统一）：
  - 页面 root: `setContentsMargins(12,8,12,8)`（main）、`(4,4,4,4)`（routes/practice/monthly）、`(4,0,4,0)`（settings）
  - 卡片: `setContentsMargins(14,10,14,10)`（TaskWidget / route card / project card，重复 3 处）
  - 间距: `root.setSpacing(8)` / `(10)` / `(6)` / `(3)` / `(4)` / `(2)` 混用
- 这些值散落在每个 `_build_ui()`，没有共享常量 → 全页面间距节奏不一致。

---

## 5. 页面私有 style / workaround 清单

| 位置 | 写法 | 问题 |
|---|---|---|
| `main_window.py:313` | `self.scroll.setStyleSheet("background: transparent;")` | 唯一页面级内联 QSS，为了去掉 QScrollArea viewport 底色 |
| 10 个文件 | `self.error_label.setStyleSheet("color: #e74c3c;")` | 重复 18 次 |
| `styles.py:apply_secondary_button_text` | 改 `QPalette.ButtonText` | Windows 原生样式 workaround（见 §6） |
| `main_window._tray_icon()` | `QPainter` 手绘圆角蓝底“学”字 | 唯一“图标”，硬编码 `#2c6fbb` / radius 14 / 64px |
| `TaskWidget` | `setProperty("done"/"postponing")` + `unpolish/polish` | 用 Qt property selector 做状态色，机制正确但只此一处 |
| `ai_settings_dialogs.py:35` | `toggle.setFixedWidth(40)` | 眼睛按钮固定宽 |
| `summary_pages.py:79` | `k.setFixedWidth(120)` | 统计 key 列固定宽 |
| `topic_learning_dialog.py` | `setMinimumWidth(80)` / `setFixedWidth(30)` | 排序按钮固定宽 |
| 多个 dialog | `desc_edit.setFixedHeight(70)` 等 | 固定高度，DPI 敏感 |

---

## 6. `apply_secondary_button_text()` 专项说明

### 为什么必须存在
Windows 原生 QPushButton 的样式引擎（`windowsvista` / `windows11` style）会**忽略**
QSS 中 `QPushButton#SecondaryButton { color: … }` 的文字颜色，继续使用系统
`ButtonText` palette，导致蓝框按钮出现白字/系统色字，可读性不达标。Qt 官方文档也
承认原生 style 对 `color` 的支持受限。

当前实现：

```python
blue = QColor(SECONDARY_TEXT_COLOR)          # 硬编码 #2c6fbb
pal = button.palette()
pal.setColor(QPalette.ColorGroup.Active,   QPalette.ColorRole.ButtonText, blue)
pal.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.ButtonText, blue)
button.setPalette(pal)
```

它在 **31 处** `SecondaryButton` 创建点被逐一调用（`TaskWidget`、`main_window`、
`routes_page`、`practice_page`、`past_task_dialog`、`career_dialogs`、
`manual_task_dialog`、`route_dialogs`、`route_builder_dialogs`、`ai_settings_page`），
外加 `project`/`routes` 页面各自的 `_secondary()` 工厂里也包了一层。

### 问题
这是**在 31 个调用点分别打补丁**，而不是让“次级按钮”这个组件自己负责颜色。
一旦引入 dark theme，`#2c6fbb` 必须随主题变化，但 palette 的硬编码颜色不会更新。

### Design System 应如何消除
在 UI-1 的 `SAButton` 组件内部**只实现一次**：
- `SAButton(variant="secondary")` 在构造时根据当前 theme 设置 `QPalette.ButtonText`（active/inactive/disabled）；
- theme 切换时通过主题信号重新 apply palette；
- 业务代码不再 import `apply_secondary_button_text`，也不再调用它。

也就是说：**workaround 本身保留机制（palette 兜底），但把它从“调用点约定”收敛为“组件内部实现”。**

---

## 7. 其它 workaround

| Workaround | 位置 | 未来处理 |
|---|---|---|
| `scroll` 透明背景内联 QSS | `main_window` | 归入 `SAScrollArea` / 主题 QSS |
| `unpolish/polish` 刷新 property 样式 | `task_widget.render` | 由组件封装，调用方不再手动触发 |
| 用 `objectName` 当 token，靠字符串约定 | 全 UI | UI-1 改为 `property`/class-based selector 或直接 Qt widget 类 |
| `QErrorMessage` 当 objectName | `main_window`、`routes_page` | 改 `SAErrorText` 组件 |
| 递归 `_clear_layout()` 在各页面各写一份 | `practice_page`、`routes_page`、`main_window` | 提取为公共 util / `SACardList` |
| 多处 `_secondary()` 工厂重复 | `practice_page`、`routes_page`、`route_dialogs` 等 | `SAButton` |
| 卡片 `setContentsMargins(14,10,14,10)` 复制 3 次 | 多处 | `SACard` 内部常量 |
| 手绘托盘图标 | `main_window._tray_icon` | `design/icons.py`（Fluent System Icons 导出或矢量绘制） |

---

## 8. 测试对样式的耦合（重构约束）

`tests/` 中有 31 个文件引用 UI 内部符号，样式相关的硬依赖：

- `objectName() == "SecondaryButton"`（3 处）
- `objectName() == "DangerButton"`（1 处）
- `objectName() == ""`（1 处，断言某控件无 name）
- `findChild(QPlainTextEdit)`、`findChild(QTabWidget)`、`findChild(...)` 依赖控件类型
- `window.list_container`、`page.list_container`、`page.profile_panel`、
  `page.prompt_panel`、`dlg.postpone_btn`、`dlg.error_label`、`dlg._answer_edits`
  等widget 属性名被测试直接访问

**结论**：UI-1/UI-2 重构时：
1. 可以替换 QSS 与内部结构，但 `SecondaryButton` / `DangerButton` / `PrimaryButton`
   这几个 `objectName`、以及被测试访问的公开属性名应保留兼容层（或同步更新测试，但那是
   独立、可审计的改动）。
2. 不能为了视觉重构顺手改名 `list_container` / `_task_widgets` 等测试依赖点。

---

## 9. Style Audit 结论

1. **没有 token 层**：颜色、字号、间距、圆角全部硬编码或靠 `objectName` 约定。
2. **没有主题层**：单一浅色，无法 dark / system。
3. **错误色散布 18 处**，是最大重复源。
4. **31 处 palette workaround**：机制正确但位置错误。
5. **约 27 个 hex、18 个 objectName、6 级字号、2 级圆角、多套间距**并存，无命名。
6. **原生控件未样式化**（ComboBox / Tab / List / Tree / Splitter / ScrollBar），
   视觉风格与任务卡片不统一。
7. 存在死样式（`StatsBar`/`StatsTitle`/`StatsValue`）。
8. 测试对 `objectName` 与 widget 属性有硬依赖，重构需保留兼容。
