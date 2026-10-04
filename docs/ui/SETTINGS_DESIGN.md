# Settings Design (UI-4)

> Settings = 独立外观设置 + 个性化 / 模型与 API / 高级三个标签页。

## Availability
Settings 一级页**始终可进入**：即使 `ai_config_service` / `prompt_registry` 缺失，
Appearance 仍可用；缺失的 panel 显示 unavailable 状态（按钮 disabled）。
这是 UI availability 修正，不改任何 Service。

## Appearance
仅 跟随系统 / 浅色 / 深色，QSettings key `appearance/theme`（行为不变）。
`theme_combo` 有 accessibleName。

## Model & API（AIProfilesPanel）
- 保留 QListWidget + detail 结构；list item 不再用 `● / ○`，active 用加粗 + “当前”后缀。
- source banner 改为 `SAInfoBanner`（`source_label` 为其 description label，兼容旧断言）。
- detail 显示 name/provider/base URL/model/API Key 状态；**绝不回显完整 Key**。
- 添加配置、订阅登录、设为当前和测试连接保持直接入口；修改 Key / 重命名 / 删除 / 条件性的旧配置导入进入更多菜单。菜单同步可用状态，原按钮属性保留为调用入口。
- Connection test 仍走 QThread，状态用语义文本/banner；不阻塞 UI。

## Prompt Manager（PromptManagerPanel）
- Tree + editor + variables + preview + save/reset 结构保留。
- Editor 使用 MONOSPACE（`SAPromptEditor`，accessibleName）。
- 右侧状态用 `SATag`（系统默认 / 已自定义）；tree 文本暂时保留以兼容。
- 变量保持 `{{variable}}`；validation / override / preview 语义不变。
- 按钮：保存 primary、预览 secondary、查看系统默认/恢复默认 subtle。

## Compatibility
保留 `AISettingsPage` / `AIProfilesPanel` / `PromptManagerPanel` 类名与关键属性
（theme_combo / list_widget / source_label / legacy_btn / editor / tree / *_btn / *_label）。

## 2026-10 设置页精修与内存草稿

- 外观保持独立可用，保留个性化 / 模型与 API / 高级三个标签页。
- 面板控件构造在 `settings_views.py`，服务与 worker 编排保留在原控制层。
- 窄窗口列表/详情纵向排布；操作组自动换行；次级命令进入菜单，原按钮属性保留为兼容入口。
- Agent 说明和每个 Prompt 的草稿只存在本页面实例内，切换/刷新不覆盖；不包含 API Key。
- 保存成功读回服务结果并清除对应草稿；保存失败保留；显式放弃读取最新已保存版本。
- Prompt 默认恢复仍需原确认，成功才清除对应草稿；预览始终使用已保存 effective template。
- 保存与校验反馈就近展示，语义颜色跟随主题；连接测试仍异步，禁止重复测试，停止后忽略迟到结果。
- 未启用和暂不可用仍有明确状态，不增加自动保存、授权或记忆候选确认功能。

## 高频弹窗（2026-10 收尾）

范围：添加模型配置、修改 Key、重命名、默认 Prompt / 已保存 Prompt 预览、
本地记忆管理与编辑、手动学习、未完成原因、学习验收。

- 公共 `components/form_dialog.py` 组织滚动正文与固定页脚，不替换原控件、构造参数或服务调用。
- 表单允许长行换行，字段随宽度展开，尺寸受可用屏幕约束；错误反馈固定在操作区上方。
- 确认在右、取消在左，确认作为默认按钮；Key 默认遮挡，显示按钮有可访问名称。
- 只读 Prompt 保留全部原文与等宽字体，可选择复制，关闭入口不进入正文滚动区。
- 记忆与手动学习沿用原长度、路线、知识点及活动规则，不增加隐式保存或候选确认链路。
- 验收题目与结果共享一个滚动区域，提交/关闭固定；判题完成后结果进入视野，退出等待和迟到结果保护保留。
- 未触碰的低频路线/项目编辑与证据弹窗不在本轮范围；已验收路线和实践页需做兼容回归。
