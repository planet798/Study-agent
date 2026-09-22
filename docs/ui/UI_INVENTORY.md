# Study-Agent — UI Inventory (UI-0)

> 基线 commit: `a36e688`
> 审查范围: `app/ui/` 全部 20 个 Python 文件，共 **9,366 LOC**。
> 本文件只做盘点，不改动任何 production code。
> 架构边界: `ui → services → database`（见 `docs/ARCHITECTURE.md` §1、`docs/AGENT_GUIDE.md` §2）。
> UI 只调用 service，**不得**内嵌业务规则。

## 0. 汇总

| 类别 | 文件数 | LOC |
|---|---|---|
| 主窗口 / 页面 | 5 (`main_window`, `routes_page`, `practice_page`, `ai_settings_page`, `summary_pages`) | 4,696 |
| 组件 / 控件 | 1 (`task_widget`) | 352 |
| 对话框 | 8 (`dialogs`, `manual_task_dialog`, `past_task_dialog`, `route_dialogs`, `route_builder_dialogs`, `practice_dialogs`, `career_dialogs`, `capability_dialog`, `assessment_dialog`, `topic_learning_dialog`, `ai_settings_dialogs`) | 3,613 |
| 后台线程 | 1 (`ai_worker`) | 216 |
| 样式 | 1 (`styles`) | 192 |
| 空文件 | 1 (`__init__.py`) | 0 |

---

## 1. `app/ui/main_window.py` — 1,856 LOC（最大文件）

| 项 | 内容 |
|---|---|
| 主要 class | `TodayViewState` (dataclass)、`MainWindow(QMainWindow)` |
| 职责 | 应用外壳 + Today 页面 + 顶部导航 + 托盘 + 生命周期 + 业务编排 |
| 依赖 service | `TaskService`、`DateService`、`TaskReviewService`、`ManualTaskService`、`StudyPlanService`、`DailyPlannerService`、`SummaryService`、`AssessmentService`、`ReviewService`、`SkillService`、`JdService`、`JdSummaryService`、`LearningRouteService`、`RoutePlanService`、`CapabilityService`、`PracticeProjectService`、`PracticeCapabilityService`、`PracticeReadinessService`、`AIConfigService`、`PromptRegistry`、`scheduler`、`ai_route_service` … |
| 依赖 UI | `ai_worker`、`assessment_dialog`、`dialogs`、`manual_task_dialog`、`styles`、`task_widget`；延迟 import `summary_pages` / `routes_page` / `practice_page` / `ai_settings_page` / `career_dialogs` |
| 局部 style/QSS | `self.scroll.setStyleSheet("background: transparent;")`（第 313 行）；全局 `APP_STYLE` 注入 |
| 是否值得拆分 | **是，最高优先级**。包含至少 A–F 六类职责（见 §main_window 审计）。Today 页面整体混在 MainWindow 内。 |

`MainWindow` 内部功能区块：

- 构建与生命周期：`_build_ui` / `_build_tray` / `_apply_styles` / `_on_startup` / `run_app` / `closeEvent` / `_shutdown` / `_stop_ai_workers`
- 导航：`_switch_page` / `_switch_to_routes` / `_switch_to_practice` / `_switch_to_ai_settings`
- Today 渲染：`refresh` / `_clear_dynamic_list` / `_add_section_header` / `_add_section_hint` / `_add_task_widget` / `_add_label`
- Today 滚动保持：`capture_today_view_state` / `restore_today_view_state`
- 路线筛选/统计：`_route_name_map` / `_active_learning_routes` / `_reload_route_filter` / `_selected_route_filter` / `_matches_route` / `_on_route_filter_changed` / `_update_route_stats`
- 手动任务：`_available_topics` / `_topics_by_route` / `_on_add_learning_task` / `_confirm_remove_dialog` / `_on_remove_task`
- 职业面板（Today 内嵌）：`_add_skill_overview` / `_add_jd_trend_panel` / `_add_jd_candidates` / `_add_curriculum_gap` / `_add_skill_gap_from_trend` / `_on_add_jd_summary` / `_on_view_history_jd` / `_on_accept_candidate` / `_on_ignore_candidate` / `_on_add_jd` / `_show_jd_detail`
- 验收编排：`_on_start_assessment` / `_ensure_task_knowledge_point` / `_on_assessment_ready` / `_on_assessment_failed` / `_open_assessment_dialog` / `_on_assessment_completed`
- 规划状态：`_update_phase_info` / `_planning_paused` / `_planning_route_name` / `_update_planner_info` / `_on_replan`
- 任务操作：`_on_complete` / `_on_not_done` / `_on_dialog_postpone` / `_on_dialog_no_postpone` / `_on_postpone`
- worker 释放：`_release_assessment_worker` / `_release_worker`
- 托盘：`_restore_from_tray` / `quit_app`

