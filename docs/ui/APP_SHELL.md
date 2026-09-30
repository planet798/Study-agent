# Study-Agent App Shell

> 当前静态主导航：Today / Learning Routes / Practice；Settings 固定在 Sidebar footer。
> Learning Shell-1 增加动态「学习会话」，打开内部 Agent Workspace；Monthly 已从产品中移除。

## Architecture

```text
MainWindow
└── AppShell
    ├── SANavigationSidebar
    │   ├── Today
    │   ├── Learning Routes
    │   ├── Practice
    │   ├── dynamic: 学习会话 (Session entries)
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
- 静态页面点击由 `sidebar.page_requested` 转发至 `MainWindow._on_nav_requested`。
- 动态会话点击由 `sidebar.session_requested` 转发至 `MainWindow._on_open_agent_session(session_id)`；active Session 独立于 origin Task 状态，最近 10 个按 updated_at 降序（必要时保留当前会话）。内部 Agent Workspace 不注册静态 PageKey/PageSpec；离开不关闭 Session，忙碌时不切换其它 Session。

## Page header and navigation

Today subtitle 由运行时日期覆盖；Routes、Practice、Settings 通过 page registry 设置标题。
Monthly 不再有 PageKey、PageSpec、navigation item 或 page switching branch。

## Theme and accessibility

主题偏好由 QSettings 保存（System / Light / Dark），启动时先读取偏好再应用主题。
导航项保留 accessibleName、tooltip 与键盘 focus；Settings 保持 footer 可达。
