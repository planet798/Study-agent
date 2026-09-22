# Monthly Review Design (UI-4)

> 轻量学习复盘，不是 BI dashboard；无 chart library。

## Layout
Month Navigator（`SAIconButton` CHEVRON_LEFT/RIGHT，无 `<`/`>`）· 日期范围
→ 4 个核心 `SAStatCard`（完成任务 / 完成率 / 实际学习时长 / 学习天数）
→ secondary stats（总任务 / 完成 / 延期 / 预计时长 / 最长连续，`SACard`）
→ 分类排行（每行 `SAProgressBar` = 已有真实 completion rate + `SAStatCard`-style meta）
→ 学习路线（done/covered/assessment/mastered/review/weak；**不硬算 route mastery %**）
→ 项目能力证据（只计数 + Topic 名，**不平均 Capability**）
→ AI 月度解读。

时间统一 `_h_mm()`（小时/分）。空月份显示“本月暂无学习记录”。

## AI Insight
`SACard` + `SASectionHeader`。production 文案为月度中性：overview / strengths /
主要问题 / 本月不足 / 建议 / 后续重点 / 下月重点。
**不再出现“本周主要问题”“下周重点”**（weekly 已移除）；legacy JSON 的
`problems` / `next_week_focus` 仅做中性映射，不改 Prompt schema。
AI 不可用 → `SAInfoBanner(info)`“AI 解读暂不可用，本地统计仍可正常查看。”
