# Study-Agent App Shell

> 当前主导航：Today / Learning Routes / Practice；Settings 固定在 Sidebar footer。
> Monthly 已从产品中移除。

## Architecture

```text
MainWindow
└── AppShell
    ├── SANavigationSidebar
    │   ├── Today
    │   ├── Learning Routes
    │   ├── Practice
    │   └── footer: Settings
    └── Workspace
        ├── SAPageHeader
        └── QStackedWidget
```

`PageKey` 与 `PAGE_SPECS` 只注册 Today、Routes、Practice、Settings。`MainWindow`
根据可用 service 添加 Routes / Practice 页面；Today 与 Settings 始终存在。

## Sidebar

- Expanded 228px / collapsed 60px。
- 选中态使用背景、accent indicator、Filled icon 与字重共同表达。
- Settings 通过 stretch + divider 固定在 footer。
- 点击事件由 `sidebar.page_requested` 转发至 `MainWindow._on_nav_requested`。

## Page header and navigation

Today subtitle 由运行时日期覆盖；Routes、Practice、Settings 通过 page registry 设置标题。
Monthly 不再有 PageKey、PageSpec、navigation item 或 page switching branch。

## Theme and accessibility

主题偏好由 QSettings 保存（System / Light / Dark），启动时先读取偏好再应用主题。
导航项保留 accessibleName、tooltip 与键盘 focus；Settings 保持 footer 可达。
