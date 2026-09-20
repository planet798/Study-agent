# Study Agent · Prompt 全量审计清单

> 本清单为「AI 设置中心」交付的一部分（需求 #45）。
> 所有会真实发送给 LLM 的系统 Prompt 均已纳入 `PromptRegistry`，
> 稳定 key 见下表；系统默认模板位于 `app/ai/prompt_defaults.py`。
>
> 运行时变量由 `app/ai/prompts.py`（Context Builder）动态生成，
> 用户可编辑 Template，但 Runtime Context 由系统注入，二者分离。

## 汇总表

| Prompt Key | 功能 | 角色 | 默认文件 | Runtime variables | UI 可编辑 | 生产调用位置 |
|---|---|---|---|---|---|---|
| `task_review.system` | 未完成原因复核 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/task_review_service.py` |
| `task_review.user` | 未完成原因复核 · 用户提示 | user | `prompt_defaults.py` | task_title, task_description, task_category, task_estimated_minutes, task_priority, task_scheduled_date, task_postpone_count, reason, today_line, output_instruction | 是 | `app/services/task_review_service.py` |
| `planner.system` | 每日规划 · 系统提示 | system | `prompt_defaults.py` | daily_limit | 是 | `app/ai/planner.py` |
| `planner.user` | 每日规划 · 用户提示（多路线/阶段/技能/市场） | user | `prompt_defaults.py` | route_section, context_json, knowledge_evidence_section, skill_priority_section, market_trend_section, long_term_section, output_instruction | 是 | `app/ai/planner.py` |
| `assessment.generate.system` | 验收出题 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/assessment_service.py` |
| `assessment.generate.user` | 验收出题 · 用户提示 | user | `prompt_defaults.py` | knowledge_point_name, knowledge_point_description, num_questions, types, max_questions, max_points, output_instruction | 是 | `app/services/assessment_service.py` |
| `assessment.judge.system` | 验收判题 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/assessment_service.py` |
| `assessment.judge.user` | 验收判题 · 用户提示 | user | `prompt_defaults.py` | questions_json, answers_json, verdicts, levels, output_instruction | 是 | `app/services/assessment_service.py` |
| `summary.monthly.system` | 月总结 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/ai/summary.py` |
| `summary.monthly.user` | 月总结 · 用户提示 | user | `prompt_defaults.py` | stats_json | 是 | `app/ai/summary.py` |
| `route_builder.system` | AI 路线草稿 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/ai_route_service.py` |
| `route_builder.user` | AI 路线草稿 · 用户提示 | user | `prompt_defaults.py` | route_context_json, route_skills_section, market_section | 是 | `app/services/ai_route_service.py` |
| `route_suggestion.system` | 候选技能关联路线 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/ai_route_service.py` |
| `route_suggestion.user` | 候选技能关联路线 · 用户提示 | user | `prompt_defaults.py` | candidate_name, routes_json | 是 | `app/services/ai_route_service.py` |
| `jd_parse.system` | JD 结构化解析 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/jd_service.py` → `build_default_parse_ai` |
| `jd_parse.user` | JD 结构化解析 · 用户提示 | user | `prompt_defaults.py` | jd_text, output_format | 是 | `app/services/jd_service.py` → `build_default_parse_ai` |
| `resume_material.system` | 简历素材整理 · 系统提示 | system | `prompt_defaults.py` | 无 | 是 | `app/services/learning_outcome_service.py` |
| `resume_material.user` | 简历素材整理 · 用户提示 | user | `prompt_defaults.py` | outcomes_json, output_format | 是 | `app/services/learning_outcome_service.py` |

## 是否还有裸字符串 Prompt？

- 生产代码中**没有**未纳入 Registry 的系统预制 Prompt。
- 例外（**有意不纳入**，因为它们是用户实时输入内容，不是系统预制 Prompt）：
  - 用户填写的「未完成原因」（`NotDoneDialog` 输入）；
  - 用户编辑的学习成果正文 / JD 原文 / 路线目标；
  - 这些内容作为 Runtime Context 变量注入模板，而不是模板本身。

## 三个层次（严格区分）

| 层次 | 内容 | 是否用户可编辑 | 存放位置 |
|---|---|---|---|
| A. Prompt Template | 提示词骨架 + `{{变量}}` | ✅ 是 | 默认：`prompt_defaults.py`；覆盖：`prompt_overrides` 表 |
| B. Runtime Context | route_name / phase / available_topics / mastery / market / stats / questions … | ❌ 系统动态生成 | `app/ai/prompts.py` 的 `build_*_vars` |
| C. Final Rendered Prompt | A 用 B 渲染后的最终文本 | ❌ 只读预览 | `PromptRegistry.render` / `PromptPreviewService` |

## 动态学习路线不会被写死

- Planner 的路线信息（route_name / route_goal / available_topics）来自
  `PlanningContext`（运行时由 `DailyPlannerService.build_context` 注入），
  默认模板中只有 `{{route_section}}` 变量，不包含任何具体路线名。
- 默认 Planner 系统提示中的规则是通用规划原则 + 边界说明，不含
  “你正在学习搜广推 + LLM”之类的硬编码路线。
