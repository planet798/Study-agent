# Agent-9/10 — Explicit Approval for Application Actions

The model may **request**, but cannot authorize or execute, a Study-Agent application mutation. The fixed actions are `request_complete_current_task`, `request_start_assessment`, and `request_save_learning_note`. Each creates a pending approval tied to a persisted assistant Tool Call; its result (`approval_required`, ID, `pending`) does **not** execute the action. The Workspace renders application-authored text with separate **拒绝** and **批准** buttons. Chat text, model assertions of consent, and prior approvals never authorize execution. There is no “always allow”, automatic approval, bulk approval, or remembered preference.

## Trust boundaries

- Native learning tools and MCP tools remain read-only. External MCP servers cannot register application write tools.
- Sandbox mutations remain confined to the current Task workspace. A Sandbox artifact is not a completed Task, Assessment, Mastery, Capability, or Practice Evidence.
- `AgentToolRegistry()` remains read-only. Only an explicitly composed local Approval Provider adds the `approval` mutation scope, *after* Native → MCP → Sandbox; it preserves the Sandbox scope without granting MCP write permission. Approval Tools can write only Approval metadata, not business state.
- A real application write happens **only** after a Workspace button click: worker-owned fresh SQLite connection → `AgentApprovalService.approve_and_execute()` → canonical `TaskService.complete_task()`, `AssessmentService.start_assessment()` (or pending-attempt resume), or `LearningOutcomeService.create_learning_note_for_task()`. Task completion and Assessment startup do not imply an Assessment pass or Mastery increase.

## Persisted authorization and replay safety (schema v24)

`agent_approval_requests` references Session, Task and assistant message, stores a bound `tool_call_id`, fixed `tool_name`, status, decision/execution timestamps and controlled failure code; it stores no arguments, model reasons, Task title, or message content. `(session_id, tool_call_id)` is unique. `(session_id, tool_name)` is unique while pending, so a repeated empty-argument request reuses the same card. A Note request reuses its pending card only for canonical-equivalent arguments; different content yields `approval_action_already_pending`. The repository binds Session/Task, assistant message, Tool Call ID/name, and JSON-object arguments without copying the payload. The Service validates the persisted call again at execution and derives the bounded UI preview from it. Unknown actions and cross-Session calls fail closed.

`agent_approval_events` is append-only authorization history. Request/event insertion and each status/event transition use a single transaction. Legal chains:

```text
requested(agent) → rejected(user)
requested(agent) → approved(user) → executed(system)
requested(agent) → approved(user) → failed(system)
```

Approval Request status is a current-state projection; `consistency_problems()` / release verification check its chain. Events are never updated or deleted by production code. Requests and Events are `HISTORY_TABLES`: v6 fingerprint protects request identity (`id/session/task/assistant/tool call/tool name/requested_at`) and every Event field; lifecycle fields are mutable but their transitions remain auditable in immutable Events. Old v1–v5 snapshots remain compatible. Trace/Evaluation remain derived growth-only telemetry.

Approval rechecks Task status at execution time. `active` calls canonical completion; `done` resolves as `executed/already_done` without rerunning side effects; `not_done`/`cancelled` resolves as `failed/task_state_changed`. Exceptions become `failed/execution_failed`, never raw exception text. Repeating an already executed approval is idempotent and does not call completion again. Reject never calls TaskService completion.

## Conversation / Trace / UI

The pending Tool result remains immutable in `agent_messages`. The later user decision is recorded in Approval Events, **not** by updating that Tool message or appending another Tool result. Approval execution occurs outside the Agent turn and never rewrites its completed Trace/Evaluation. The Trace of the *request* records only `tool_kind=approval`, safe Tool name, outcome and duration; Evaluation v2 adds `approval_tool_calls`, without treating a successful request as Task-completion success. Approval tables contain no prompts, Tool arguments, user text, or remote output.

After approval/rejection, Workspace and Today refresh, but no old Agent turn resumes and the Session is not closed. Assessment approval may call the configured AI Profile to generate questions; it never submits answers or judges Mastery. The next ordinary user message reads canonical updated Task Context. Pending approval does not block continued conversation. There is no Approval Settings or new Sidebar page.

## Agent-10 actions

Assessment requests require an active Task with a Knowledge Point. Approval rechecks that state and reads `AssessmentService.get_pending_attempt_for_task()` before generation; existing pending attempts are reused without another AI call. Otherwise the worker calls `start_assessment()` and opens the existing `AssessmentDialog` only while the originating Workspace Session remains current. Closing the dialog leaves an unanswered pending attempt; only later user submission can affect Mastery. Failure stores a controlled code, never AI error text or question content.

Note requests require canonical stripped title (1–120 chars) and content (1–8000 chars), no extra fields. The Service creates a plain-text preview capped at 500 chars from the bound immutable Tool Call. Approval revalidates that call and saves one `LearningOutcome(kind=note)` with `derive_git=False`, `task_id=None`, and KP/Topic links. NULL Task linkage prevents later Task-completion outcomes from overwriting the note. No Mastery, Capability evidence, Task status, or filesystem write results from saving a note.

Versions remain `SCHEMA_VERSION=24`, `FINGERPRINT_VERSION=6`, `EVALUATOR_VERSION=2`. No Assessment submission Tool, arbitrary note editor, natural-language approval, remembered permissions, or MCP write capability is provided.
