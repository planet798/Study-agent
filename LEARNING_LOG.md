# Learning Log

## 2026-09-05

### 本次学习
- Git 工作区、暂存区、本地仓库的关系
- git status
- git add
- git diff --staged
- git commit
- git log --oneline
- Git Tag

### Study Agent 当前功能
- GUI
- SQLite
- AI 任务规划
- AI 任务复核
- AI Summary
- 日期注入
- Windows 一键启动
- 305 个测试

### 当前版本
- v0.1.0：Study Agent MVP

## 2026-09-05（长期学习上下文升级）

### 本次新增
- 分析 10 个真实 BOSS JD（华为/蔚来/科大讯飞等）确立职业目标与技能路线
- 新增 `docs/career_context.json`：职业目标 / 目标岗位 / JD 证据 / 技能路线 / 当前能力状态 / 学习原则 / 项目状态（长期学习上下文的单源真相）
- 新增 `app/ai/long_term_context.py`：加载 / 校验 / Prompt 摘要（缺失或非法时优雅降级为旧行为）
- AI Planner 现在把长期学习上下文注入每日规划 user prompt，作为长期依据
- 长期上下文与 LEARNING_LOG.md 分工：上下文=为什么学/岗位/路线；日志=历史

### 技术点
- 单源真相设计（避免多处互相矛盾的路线）
- 文件缺失/JSON 非法/结构错误 → 返回 None，不崩溃
- 新增 12 个测试
