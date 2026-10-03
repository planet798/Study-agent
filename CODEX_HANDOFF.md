# Study Agent — Codex 开发交接

> 本文面向在 Codex 中继续开发本项目的 coding agent，不依赖 Pi 会话、技能、子代理或临时文件。
> 交接基线：`master` 的功能提交 `6f6f656`（Fix mixed Markdown document panels with structural parsing）。本文自身会作为后续文档提交发布；接手时以实际 `git log` 为准。
> 用户最新确认：**最新 Markdown 文档面板修复已成功，Windows 实际验证没有问题。** 不要再把该修复标成未完成任务。

## 1. 接手先做什么

1. 阅读本文、`docs/PRODUCT_BASELINE.md`、`docs/ARCHITECTURE.md`、`docs/AGENT_GUIDE.md`。
2. 查看 `git status --short`、当前分支、最近提交和 remote，保护用户未提交的修改。
3. 根据本次用户任务定向读源文件和对应测试，不需要整读所有大文件。
4. UI 工作再读 `docs/ui/DESIGN_SYSTEM.md` 及相关页面文档；会话/Markdown 工作重点读 `docs/ui/AGENT_CONVERSATION_DESIGN.md`、`docs/ui/AGENT_DOCUMENT_PANELS.md`。
5. 告诉用户你理解的范围与验证方法；若用户只要求 plan，只做分析与方案，不编辑、安装、提交或推送。
6. 没有新的实施任务时，不自动继续改界面，也不要把下面的建议路线当成已批准 backlog。

本文件不是 `AGENTS.md`，不要假定 Codex 自动加载它。可以让用户在新会话中说：

> 请先阅读项目根目录 CODEX_HANDOFF.md，并核对当前 git 状态，按文档接续开发。最新 Markdown 面板修复已通过我的实机验收。下面是本次任务：……

### 文档新旧判断

- 本文的近期 UI 决策和用户验收记录补充已有文档，不代替业务的 canonical baseline。
- `UI_BLUEPRINT.md`、`STYLE_AUDIT.md` 等包含历史设计；`AGENT_PRODUCTION.md` 的旧消息宽度/气泡说明也可能落后于近期 UI 实现。
- 遇到冲突，核对当前源代码、最新相关设计文档及明确用户决定；不要根据旧文档恢复被取消的粗卡片、蓝色主导风格或旧消息尺寸。
- 文件行数、测试总数、依赖的实测版本是快照，不是永久常量。

## 2. 项目是什么

**Windows 桌面学习应用，不是 Web 项目，也不是通用聊天机器人或 Todo 管理器。**

- Python 3.12+、PySide6 / Qt Widgets、SQLite。
- 静态导航：今日、学习路线、实践项目、设置（侧栏底部）。
- 动态「学习会话」按 `session_id` 打开内部 Agent Workspace，不是第五个静态 `PageKey`。
- 主链路：Curriculum → Planning → Today → task-bound Agent Session → Assessment / Practice Evidence → Mastery / Capability。
- UI 调用 Service，Service 调用 Repository，Repository 访问 SQLite；不要在 UI 中新增业务计算/写库捷径。
- 入口：`app/main.py`；GUI 主编排：`app/ui/main_window.py`。

### 主要目录

| 目录/文件 | 职责 |
|---|---|
| `app/ui/` | 页面、组件、Qt worker、主题与消息呈现 |
| `app/services/` | 任务、课程、规划、验收、实践、会话和工作区业务 |
| `app/database/` | SQLite schema、connection、领域 repositories |
| `app/agent/` | 学习 Agent runtime、上下文、工具、memory、approval、trace/evaluation |
| `app/ai/` | 模型接入、Profiles、Secrets、Prompt、Planner 等 |
| `app/diagnostics/` | 发布迁移、历史完整性验证等 |
| `oauth_bridge/` | Node OAuth 接入桥，不是开发会话本身 |
| `scripts/` | Windows 启动、快捷方式、诊断等 |
| `tests/` | pytest / pytest-qt 回归；完整混合回复在 `tests/fixtures/agent_mixed_reply.md` |
| `data/` | 用户实际 DB、工作区、日志及本地配置；不是测试材料 |

**重要区别：** 用户从 Pi 切换到 Codex 作为开发工具，不代表应用里的 OAuth 桥可以删除。`oauth_bridge/package.json` 当前依赖 `@earendil-works/pi-ai`；这是产品运行依赖，不是要求开发必须在 Pi 中进行。

