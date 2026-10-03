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
- 选中态使用柔和中性背景、Filled icon 与字重表达；不使用 accent 左侧条或蓝色图标叠加强调。
- Sidebar、会话列表与会话滚动区使用统一 surface，不使用嵌套的大色块分割。
- Settings 通过 stretch + divider 固定在 footer。
- 静态页面点击由 `sidebar.page_requested` 转发至 `MainWindow._on_nav_requested`。
- 动态会话点击由 `sidebar.session_requested` 转发至 `MainWindow._on_open_agent_session(session_id)`；active Session 独立于 origin Task 状态，最近 10 个按 updated_at 降序（必要时保留当前会话）。内部 Agent Workspace 不注册静态 PageKey/PageSpec；离开不关闭 Session，忙碌时不切换其它 Session。

## Page header and navigation

Today subtitle 由运行时日期覆盖；Routes、Practice、Settings 通过 page registry 设置标题。
Monthly 不再有 PageKey、PageSpec、navigation item 或 page switching branch。

## Theme and accessibility

主题偏好由 QSettings 保存（System / Light / Dark），启动时先读取偏好再应用主题。
导航项保留 accessibleName、tooltip 与键盘 focus；Settings 保持 footer 可达。视觉方向以暖中性工作空间为主，主操作采用中性色，accent 保留给链接、焦点和必要状态。

页面标题不依赖装饰性 leading icon；窗口继续使用系统原生标题栏，本项目 QSS 不控制系统标题栏颜色。