---

## 2. `app/ui/routes_page.py` — 1,152 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `RouteDetailDialog(QDialog)`、`LearningRoutesPage(QWidget)` |
| 职责 | 学习路线总览卡片 + 路线详情（Plan/Phase/Topic/Activity/Skill/Gap/Project/Blocker） |
| 依赖 service | `route_service`、`route_plan_service`、`progress_service`、`ai_route_service`、`skill_service`、`topic_learning_service`、`capability_service`、`outcome_service`、`practice_service`、`practice_capability_service`、`practice_readiness_service` |
| 依赖 UI | `dialogs.show_warning`、`route_builder_dialogs`、`ai_worker.AIRouteBuilderWorker`、`route_dialogs`、`styles.apply_secondary_button_text` |
| 依赖 database | `ROUTE_TYPE_GROUP`（仅常量）、`RouteValidationError`/`RouteStructureError`（异常类型） |
| 局部 style/QSS | 无 `setStyleSheet`；仅 `objectName` |
| 是否值得拆分 | **是**。总览与详情可拆为两个文件；详情内「知识/能力/复习」「技能」「课程缺口」「项目」四组区块可独立组件化。 |

`RouteDetailDialog.refresh()` 一个方法内叠加了大量 section：路线进度、Mastery、学习活动、知识掌握/能力逐行、复习状态、Phase 卡片、技能、课程缺口、关联项目、Practice blocker。

## 3. `app/ui/practice_page.py` — 723 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `PracticeProjectsPage(QWidget)`、`PracticeProjectDetailDialog(QDialog)` |
| 职责 | 实践项目组合（portfolio）总览 + 项目详情（routing/skills/topics/milestone/output/readiness/evidence） |
| 依赖 service | `practice_service`、`route_repo`、`skill_repo`、`plan_repo`、`capability_service`(practice_capability)、`readiness_service` |
| 依赖 UI | `practice_dialogs`（10 个对话框）、`capability_dialog`（延迟）、`styles` |
| 局部 style/QSS | 无；含自定义 `_secondary()` 工厂、`_clear_layout()` 递归工具、`_FILTERS` 常量 |
| 是否值得拆分 | **是**。详情 dialog 内部 7 个 section 可复用 `SACard`/`SASectionHeader`；`_secondary` 与 `routes_page._secondary` 重复。 |

## 4. `app/ui/ai_settings_page.py` — 671 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `AIProfilesPanel(QWidget)`、`PromptManagerPanel(QWidget)`、`AISettingsPage(QWidget)` |
| 职责 | 设置页：Profile 列表/详情 + Prompt 树/编辑器/变量/预览；`QTabWidget` 两 tab |
| 依赖 service | `AIConfigService`、`PromptRegistry`、`PromptPreviewService` |
| 依赖 UI | `ai_settings_dialogs`（5 个对话框）、`ai_worker.AIConnectionTestWorker`、`styles` |
| 局部 style/QSS | 无显式 QSS；但 mono 字体未定义（Prompt 编辑器应为 monospace） |
| 是否值得拆分 | **是**。未来 Settings 演进为 Appearance / Model & API / Prompt Manager 三个 section；当前是 2-tab。 |

## 5. `app/ui/summary_pages.py` — 294 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `MonthlySummaryPage(QWidget)`；模块函数 `_h_mm`、`_ai_section`、`_add_pair` |
| 职责 | 月总结：月份切换 + 本地统计 + 分类排行 + 路线维度 + 项目能力证据计数 + AI 解读 |
| 依赖 service | `summary_service`、`practice_capability_service` |
| 依赖 UI | 无（仅 utils.date_utils） |
| 依赖 utils | `month_range`、`today` |
| 局部 style/QSS | 无；`_add_pair` 使用 `setFixedWidth(120)`（DPI 风险） |
| 是否值得拆分 | 中等。`_ai_section` / `_add_pair` 应成为 Design System 的 section/value 组件。 |

## 6. `app/ui/task_widget.py` — 352 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `TaskWidget(QFrame)` |
| 职责 | 单任务卡片纯展示；通过信号把操作上抛，自身不调 service |
| 依赖 service | **无**（符合纯展示约定）；依赖 `database.repository.Task`、`database.schema` 常量、`services.learning_activity.activity_label`（延迟 import） |
| 依赖 UI | `styles.apply_secondary_button_text` |
| 信号 | `complete_requested`、`not_done_requested`、`postpone_requested`、`remove_requested`、`assessment_requested` |
| 局部 style/QSS | 无；用 `objectName="TaskCard"` + Qt property `done` / `postponing`（QSS 属性选择器） |
| 是否值得拆分 | **是**。卡片承担了 title/desc/meta/time/tags/reason/warning/actions 全部信息，未来应拆为 `SATag` / `SACard` / `SAButton` 组合。 |