## 3. 用户的工作方式与提交偏好

- 用中文沟通，先讲结论、范围和风险，少说工具内部术语。
- 用户喜欢先 plan，再通过编号问题＋推荐答案澄清取舍，确认后才执行。Codex 不必安装 Pi 的 grillme 技能，按相同访谈方式即可。
- `plan` / 「先提供解决方案」不是实施授权。未确认前不修改项目。
- 实施完成后说明实际变更、测试结果、尚未验证事项，不能把自动测试当成实机视觉验收。
- 用户已明确授权：**以后完成开发并通过必要验证/审查后，自动 commit 并 push 到 GitHub 当前工作分支。** 当前常用分支为 `master`，remote 是 `origin`，仓库 `planet798/Study-agent`。
- 提交只包含本次任务拥有的改动；发现工作区已有用户修改，不得直接 `git add -A` 全部提交。
- 不 force push，不重写历史，不擅自合并/发布；测试失败、推送失败或分支不明时如实报告并停止对应操作。
- 没有 Pi 子代理也能工作：用 Codex 可用的审查能力，或另做独立 diff 检查。不要调用不存在的 Pi 工具，也不要虚构「独立审查通过」。

## 4. 已确认并验收的 UI 方向

视觉参考是现代 Codex 工作空间的**克制、暖中性、内容优先**，不是照搬网页布局。

### 外壳与今日页

已通过用户实机验收：

- 深色作为主要设计基准，浅色及跟随系统保留，不重置用户主题偏好。
- 暖炭灰/暖白，中性色导航选中态；蓝色主要留给链接、焦点和必要语义。
- 侧栏与会话区底色连续，保留折叠、独立会话滚动、底部设置及会话管理。
- 今日页统计是紧凑摘要，不再是两个巨型卡片；任务以轻量列表呈现。
- Planner 提示紧凑、说明可见，保留真实可用状态和重规划条件。
- 无任务时集中空态与添加按钮，避免两个主入口及空白滚动区并存；路线无结果与全局空态区分。
- 没有为了美化修改统计口径、任务操作、规划硬 gate 或业务规则。
- 原生 Windows 标题栏保留；没有实施无边框/自定义标题栏。

### 学习会话

已完成实现与回归；用户此前提供实机截图作为后续文档面板改进依据：

- 助手正文透明、弱化身份标识；用户消息右对齐、柔和底色、短内容收紧，隐藏重复的「你」。
- 阅读列最大约 800 Qt 逻辑像素，上下文、状态、审批和输入边界与实际 viewport/滚动条协调。
- 输入约两行起步，自增长到约七行，同时受窗口高度限制；触顶才内部滚动，清空收缩。
- Enter 换行、Ctrl+Enter 发送；16k 起计数、20k 上限；草稿、锁定条件不变。
- 顶部任务信息收紧，工作区模式不重复；安全/异常信息仍可见。
- 审批仍独立在输入区上方；重要说明、预览和按钮不隐藏、不新增确认步骤。
- 代码/表格使用原生 Qt 排版；不引入 WebEngine/浏览器。

### 不要退回的风格

大面积亮蓝描边、粗重封闭卡片、层层容器、彩色徽章堆叠、巨大欢迎标题、玻璃/光晕/渐变、装饰动画。精修优先投入字体层级、间距、对齐、比例和交互状态。

## 5. 最新 Markdown 面板：已解决的问题与关键机制

### 提交脉络

| 提交 | 内容 | 当前状态 |
|---|---|---|
| `1acaf38` | 暖中性 UI、外壳与今日页 | 用户实机验收通过 |
| `bb5955f` | 学习会话布局、输入自增长 | 后续功能基线，保留 |
| `326ccec` | 代码/文档面板、复制、预览/源码、展开 | 面板组件保留；其中过宽回退策略已被修正 |
| `6f6f656` | 成熟结构解析、共享引用、混合回复修复 | **用户刚确认实际验证成功** |

这些功能提交均已推送 `origin/master`。接手不要重新修复已被 `6f6f656` 替换的扫描器。

### 曾经的失败，必须防回归

旧扫描器在围栏外遇到任意列表、引用或方括号开头行时，返回整条 `MarkdownBlock`。回复末尾正常的「三点说明」也会取消前面所有面板；没有复制和 tabs，原生显示又恢复逐行灰底。

