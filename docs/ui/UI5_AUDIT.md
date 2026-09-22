# UI-5 Final Visual Audit

> 基线 `5ae2069`。仅记录真实观察到的问题；不做凭感觉的大改。

## P0（必须修）
1. `tests/test_ui4_pages.py` 恒真断言 `assert ... is False or True` → 已改为真实 empty/populated 验证。
2. `PracticeProjectsPage` empty state 无 action → 已补 `SAButton("新建项目")` 触发现有 `_on_create`。
3. Practice Detail 的 Scope/Readiness/Milestones/Outputs/Evidence 仍是裸 `SectionTitle + QLabel`
   → 已统一为 5 个 `SACard`（Overview / Scope / Readiness / Milestones / Outputs / Evidence）。

## P1（已处理）
- Readiness 状态此前用 `✓/⚠` → 改为文字“已满足 / 能力缺口”。
- Evidence 此前只用 `✓ 已在真实项目中使用` → 改为“已确认项目使用证据”。
- Monthly 此前 8 行文本 → 4 核心 `SAStatCard` + secondary；时间用小时/分。
- Settings 无 AI service 时整个页面不可用 → 改为始终可进入，仅对应 panel unavailable。
- Practice 旧文案“项目不参与能力等级判定”与 Phase 5/6 不符 → 已替换。

## P2（保持，future）
- `QMessageBox` / `QInputDialog` 仍为系统原生，无法完全 Fluent 化（Qt 限制）；语义与流程正确。
- 少量 `setFixedHeight`（描述输入框）保留为 Qt-specific geometry。
- 6 个本地 Fluent-style icon 保留（上游资产暂不可靠获取）。
- Today/Practice scroll-restore timer 未改动（有测试保护，稳定性优先）。
- 未引入 golden-image 测试（跨平台字体/DPI 脆弱）。
