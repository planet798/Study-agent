# Agent 学习会话：第二轮局部视觉收敛

本轮只调整 Agent 会话呈现。沿用已接受的暖中性色、全局字体、Sidebar、Today 和 Shell；不新增工具面板、文件树、终端或会话功能。**原「不新增复制按钮」限制已由用户批准的消息内代码/Markdown 文档面板明确取代**；范围、安全、保守解析回退与 Windows 待验收见 [AGENT_DOCUMENT_PANELS.md](AGENT_DOCUMENT_PANELS.md)。原有阅读列、输入、权限与滚动行为继续保留。

## 阅读与输入

- 阅读列最大 **800 Qt logical px**，随消息 viewport（含滚动条变化）居中缩窄。上下文、警告/错误、确认区、忙碌状态和输入区共用相同列边界。
- 通过 conversation_layout 的自适应 margins 实现；居中 gutters 不计入 layout 的最小宽度，避免宽窗口的旧 margins 阻止下次缩窄。用户气泡只限制最大宽度，不固定最小宽度。消息 QObject parent 仍为 conversation_body，滚动目标的 mapTo 坐标及取消 epoch 不变。
- 助手正文透明、无包围边框；保留弱化的「学习助手」身份。用户消息右对齐，柔和中性背景，短内容自然收紧；隐藏重复的可见「你」，但保留 speaker、role、message_id、raw_text 和行的 accessibleName。
- `hello` 的旧高度来自 speaker、上下内边距、QTextBrowser 全局 padding 和额外 20px slack 的叠加。现在用户隐藏 speaker、减少内边距，正文局部 padding=0；高度使用 ceil(document height) + 稳定 contentsMargins + 2px descender 余量，避免 resize 中读取尚未更新的 viewport 高度。
- 输入初始约两行，按照实际 wrapped block 高度增长，通常上限约七行；上限同时受页面高度的 30% 约束，保留至少两行。仅触顶后出现输入内滚动条，清空即收缩。text/document/width/font/style 事件以单次 Qt timer 合并重算，不通过输入自己的高度反推可用空间。
- Enter 换行、Ctrl+Enter 发送、16k 显示计数、20k 上限、同会话 draft 保留、换会话清空、模型和忙碌锁定全部保留。

## 顶部与安全状态

- route/activity/time 使用中性 metadata；原任务描述继续通过「任务信息」展开。
- 工作区 selector 保留路径 tooltip、路径复制、打开文件夹、托管/本地选择、重新选择和解绑菜单以及 busy 锁。
- 工作区绑定模式仅在 selector 旁显示一次；不再重复展示 Workspace capability chip。只读/可读写是已有绑定显示语义，不代表本轮工具授权；tooltip 明确区分。绑定失效显式显示「不可用」，不会开放打开文件夹。
- AI 已配置改为中性弱提示。AI 未配置、写操作需确认、warning/error 等安全状态继续可见，能力状态仍来自现有 service；不推断本轮权限。
- 会话 header 独立启用可伸展文字布局，避免短 route subtitle 因 text layout 分到过窄宽度而换行。返回静态页面立即恢复默认布局参数；不改全局 header QSS。

## Markdown 与审批

- 继续 Qt-native GFM + MarkdownNoHTML；loadResource 为 no-op，不导航链接，不引入 WebEngine/HTML renderer/网络。数据库原文不变。
- 段落、列表和首标题间距收敛；代码仍为轻量 surface_alt，WrapAnywhere 不插入额外字符。完整回复由 markdown-it 结构解析，仅提升 root fence；外部列表/quote/引用定义不取消独立文档面板。普通/有界回退正文继续原生渲染，并与 Markdown 面板预览一致使用连续 code 背景；inline-code-only 段落不误判为 block code。共享引用上下文只进入普通 prose 的临时 Qt 输入，面板 payload 与复制不变，详见 AGENT_DOCUMENT_PANELS.md。
- 原生表格使用等比例列、细边框和 4px cell padding。表格宽度根据 viewport 重算，预留 Qt cell/border chrome 及各列整数舍入余量；没有通过剪裁正文伪造响应式，也没有转为其他 renderer。修正 Qt importer 在代码块之后首个表头格产生的空段落和缺失粗体，仅移除生成的空 block，不删原始文字；显式设置 table/cell 细实线边框。
- 已覆盖 3/5/8 列表格在 280/380/501/800 logical px 下、两主题的长路径换行、无内部滚动溢出及剪贴板逐字符一致。任意多列/极端大字号的最小 glyph 宽度仍是 Qt-native 的物理限制；若实际必要表格不能容纳，需报告并决定，不能隐藏列或暗换 renderer。
- 待确认操作继续独立位于输入上方；紧凑 margins 不删固定动作说明、plain-text 笔记预览、批准/拒绝文案、IDs 或串行锁定。
- AgentSendButton/AgentApprovalApprove 保留 objectName，以 Agent-local `[saVariant="primary"]` 规则覆盖中性主操作的 enabled/hover/pressed/disabled/focus；不改其他页按钮。

## 自动回归边界

重点 suites：test_agent_message_widget、test_agent_composer、test_agent_conversation_scroll、test_agent_workspace_ui、test_agent_workspace_binding_ui、test_agent_approval_ui、test_learning_shell、test_session_management_ui。追加 header/theme/Today 回归以确认局部 sizing 和 QSS 不影响第一轮接受结果。

测试只使用临时 DB/QSettings。Linux offscreen 几何结果不等同 Windows 截图或字体/DPI 验收。

## Windows 人工验收（尚未完成）

- [ ] Windows Light/Dark：短 `hello` 用户气泡尺寸、助手透明正文及身份、发送/批准按钮 enabled/disabled/focus 可读。
- [ ] 100% / 125% / 150% DPI：中文字体、英文 descender、末行、列表、代码不被截断；逻辑阅读宽度约 800。
- [ ] 900 / 1100 / 1400 窗口与 Sidebar 展开/折叠：阅读列、错误/忙碌状态、审批/输入边界对齐，短 route subtitle 不多余换行。
- [ ] 输入空白→两行→wrapped 多行→上限→清空；调整窗口高度/宽度和主题后 cap 合理、未触顶无内部滚动、draft 不丢。
- [ ] 模型未配置、busy、审批执行、失效工作区、只读工作区、三类确认操作和长笔记预览：全部安全文案可见且锁定正确。
- [ ] 中文/英文 3/5/8 列表格、长代码/路径与大字号：完整可读、无横向数据丢失；实际必要表格超出 Qt 原生能力时记录并升级决策。
- [ ] 新助手回复定位起点，读历史时新回复不抢位置，点击「最新」到底；刷新/换会话/关闭的迟到滚动回调不串会话。

截图记录应注明操作系统、字体、Qt 版本、DPI 与窗口大小；offscreen 预览只作结构参考，以上未勾项不可据此宣布通过。