旧测试证明了「回退不丢原文」，却未证明目标 UI 实际出现。这是已确认的覆盖缺陷，**不得恢复这一策略**。

### 当前实现

| 文件 | 关键职责 |
|---|---|
| `app/ui/agent_content_blocks.py` | markdown-it 解析完整消息，root fence 识别、原文偏移、共享引用定义 |
| `app/ui/agent_code_panel.py` | 独立面板、复制、md 预览/源码、长文展开、正文组合 |
| `app/ui/agent_message_widget.py` | Qt 原生安全 Markdown、引用渲染上下文、连续代码背景、消息身份与高度 |
| `app/ui/agent_workspace_page.py` | 外层滚动、会话状态、审批/输入及面板用户交互集成 |
| `docs/ui/AGENT_DOCUMENT_PANELS.md` | 最新精确设计与保守边界 |

当前要求：

1. `markdown-it-py>=4.0,<5.0` **只做结构解析，不生成 HTML**。启用 table/strikethrough，不加 linkify/plugins。
2. 完整回复解析一次；只提升真正 root fence。list/quote 内的围栏及缩进代码保留原结构，不影响旁边独立面板。
3. 外部普通列表、引用、标题、方括号段落、引用定义不再取消面板。
4. 解析器可能规范化换行和缩进，**复制必须取原始字符串 payload 切片，不用 token.content 或渲染结果**。
5. 保留 CRLF、空行、缩进、内部较短围栏、尾换行；外四反引号包内三反引号仍是一份 Markdown 文档。
6. `md`/`markdown` 默认预览，可切源码；其他语言/无语言只读源码，不猜测文档类型。
7. 复制永远复制完整 payload，不含外层围栏或面板标题，与 tab/折叠状态无关。短暂「已复制」反馈，当前为文字按钮，不是复制 SVG。
8. 长内容初始约 14 行，有明确「展开全文/收起」；面板没有内层滚动区，展开后使用会话外层滚动。
9. 跨片段引用用完整消息的原始定义顺序形成临时 Qt 上下文，首定义优先。上下文不进入 raw_text、source、复制或持久化。
10. Markdown 面板预览是 payload 自有文档，不继承父回复的引用定义。
11. 普通正文、原生有界回退和预览默认使用连续代码背景；不能恢复逐行条纹。
12. 根级代码 frame 分组保留 list/quote/table/indent 保护及首 code block 的 importer metadata。
13. `content_interaction` 在用户展开/切 tab的尺寸变化前触发，仅取消旧 pending scroll；普通初始化/主题/resize不能误当成用户交互。

### 安全与类型契约

- User、源码、审批文案恒为 PlainText；Assistant/preview 是 Qt GFM + MarkdownNoHTML。
- 禁链接导航，`loadResource` 恒不加载；不能获取 http/file/qrc 或执行 JS/HTML。
- 原始消息、role、message_id、raw_text/text 不改；UI 渲染不回写数据库。
- 分块消息的 `markdown_view` 是 `AgentAssistantContent` QWidget 组合，保留完整 `.markdown`；普通消息仍是 `AgentMarkdownView`。不要假定每条助手消息都是 QTextBrowser。
- 消息直接属于 `conversation_body`，滚动目标的 QObject 身份、mapTo 和 epoch 不能随意更改。

### 已知边界（不是待重做功能）

- CommonMark + table/strikethrough 与 Qt GFM 的全部扩展不完全相同；遇到真正语法/源码映射异常需保守处理，不能凭此重新给正常混合回复整条拒绝。
- 字符/行数/深度有界：超过 1m 字符、30k 行、深度边界等走保留原文的 native fallback。
- 派生引用上下文若被 Qt 解释为可见文本，该 prose 明确降级纯文本，不显示额外上下文、不取消其他面板。
- 大量引用与大量 prose 组合可能产生重复上下文工作量。本次隔离样本 100 面板/1000 引用构造约 0.18s，未证明性能缺陷；不能把它当已确证卡死问题，但今后可做有依据的资源预算评估。

## 6. 最新验证与实机验收记录

功能提交 `6f6f656` 的父代理最终复跑：**350 passed in 134.79s**；独立代码审查无已证实问题。compileall、diff-check、全局 design/QSS 未变检查通过。