## 7. `app/ui/ai_worker.py` — 216 LOC

| 项 | 内容 |
|---|---|
| 主要 class | `AIConnectionTestWorker`、`AIReviewWorker`、`AssessmentWorker`、`AIRouteBuilderWorker`、`RouteSuggestionWorker`、`run_start_assessment`、`run_submit_answers` |
| 职责 | QThread 后台调用 AI / service；只传普通数据与 `db_path`+工厂，不跨线程传 DB 连接 |
| 依赖 | `database.connection.get_connection`、`database.repository.Task`、`services.task_review_service` |
| 是否值得拆分 | 否（职责单一）。UI 重构期间**行为必须保持**。 |

## 8. 对话框清单

| 文件 | LOC | 主要 class | 职责 | 局部 QSS | 可拆分 |
|---|---|---|---|---|---|
| `dialogs.py` | 238 | `NotDoneDialog`、`AIReviewDialog`、`show_warning` | 未完成原因、AI 复核结果 | `error_label` 局部红字 ×2 | 中 |
| `manual_task_dialog.py` | 290 | `AddLearningTaskDialog`、`KIND_TODO` | 添加普通 To-do / 正式知识任务 | 局部红字；`setFixedHeight(70)` | 中 |
| `past_task_dialog.py` | 177 | `PastTaskConfirmationDialog` | 跨日未确认任务逐条确认 | 局部红字 | 低 |
| `route_dialogs.py` | 332 | `CreateLearningRouteDialog`、`EditLearningRouteDialog`、`AddPhaseDialog`、`AddTopicDialog` | 路线/阶段/主题 CRUD | 局部红字 ×4 | 中 |
| `route_builder_dialogs.py` | 435 | `AIRouteBuilderDialog`、`RouteDraftPreviewDialog`、`SkillPickerDialog` | AI 路线草稿生成与预览 | 局部无 `setStyleSheet`（用 `setFixedHeight`） | 中 |
| `practice_dialogs.py` | 703 | 11 个 class（Create/Edit/Manage×3/Milestone/Output/Evidence/Revoke/Requirement） | Practice 组合管理 | 局部红字 ×4；`setFixedHeight` ×7 | **是** |
| `career_dialogs.py` | 703 | `JdInputDialog`、`JdDetailDialog`、`ResumeMaterialDialog`、`JdCandidateAcceptDialog`、`JdSummaryInputDialog`、`JdHistoryDialog` | JD / 简历材料 / 面试准备 | 局部红字 | **是** |
| `capability_dialog.py` | 300 | `CapabilityEvidenceDialog`、`ExperimentOutcomeDialog` | Capability 证据查看 / 实验成果 | 局部红字 | 中 |
| `assessment_dialog.py` | 236 | `AssessmentDialog` | 验收问答 + 提交线程 | 局部红字 | 中 |
| `topic_learning_dialog.py` | 224 | `TopicLearningProfileDialog` | Topic 学习活动配置（上下移排序） | 无 | 中 |
| `ai_settings_dialogs.py` | 272 | `AddAIProfileDialog`、`EditAPIKeyDialog`、`RenameProfileDialog`、`FinalPromptPreviewDialog`、`PromptDefaultDialog`、`PasswordLineEdit` | 设置相关弹窗 | `setFixedWidth(40)`（眼睛按钮） | 中 |

## 9. `app/ui/styles.py` — 192 LOC

| 项 | 内容 |
|---|---|
| 内容 | 单一 `APP_STYLE` QSS 字符串 + `SECONDARY_TEXT_COLOR` + `apply_secondary_button_text()` |
| 职责 | 全局浅色主题、任务卡片、按钮、输入、统计栏、状态栏 |
| 是否值得拆分 | **是**。应演进为 `app/ui/design/`（tokens + theme_manager + `styles/light.qss` / `dark.qss`），见 `UI_BLUEPRINT.md` §8。 |

## 10. 其它观察

- **无 `app/ui/components/`，无 `app/ui/design/`**：目前所有视觉都靠字符串 `objectName` + 一个全局 QSS。
- **无 icon 资源文件**：`app/` 下没有任何 `.svg` / `.png` / `.ico`；图标来源是 emoji / Unicode / 代码手绘（托盘 `_tray_icon()`）。
- **无 QGroupBox / 无 QSS tab 样式**：设置页 `QTabWidget` 使用系统原生外观。
- **无页面级独立样式文件**：所有页面复用同一个 `APP_STYLE`；页面私有差异主要靠 `objectName` 与局部红字。
- **`objectName` 是当前唯一的“设计 token”**，被测试直接依赖（见 `STYLE_AUDIT.md` §6），重构时必须兼容。
