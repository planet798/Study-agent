# Product Baseline — after S1–S6, Agent-11, Workspace-1 and Learning Shell-1

> Canonical product boundary. Product simplification is complete; do not continue product removal. Agent implementation status is recorded below; future capabilities are explicitly marked not implemented.

## Position and surfaces

Study-Agent is an agentic learning environment, not a generic Todo manager, review scheduler, career dashboard or generic chatbot. The static Sidebar navigation has **Today**, **Learning Routes**, **Practice**, and **Settings** (footer), plus a dynamic **学习会话 / Learning Sessions** section. Session entries open the internal Agent Workspace; they are not a generic static Agent `PageKey` page.

- **Curriculum**: six canonical learning routes, Route → Phase → Topic → Learning Component → Task; route ownership, isolation and prerequisites remain authoritative.
- **Planning**: Planner hard gates select legal work; Global Scheduler allocates routes with existing budget/fairness semantics.
- **Today**: execution-focused learning surface: date, pending count, estimated minutes, route filter, current phase, Planner status/explanation, 今日学习 tasks and manual learning entry. It does not display JD/Skill dashboard data.
- **Assessment / Mastery**: completed task ≠ mastered. Formal Mastery updates happen only through Assessment; weak points and latest assessment remain evidence for learning decisions.
- **Capability**: Task / Assessment / Experiment / Project evidence contributes according to existing rules. Capability and Mastery remain separate; L5 PROJECT requires confirmed qualifying project-use evidence.
- **Practice**: separate projects, milestones, outputs, requirements/readiness and evidence. Settings defaults to Personalization (Agent Instructions and manual local memory management), followed by Model/API and Advanced. AI Profiles, internal Prompt overrides (under Advanced) and the theme system remain active; personalization is persisted but not yet injected into Agent turns, and no automatic memory extraction exists. TaskReviewService (AI review of the user's *unfinished-task reason*) remains active; it is not the retired Review Scheduler. Obsidian notes remain available.

## Manual learning

| Type | Storage | Outcome |
|---|---|---|
| Learning Activity | `source=manual`, `task_type=manual`, no KP | One-off study behavior; no Assessment, Mastery or Capability evidence |
| Knowledge Learning | `source=manual`, `task_type=new`, Topic or route-scoped temporary KP | Assessment-capable; Assessment is the formal Mastery path |

The new UI requires a route when active learning routes exist. If none are available, NULL is allowed. Historical `task_type=manual, route_id=NULL` rows remain readable and actionable; `create_todo()` is a legacy compatibility alias, not a product entry point.

## Planner inputs and backend signal

Curriculum, Mastery/weak-point evidence, Capability/Practice requirements, Planner feedback and JD market signals can influence legal planning. JD samples → `JdSummaryService` → `MarketSignal` → `SkillService` → Planner. JD maintenance CLI (`add-jd`, `add-jd-summary`, `jd-trends`) remains available; this data is not a Today dashboard.

## Retired product surfaces

Daily Review / Review Scheduler / Daily Retention / generated review tasks; Monthly Dashboard / SummaryService / StatsService / AI Monthly Summary; Today Career Dashboard / JD Trend / Candidate Skill / Skill Gap panels; Generic Todo Manager. No new scheduled review task, Monthly page or generic Todo UI is part of the current product.

## Legacy DB and migration compatibility

`SCHEMA_VERSION = 27`; `FINGERPRINT_VERSION = 7` (v6 protects Approval request identity and immutable user authorization events; v7 protects Task Workspace bindings, including local path and creation time; Session Memory and Trace/Evaluation remain derived growth-only telemetry). `EVALUATOR_VERSION = 2`. Do not drop or rewrite historical `review_schedule`, `knowledge_points.review_count/next_review_date/interval_days`, `weekly_summaries`, `monthly_summaries`, historical `task_type=review` / `source=daily_retention`, manual `task_type=manual` / NULL-route rows, or historical Monthly prompt overrides. Migration Gate / Release Verifier protect historical rows and fingerprints. Legacy data is not a production feature.

## Agent core — Agent-1 through Agent-11 implemented

Planner decides **WHAT** to learn. Agent Runtime helps the user actually learn that task:

```text
Task → Agent Study Session → persistent multi-turn messages
     → AgentRuntime → AgentModelClient → OpenAI-compatible chat completion
     → content-free operational Trace → deterministic Evaluation
```

Implemented:

- **Agent-1**: task-bound session persistence, immutable multi-turn messages, independent `AgentModelClient`, and persistent Runtime.
- **Agent-1.1**: verifier v5 protects immutable Agent Session/Message history while allowing Session lifecycle changes and legitimate new conversation rows.
- **Agent-2**: read-only `AgentToolRegistry` and six task-scoped learning tools; Runtime persists assistant tool calls/results, reconstructs history, and limits tool rounds.
- **Agent-3**: Today task cards open/resume the task-bound internal Workspace. Each user turn gets one compact Service-backed Task Context snapshot; model turns run in `AgentTurnWorker` with a worker-owned SQLite connection. Workspace is an internal stack page, not a static Sidebar `PageKey`/`PageSpec`; Learning Shell-1 adds dynamic Session navigation.
- **Agent-4**: static Agent learning-behavior Skills are selected deterministically from `learning_activity_kind`; they grant no permissions and are not persisted.
- **Agent-5**: optional official-SDK MCP Tools for operator-configured external servers, gated by exact local allowlist plus `readOnlyHint=True`; transport/client lifecycle is per worker turn and no external integration can mutate Study-Agent state.
- **Agent-6**: Bounded relative-path file tools and optional Docker-only execution. Workspace-1 gates them behind an explicit Task binding: none exposes no file tools; managed supports list/read/write/mkdir; local project supports list/read only. Application state and Native/MCP permissions remain read-only.
- **Agent-7**: Session-scoped rolling conversation summary plus recent complete turns. Original `agent_messages` remain append-only and the UI continues to display full history. Summary is derived, untrusted continuity data; it cannot update learning evidence or application state.
- **Agent-8**: Content-free per-turn Trace/events and deterministic protocol Evaluation (`pass` / `warn` / `fail`). Trace never duplicates conversation/tool payloads; Evaluation uses no LLM and does not assess answer quality or learning outcomes. Trace/Evaluation failures are fail-open.
- **Agent-9**: Local approval-request Tool for Task completion; explicit Workspace approval runs canonical `TaskService.complete_task()` in a worker-owned connection. Append-only authorization events are protected history. No natural-language approval or auto-resume.
- **Agent-10**: Adds fixed request-only Tools for formal Assessment start/resume and bounded LearningOutcome note save. Approval executes through canonical Services on a fresh worker connection. Assessment startup does not judge answers or change Mastery; Note save creates no Capability evidence and never occupies the Task-completion outcome link.
- **Agent-11**: Agent Session production UX adds static capability status, safe errors, Settings shortcut, Ctrl+Enter, bounded input, in-flight serialization and resilient reload/shutdown. Offline `agent-diagnostic` reports read-only local health without external calls.
- **Workspace-1**: Task-scoped user-selected managed or local project binding appears in a compact conversation-header selector/menu. Managed file tools work with safe defaults even without `sandbox.json`; local projects are read-only and sensitive paths are denied. The model receives no host path and cannot switch bindings. Saving a Study-Agent Learning Note remains an approved SQLite action, distinct from writing an explicitly requested Workspace file. See `docs/WORKSPACES.md`.

- **Learning Shell-1**: Active Agent Sessions remain available independently of origin Task active/done/not_done/cancelled status. The Sidebar shows all pinned active non-archived Sessions, then the latest 10 unpinned active non-archived Sessions by `updated_at DESC`, retaining the currently open unarchived Session if needed. Opening is by `session_id`, loading its immutable origin `task_id`, full messages, approvals and Task Workspace binding; leaving the Workspace never closes the Session. Busy turns/approval execution prevent switching to a different Session. Conversation is the primary workspace, with compact metadata/Workspace controls and a bottom composer.

- **Session Management-1C**: Expanded Sidebar row menus expose user rename/reset, pin/unpin and confirmed archive. Archiving the current Session navigates to Today without closing history. A small archived list supports Restore without automatic opening or re-pin. Management is disabled during Agent turns/approval execution. No Delete UI/API. See `docs/SESSION_MANAGEMENT.md`.

NOT IMPLEMENTED:

- Session search/folders
- Agent Assessment answer submission, arbitrary note editing, local project writes/execution, diff approval, natural-language approval, remembered permissions, MCP application writes, or other arbitrary application writes

Agent Tools must call **existing Service → Repository → SQLite**, never Repository or raw SQLite directly. Agent may neither set Mastery nor Capability directly: **Assessment → Mastery** and **Evidence → Capability**. `AgentSkill` / `AgentSkillRegistry` in `app/agent/skills/` are static learning-behavior strategies; current `SkillService` and `skills` table describe career/technical skills and must not be repurposed. See `docs/AGENT_ARCHITECTURE.md`.