- 350 是本次选定相关回归数，**不是全套测试通过声明**。
- 完整 fixture：`tests/fixtures/agent_mixed_reply.md`，包括开场、路径围栏、外四反引号 Markdown、三段内三反引号 Python、外围 bullets/quote。
- 离屏实际 widget 检查确认两个真实面板、默认 md 预览/tabs、两块原文复制、外部结构和展开入口。
- 用户在最新消息中确认 Windows 实际验证成功；这是此前「尚待实机验收」状态的最新补充。
- 用户未逐项报告所有 DPI/超大表格矩阵，不要把此次通过夸大为所有极端场景全通过。已有文档的未勾选细项是更广泛检查，不等于当前主要问题仍未解决。
- `/tmp` 截图、日志和 Pi 会话文件不是可靠交接资源，可能不存在。新 Codex 环境应从仓库 fixture 和测试重新生成，不依赖旧临时路径。

## 7. 环境与 Windows 常用命令

以下在项目根目录执行。使用项目 `.venv`，不用全局 Python/pip，不能提交 `.venv`。

### 现有 Windows 环境更新

先完整退出应用（包括系统托盘），再在 PowerShell 执行：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('markdown-it-py'))"
```

当前实测 parser 为 4.2.0，PySide6 为 6.11.2，Python 为 3.12.3；requirements 的范围才是安装约束。

无需激活 venv、无需重建数据库。随后使用原快捷方式启动；拉取源码但不更新依赖会缺少结构解析器。

### 全新 Windows 开发环境

先安装 Python 3.12+，然后：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`py -3.12` 需要该版本已安装；其他满足项目要求的 Python 应显式选择正确解释器。

GUI 启动（注意默认会使用真实 `data/study_agent.db`，只在用户授权的实际运行中执行）：

```powershell
.\.venv\Scripts\python.exe -m app.main
```

### Linux / 无显示开发环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q tests/test_agent_content_blocks.py tests/test_agent_code_panel.py
```

Qt 测试由 `tests/conftest.py` 设置 offscreen；直接写 Qt 隔离探针时，在进程内/命令环境设 `QT_QPA_PLATFORM=offscreen`，不要给实际 Windows GUI 永久设置这个变量。Linux 缺中文字体的预览不能代替 Windows 排版验收。

### 应用 OAuth 桥（仅相关开发/首次 OAuth 使用需要）

Node.js 22.19+：

```powershell
cd oauth_bridge
npm ci
npm test
cd ..
```

只使用假凭据/隔离测试，真实登录与真实 API 调用需用户明确授权。不要为 UI 验证读取系统凭据或请求在线模型。

## 8. 测试策略

先 targeted → 相关 regression → 必要的 broad/full gate。按 `docs/AGENT_GUIDE.md` 控制完整测试次数，不要每次小改动跑全量。

完整 suite 曾在 600/900 秒工具限时中途结束；已有指南记录全量可能需要 20–38 分钟。**超时不等于通过，也不能盲目循环重跑。** 为 full gate 预留足够运行窗口，保留日志并定位失败；当前 UI 修复只有明确记录的相关 suite 验证。

### UI / 面板最小回归（PowerShell）

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_content_blocks.py tests/test_agent_code_panel.py tests/test_agent_message_widget.py tests/test_agent_conversation_scroll.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_workspace_ui.py tests/test_agent_composer.py tests/test_agent_approval_ui.py tests/test_agent_workspace_binding_ui.py tests/test_learning_shell.py tests/test_session_management_ui.py
```

其他影响面：`test_theme_manager.py`、`test_theme_runtime.py`、`test_theme_preferences.py`、`test_page_header.py`、`test_today_page.py`、`test_today_scroll_preservation.py`。修改对应层才追加相应验证。

### 广泛与专项测试

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not slow" -q
.\.venv\Scripts\python.exe -m pytest -m ui -q
.\.venv\Scripts\python.exe -m pytest -m migration -q
.\.venv\Scripts\python.exe -m pytest -m threaded -q
# 正式完整 gate，不会默认跳过 slow
.\.venv\Scripts\python.exe -m pytest -q
```

Linux 同样使用 `.venv/bin/python -m pytest`。不要依赖 `pytest` 恰好在 PATH 中。PowerShell 不应直接照抄 Bash 的未展开文件通配符测试命令。

测试使用临时 DB、隔离 QSettings、fake keyring；不得拿正式学习库或真实会话作自动 fixture。文档修改只需检查路径、命令、事实及 `git diff --check`，不必伪称重跑了全套功能测试。

