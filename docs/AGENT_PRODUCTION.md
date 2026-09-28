# Agent Platform v1 — Production Operation

## Runtime and dependencies

The Workspace is task-bound. Each accepted turn persists the user message, then a worker-owned SQLite connection builds Session Memory → Task Context → static Skill → Native read tools → optional read-only MCP → task-scoped Sandbox → approval-request tools → model/tool loop → Trace/Evaluation. It does not expose raw Tool JSON, Trace IDs, Skill keys, or token usage to the user. Schema v24, fingerprint v6, evaluator v2 are unchanged by Agent-11.

The AI Profile is configured in Settings; the Workspace's model status is a current local snapshot, **not** a network connectivity guarantee. Its “前往设置” action opens the existing Settings page. Ctrl+Enter sends and Enter inserts a newline. A single message longer than 20,000 characters is rejected without truncation; the complete typed text stays in the composer. Opening/resuming a Session scrolls to the latest message; an appended Assistant answer starts at its first line rather than its end. A same-Session status/approval refresh keeps the current scroll position and the unsent draft. If the user reads older messages during a turn, the answer does not steal scroll position; “新回复 ↓” offers an explicit jump to the latest message.

## Conversation rendering (UX-1)

User messages and approval cards (including note previews) are **PlainText**: Markdown typed by the user is shown literally. Assistant messages are rendered as **safe Markdown** in `app/ui/agent_message_widget.py` using the Qt-native `QTextDocument` importer (`MarkdownNoHTML | MarkdownDialectGitHub`); no WebEngine, JavaScript or third-party renderer is used. Raw HTML in model output is escaped to text (`setHtml(model_output)` is never used), external links are shown but never opened, and `loadResource` is a hard no-op so Markdown can never trigger a network fetch or read `file://` / `qrc://` / local paths. If Markdown parsing raises, the view falls back to PlainText instead of crashing the Workspace.

The database still stores the raw Markdown string; rendering is presentation-only and never mutates `agent_messages`, Session Memory, Trace or Evaluation. The whole conversation keeps a single outer scroll area (the Markdown view has no internal scrollbars, wraps long code/table content and fits its fractional document height plus viewport slack), bubbles align left/right by role with responsive viewport-based widths (assistant ≤ 960 px / 90% viewport, user ≤ 680 px / 78%), and all colours come from the existing Light/Dark semantic tokens.

By default the Agent teaches **interactively** (core answer → minimal example → comprehension check, one main knowledge block per turn) unless the user explicitly asks for a full summary, detailed notes or a systematic walkthrough. This is behavioural guidance in the system prompt / learning Skills, not a hard token cap. `AGENT_SYSTEM_PROMPT` also asks the model not to emit HTML for layout.

## Workspace shell and interaction (UX-2)

The Workspace information hierarchy is: a compact toolbar (`← 返回今日` plus capability chips), a Task Context card (route / activity / duration tags + optional PlainText description), any warning/error surfaces, the conversation, the “待确认操作” approval section (only when pending) and the composer. The Task title has exactly one primary home — the global `SAPageHeader` subtitle set by MainWindow — so the Workspace never duplicates it. Switching Session A → B updates the subtitle immediately; returning to Today restores the Today header.

Capability chips are truthful **configuration** snapshots: `AI 已配置`, `写操作需确认`, `MCP 已配置`, `Sandbox 已启用`, `Sandbox 执行已配置`. They never claim “在线 / 连接正常 / Docker 可用”, never expose the internal read-only application-state architecture, and use no runtime probe. Only invalid optional config or a runtime failure raises a visible warning/error surface.

The composer is an independent `surface` card with a low-weight `Ctrl+Enter 发送 · Enter 换行` hint and a character counter that appears only near the limit (`≥ 16,000`). The busy state is unified: `学习助手正在思考…` for a running turn and `正在执行已确认操作…` while an approved action executes (never two messages at once). Unsent drafts are UI-only memory owned by the current Session: a same-Session status/approval/error reload preserves the draft, while a real Session switch or a successful send clears it.

