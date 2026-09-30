# Agent-8 — Trace / Deterministic Evaluation

## Purpose and boundary

Agent-8 makes a user turn's **engineering/protocol behavior** inspectable and reproducible:

```text
Agent Turn → structured Trace → deterministic Evaluation
```

It does not evaluate answer quality, teaching quality, or whether a user learned. It never changes runtime decisions, prompts, Skills, Tool permissions, Task status, Mastery, Capability, Assessment, Practice, or Evidence.

`agent_messages` remain the complete, immutable conversation and protocol audit history. Trace is separate operational telemetry. It records event types, timing, outcome, bounded counts/identifiers, memory flags/boundary, and model usage; it does **not** duplicate prompts, user/assistant content, Task Context, Tool arguments/results, MCP descriptions/results, Sandbox paths/files/stdout/stderr/argv, API configuration, credentials, environment values, or exception text.

## v23 persistence

- `agent_turn_traces`: one completed accepted user turn, with Session/Task/user/final-assistant references, success/failure, static Skill key, Memory flags/boundary, model/tool counts, usage, finite error code, wall-clock timestamps, and monotonic duration.
- `agent_trace_events`: ordered `(trace_id, seq)` events for `runtime`, `memory`, `model`, `tool`, `mcp`, and `sandbox`. `seq` starts at 1 and is contiguous. `details_json` is allowlisted and capped at 4096 characters.
- `agent_turn_evaluations`: replaceable deterministic evaluation keyed by `(trace_id, evaluator_version)`.

Trace and its complete event list are inserted in one transaction. There is no `running` trace row or incremental update. A process crash may leave no trace; the committed `agent_messages` remain authoritative. All three tables are derived operational telemetry: verifier inventory / `GROWTH_TABLES`, never immutable `HISTORY_TABLES` or `FINGERPRINT_COLUMNS`. At Agent-8 introduction, Trace/Evaluation added no immutable fingerprint fields and the global fingerprint remained v5. Approval protection (v6) and Workspace binding protection (v7) later raised the current global fingerprint to v7; Trace/Evaluation still does not participate.

## Collection and privacy

`AgentTraceCollector` is pure Python: no SQLite/repositories, Qt, MCP client, Sandbox backend, or business services. It uses `time.perf_counter_ns()` for durations and `now_iso()` for wall-clock timestamps; both clocks are injectable for stable tests.

| Event | Recorded metadata | Excluded data |
|---|---|---|
| `runtime/task_context` | whether the snapshot is available, duration | route/topic/mastery/capability details or text |
| `runtime/skill_selection` | static `skill_key`, duration | Skill prompt or instructions |
| `memory/prepare` | compacted/omitted flags, through-message ID, duration | summary input/output |
| `model/agent`, `model/memory_summary` | purpose, safe model/finish identifiers, request counts/character estimate, Tool definition count, response character count, returned call count, normalized usage, duration | full request/response or any content |
| `tool/<registered_name>` | native/MCP/Sandbox classification, `ok`, finite error code, duration | arguments, result data, error message |
| `mcp/discovery` | validated local server keys and approved-tool count | URL, command/args, environment, remote schema/description/error |
| `sandbox/scope` | file-tool/execution availability and tool count | workspace path, Docker path/image/diagnostics |

Unknown or unregistered model-supplied Tool names are recorded as `unknown_tool`, not copied into the event name. Tool error messages and provider exception strings are never recorded. Event metadata is allowlisted before serialization; oversize or invalid telemetry is discarded rather than forwarded into the Agent request or persisted unboundedly.

### Model usage

Every `AgentModelClient.complete()` call is observed, including Memory summarization. `prompt_tokens` / `completion_tokens` / `total_tokens` and `input_tokens` / `output_tokens` are normalized. Only non-negative integer values count; `bool` is not an integer here. If a provider omits a total but supplies both input and output counts, their deterministic sum is used. Known counts accumulate when another field is absent/invalid; missing or invalid usage sets `usage_complete=false`. Memory calls are a subset of `model_call_count` and their tokens are included in totals.

### Tool and external-scope events

A completed Tool execution has one event, regardless of whether its controlled envelope is successful. The local `mutation_scope=approval` request Tool is classified `approval`, not Native; its event never records approval ID or user decision. `tool_call_count` counts executions; a multi-call model response contributes one `tool_round` and one call count per Tool. Names beginning `sandbox_` classify as Sandbox; `mcp_` as MCP; other registered tools as Native. A controlled error stores only its code and yields a `warn` evaluation when the turn otherwise succeeds.

## Runtime lifecycle and fail-open behavior

A trace collector starts only after Session validation and successful persistence of the user message. Blank/closed input creates no trace. Runtime observes Memory → Task Context → Skill → MCP → Sandbox → the existing model/tool loop. On success, the final assistant row is committed before one completed trace and its events are attempted. On failure, Runtime attempts a failed trace with no final assistant reference and a finite code such as `ai_service_error`, `context_too_large`, `agent_runtime_error`, `memory_error`, or `unexpected_error`, then re-raises the original exception.

Trace insertion and Evaluation are best-effort. If collector creation, trace persistence, or evaluation fails, the Agent result is unchanged: a successful answer still returns (`trace_id=0` if Trace persistence failed); an original business/model exception remains the exception (`trace_id=0` is possible). If Evaluation fails after Trace insertion, the Trace remains and `evaluation_status` is empty. Trace errors, stack traces, or codes are not shown in the Workspace UI. `AgentRuntime(trace_service=None)` retains no-Trace behavior.

## Deterministic Evaluation v1

`AgentTurnEvaluator` consumes only a completed Trace and its events. It has no database, message/session services, AI client, MCP, or Sandbox dependency. Checks include:

- `turn_succeeded` and `final_assistant_persisted`
- `event_counts_consistent` (model/memory/tool counts, rounds, token totals and usage completeness agree with events)
- `tool_protocol_complete` (Agent model-returned Tool-call count agrees with recorded Tool executions)
- `tool_round_limit_respected` (uses the Runtime's configured limit)
- `no_tool_execution_errors`
- `model_usage_complete`

Overall status is `fail` for failed turns or a core invariant mismatch; `warn` when core invariants hold but a controlled Tool error occurred or usage is incomplete; otherwise `pass`. Missing provider usage is not an Agent failure. Metrics are bounded structural counts, durations, memory flags and tokens; there is no numeric score.

There is no LLM-as-a-Judge, quality score, teaching score, mastery inference, automatic retry, prompt optimization, Skill/Registry mutation, or Trace-to-Mastery/Capability flow. The three fixed approval-gated requests (Task completion, Assessment start/resume, bounded Learning Note save) are described in `docs/AGENT_APPROVALS.md`; they do not turn Trace/Evaluation into a business-write authority.

## Versions and UI

Trace/Evaluation tables were introduced in v23. Current versions: `SCHEMA_VERSION = 27`, `FINGERPRINT_VERSION = 7`, `EVALUATOR_VERSION = 2`. Evaluation v2 adds `approval_tool_calls` for the local request-only approval Tools; v1 evaluation rows remain unchanged. Approval execution and user decisions are recorded in separate append-only authorization events, never by rewriting a completed Trace. Production constructs repositories/service from the `AgentTurnWorker`-owned connection. Trace/Evaluation adds no Trace Viewer, token dashboard, Evaluation UI, or static Sidebar page. Learning Shell-1's dynamic Session navigation is separate from telemetry.