## 9. 不可破坏的产品与数据边界

详细以 `docs/PRODUCT_BASELINE.md` 为准：

- 任务完成 ≠ 掌握；正式 Mastery 由 Assessment 更新，Capability 是独立证据维度。
- 实践项目不是随意改变 Scheduler 的理由；route priority 用户所有，预算/公平性有既有规则。
- Daily Review、Monthly、Today Career Dashboard、Generic Todo 已退役；历史表/行保留，不恢复为 UI 功能，也不顺手删除。
- route 身份使用稳定 route_key；system seed 不覆盖用户 priority/planning/archive 状态；一个 route 只允许一个 active plan。
- Session origin task_id 和历史消息不可改写；任务 done/not_done/cancelled 不阻止打开已有 Session；离开会话页不关闭会话。
- 会话管理有 rename/reset/pin/unpin/archive/restore；无 Delete API/UI。忙碌时禁止不安全的切换和管理。
- Native 学习工具/MCP 只读；Task completion、Assessment start、Learning Note save 经显式 application Approval 调用 canonical Services。不能从聊天里的「同意」自动授权。
- 文件 Workspace 是另一层：无绑定无文件工具；managed 可有界读写；local 只读，禁止写入/执行和 host-shell fallback。
- SQLite 学习笔记与 Workspace `.md` 文件不同；写文件不代表完成任务或提升 Mastery/Capability。
- worker 使用自己的 SQLite connection；不要将 UI 主线程连接传到 QThread。
- Secrets/API Key/OAuth tokens 不进入 DB、源码、日志、截图或交接文档；真实凭据由系统 keyring 管理。Trace/Evaluation不复制对话/工具正文。

### 数据库迁移

当前核验：`SCHEMA_VERSION=27`，`FINGERPRINT_VERSION=7`，`EVALUATOR_VERSION=2`。后续变更需重新核对源文件。

- UI/Markdown 修改没有 schema 迁移，不需要让用户删除 DB。
- 新增 schema 必须真实逐级 migration 并提高版本，保持历史指纹/行子集保全。
- 正式库落后/不可读时保留 Migration Gate，不能为了启动成功放宽门禁。
- 正式迁移遵循 README 的 `db-release backup → inventory → migrate → verify`，执行前明确授权。
- 备份用 SQLite Backup API 包含 WAL；不能以文件复制冒充一致备份。
- `get_fresh_connection()` 是隔离测试快路径，不可拿来迁移真实库。

## 10. 后续工作状态

**当前没有已经批准、尚待执行的新任务。** 最新面板问题已解决，用户现在只要求交接。

此前讨论过的后续 UI 优先级是：学习路线 → 实践项目 → 设置/对话框收尾。它们只是建议顺序，不是自动施工授权。用户若选择其一，先看对应设计与源代码，再按实际截图澄清范围。

不要为「彻底去 AI 味」改模型输出原文、删状态/权限说明，或重写业务。视觉问题首先用完整用户场景复现，验收需断言目标控件真实存在，不只看测试总数。

## 11. 提交前检查清单

- [ ] 只实现本次已确认需求，没有连带产品/权限/数据变更。
- [ ] 已保护工作区中用户原有修改；review diff 包括本次新增未跟踪文件。
- [ ] targeted/相关 regression 有真实结果；失败/超时/未验收项目诚实说明。
- [ ] 涉及重要结构、安全或渲染修改时完成相应审查，不伪造独立审查。
- [ ] 原文、复制、resource safety、草稿/审批/滚动等相关不变式保持。
- [ ] `git diff --check` 通过；没有 DB、凭据、日志、venv 或临时截图进入 commit。
- [ ] 需要新依赖时更新 requirements，并给 Windows `.venv` 更新指令。
- [ ] 文档没有把历史失败策略、临时日志路径或某次测试数当成当前功能要求。
- [ ] 按用户偏好提交本次文件并正常 push 当前分支；不 force、不包含无关改动。
- [ ] 最终答复列出 commit、push 是否成功、测试证据与必要操作。

---

交接摘要：**保留已验收的暖中性桌面 UI；最新混合 Markdown 回复的结构分块与文档面板已修复并经用户实机确认。下一位 Codex 可以直接从新的用户任务继续，不需要重新设计或重做这些完成项。**