Pending approvals render in their own “待确认操作” section (hidden when empty). Each card shows an application-owned action title, a static PlainText explanation, an optional PlainText note title/preview, and `拒绝` (subtle) / approve (primary) buttons; approve never auto-focuses and `Enter` never approves.

## Degradation and safe errors

The Workspace capability chips describe configured capabilities, not remote availability. Loading them reads the current local AI/MCP/Sandbox configuration, without MCP discovery, subprocess/network access or Docker probe. Missing MCP/Sandbox config is normal; invalid optional config yields sanitized, static warnings, while Native tools and ordinary chat remain available. Sandbox execution may be configured but unavailable during a turn; its absence does not disable learning chat. Trace or Evaluation persistence failure is fail-open. Memory summarization and optional MCP failures that still produce a successful turn do not show a red error banner.

`AgentTurnWorker` maps exceptions to finite safe UI messages: model unconfigured, model connection failure, context too large, or other runtime failure. It never forwards exception text, HTTP bodies, provider URLs, credentials, Tool results or stack traces. Once persistence is confirmed, the error explicitly says the user message was saved without an assistant answer. Context-too-large does **not** delete history; the user can send a shorter subsequent message. There is no automatic retry or “retry last turn” button.

## Workers and approval serialization

Agent and Approval workers create/close their own SQLite connections in their threads. MainWindow tracks in-flight turns and approvals; opening an already-running Session retains the busy state. One Workspace interaction is active at a time: while an Agent turn runs, approval buttons are disabled; while an application approval executes, approval buttons and chat input are disabled. Other Today tasks remain usable. Rejection is a local user decision, not a model call. Worker results from Session A cannot render into Session B. On real shutdown, workers receive interruption requests and are waited on before references are released. Closing to the system tray keeps the app and workers running as before.

Approval execution is not a second Agent turn. Task completion uses TaskService, Assessment opens an existing pending attempt or generates questions in the approval worker, and Note save creates a LearningOutcome. Only the matching current Workspace Session auto-opens an AssessmentDialog. Failures resolve the approval and show static action-specific messages; approval cards disappear on reload. No Tool Call or conversation row is rewritten. See `docs/AGENT_APPROVALS.md`.

## Offline diagnostic

```bash
python -m app.main agent-diagnostic [--db DB_FILE] [--json]
```

This command runs before GUI construction. It opens an **existing** database read-only, reports schema/fingerprint and whether migration is required, checks only local AI configuration and MCP/Sandbox config syntax, counts actual built-in tool/Skill/approval declarations, and reads Session/Message/pending-approval counts. It never migrates/repairs/creates a DB, calls a model, connects to MCP, probes/runs Docker, creates a Session/Trace/Approval, or prints API credentials, model URL, MCP URL/command/env, filesystem path, notes, conversation or questions. Exit codes: 0 healthy; 1 invalid optional config (degraded); 2 core unavailable (missing/outdated DB or unconfigured AI). Missing/disabled optional features are healthy.

## Troubleshooting

| Situation | Safe next step |
|---|---|
| Model unconfigured | Open existing Settings using the Workspace shortcut; configure the current Profile. |
| Model connection failure | Check configured Profile and network using the existing Settings connection test. No automatic retry. |
| MCP configuration invalid | Correct the local MCP config; Native learning remains available. Opening Workspace does not connect MCP. |
| Sandbox configuration invalid | Correct local Sandbox config; ordinary Agent chat continues. |
| Docker execution unavailable | File tools may still work. Check local operator Docker setup separately; no host-execution fallback. |
| Context too large | Send a shorter next message. Previously persisted history remains intact. |
| Pending approval | Explicitly approve or reject the static card; a chat reply cannot authorize it. |
| Assessment generation failed | Approval resolves as failed; Mastery unchanged. Check model configuration, then explicitly request again. |

Agent Platform v1 is now frozen for real-user dogfooding, bug fixes and UX feedback. New tools, permissions, autonomous loops and schema changes are not part of this hardening phase.
