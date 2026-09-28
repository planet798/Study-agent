# Agent-9 — Explicit Approval for Application Writes

The model may **request**, but cannot authorize or execute, a Study-Agent application mutation. The only v1 action is `request_complete_current_task`. It takes `{}` and creates a pending approval tied to a persisted assistant tool call; its result (`approval_required`, ID, `pending`) does **not** complete the Task. The Workspace renders application-authored text with separate **拒绝** and **批准** buttons. Chat text, model assertions of consent, and prior approvals never authorize execution. There is no “always allow”, automatic approval, bulk approval, or remembered preference.

## Trust boundaries

- Native learning tools and MCP tools remain read-only. External MCP servers cannot register application write tools.
- Sandbox mutations remain confined to the current Task workspace. A Sandbox artifact is not a completed Task, Assessment, Mastery, Capability, or Practice Evidence.
- `AgentToolRegistry()` remains read-only. Only an explicitly composed local Approval Provider adds the `approval` mutation scope, *after* Native → MCP → Sandbox; it preserves the Sandbox scope without granting MCP write permission. The approval Tool can write only Approval metadata, not business state.
- A real application write happens **only** after a Workspace button click: worker-owned fresh SQLite connection → `AgentApprovalService.approve_and_execute()` → canonical `TaskService.complete_task()` with the same Outcome/Capability wiring as Today. Task completion does not itself imply an Assessment pass or Mastery increase.

## Persisted authorization and replay safety (schema v24)

`agent_approval_requests` references Session, Task and assistant message, stores a bound `tool_call_id`, fixed `tool_name`, status, decision/execution timestamps and controlled failure code; it stores no arguments, model reasons, Task title, or message content. `(session_id, tool_call_id)` is unique. `(session_id, tool_name)` is unique while pending, so a repeated request reuses the same card. The repository verifies the Session's Task and parses persisted assistant `tool_calls_json` to bind the ID, name and exact `{}` argument to the assistant message. Unknown actions and cross-Session calls fail closed.

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

After approval/rejection, Workspace and Today refresh, but no old model turn resumes, no API call is made, and the Session is not closed. The next ordinary user message reads canonical updated Task Context. Pending approval does not block continued conversation. There is no Approval Settings or new Sidebar page.

Versions: `SCHEMA_VERSION=24`, `FINGERPRINT_VERSION=6`, `EVALUATOR_VERSION=2`. Other actions (Assessment or learning notes) require separate domain/UI design in Agent-10.
