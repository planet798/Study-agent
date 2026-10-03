# Agent 消息内代码 / Markdown 文档面板

用户批准的局部扩展；不调整暖中性 palette、Shell、Today、800 logical px 阅读列、输入、审批或工作区权限。不执行源码、不保存文件、不建笔记、不自动打开资源，也不新增会话功能、文件树、终端、浏览器或依赖。

## 识别与原文

- `agent_content_blocks.py` 是有界线性扫描器，不是完整 Markdown parser。使用 `splitlines(keepends=True)` 和原始字符串 offsets；支持反引号/波浪号、长度至少 3、至多 3 前导空格、同字符且不短于 opener 的空白尾 closer、空/未闭合块。反引号 info 不允许含反引号。
- 四反引号外层里的三反引号保持为一个文档 payload；不同字符或过短 closer 留在原文。未闭合顶层 fence 延续到消息结尾。复制只切除外层 opener/info/closer，保留缩进、空行、内部围栏及尾 LF/CRLF，不从 Qt document 反读，不 trim。
- **保守整条消息回退**：围栏外出现列表/编号列表、blockquote、缩进代码、引用定义候选（以 `[` 开头的行，包括跨行标签和转义括号；普通方括号开头的段落也保守回退），或非标准行分隔符时，继续原 Qt Markdown 渲染，不拆分任何围栏。因此带这些结构的消息暂不提供面板/复制按钮；这是避免 list lazy continuation、跨段引用链接与编号连续性损坏的明确范围，不声称完整 CommonMark 支持。
- 超过 1,000,000 字符或 30,000 行也整条回退；不会删除内容。围栏内的列表/引用/HTML 字符均为 literal payload，不影响识别。
- 保留消息直接作为 `conversation_body` child，以及完整 `raw_text` / `text` / `role` / `message_id`。分块消息的 `markdown_view` 为 `AgentAssistantContent` 原生 QWidget 组合，`markdown` 仍是完整原文；普通/回退消息仍为 `AgentMarkdownView`。现有业务没有调用其 QTextBrowser 特有 API。

## 面板

- 每个可靠顶层 fence 是独立圆角、连续背景面板。标题为 info 首 token（仅 plain text）或「纯文本」，右上「复制」→短暂「已复制」。info 从不被解释为 URL、资源或 executable metadata。
- `md` / `markdown`（大小写不敏感）有「预览 / 源码」，默认预览；Python、无语言、未知语言等只显示只读可选择源码，不猜测 Markdown。
- 本地 licensed Fluent 集合没有适合的 Copy/code SVG；采用用户允许的**明确文字「复制」fallback**，不借用 PROJECT 图标，不新造 SVG provenance，icon registry 与 license 均不改。
- 短内容完整显示；长内容初始约 14 行视觉高度，有「展开全文 / 收起」。一个 clip 容器裁切完整高度的原生 child，不截断 payload，不加入内层 QScrollArea 或滚动条；展开后按完整 document 高度显示。自动换行不插入任何源码字符。预览/源码独立文档尺寸，宽度、字体、主题和切换通过单次 child-bound timer 重算。
- 剪贴板始终来自原始 payload，和 tab、折叠、可视 excerpt 无关。复制反馈 timer 为 QObject child，换会话销毁后不会触碰旧按钮。

## 原生预览安全与背景

- 普通正文和预览保持 Qt-native GFM + `MarkdownNoHTML`；`loadResource` no-op，链接无导航，无网络/本地/qrc 读取，不递归生成面板。
- 用 Qt `BlockCodeLanguage` importer metadata 判断 block code，替换旧 all-monospace 猜测；只含 inline code 的普通段落不误绘成 block code。
- 预览 root-level code run 用原生 QTextFrame 连续底色，不按段落刷带间距的灰条。仅无 list/quote/table/indent 容器的 code run 可分组；生成的空 frame separators 压至 1px。容器内 code 留在原容器并去掉段间背景缝隙、保留原缩进；标题、列表、引用、表格仍是原生结构。

## 滚动集成（额外最小 seam，已确认）

面板展开/收起及用户切换 tab **在改变尺寸之前**发送 `content_interaction`；message 转发，`AgentWorkspacePage._add_bubble` 仅连接现有 `_clear_pending_scroll`。避免旧 rangeChanged 恢复回调因展开而跳到底部。不改变原文 signature、epoch 策略、新回复起点定位或 latest 行为。初始化、主题变化、普通 resize 不发送用户交互信号。

## 自动检查 / 人工边界

新增 parser/panel suites 覆盖 offsets、围栏长度、CRLF/LF、空/未闭合、多块、内联/嵌套容器回退、引用/编号连续性、所有 tab/折叠状态精确复制、HTML/资源/链接禁止、两主题多宽度长表格/源码、末行尺寸、timer 销毁以及展开时旧滚动回调取消。原消息、会话滚动、workspace/composer/approval/binding/shell/session/icon/theme suites 继续回归。

Linux offscreen 截图仅作结构检查，无中文字体/DPI 验收结论。

- [ ] Windows Light/Dark：中文标题、tab、复制反馈、focus 可读。
- [ ] 100% / 125% / 150% DPI：源码/预览/表格末行与圆角背景无截断；长源码换行不改变剪贴板原文。
- [ ] 900 / 1100 / 1400 窗口与窄阅读列：独立面板折叠/展开、切 tab、主题/字体变化高度稳定，无内层滚动。
- [ ] 历史阅读中展开/收起不跳底；首次进入到底、新回复定位起点、换会话无迟到回调。
- [ ] 在实际 Windows 剪贴板目标应用核对空行、缩进、内部 backticks、尾 LF/CRLF（平台/目标应用可能规范化换行）。
