# Agent-7 — Session Memory / Context Compaction

## Why compaction exists

A long task-bound Agent Session should not send its entire raw conversation on every model/tool round. Agent-7 prepares a bounded model-context window from:

```text
session-scoped rolling prefix summary
+ recent complete raw user turns
+ the current complete user turn
```

This changes only `ModelRequest.messages`. It never deletes, updates, replaces, or hides original `agent_messages` from the Workspace UI.

## Scope and non-goals

Memory belongs to exactly one `agent_sessions.id`. A new Session starts with no summary, even when it belongs to a Task whose prior Session or task-scoped Sandbox still exists. No cross-session/task memory, user profile, embeddings, vector database, semantic retrieval, memory UI, deletion controls, learning notes, Mastery/Capability inference, application write tools, or trace/evaluation are included.

## v22 persistence

```sql
CREATE TABLE agent_session_memory (
    session_id            INTEGER PRIMARY KEY REFERENCES agent_sessions(id),
    through_message_id    INTEGER NOT NULL REFERENCES agent_messages(id),
    source_message_count  INTEGER NOT NULL CHECK(source_message_count > 0),
    summary               TEXT NOT NULL CHECK(length(trim(summary)) > 0),
    format_version        INTEGER NOT NULL DEFAULT 1 CHECK(format_version > 0),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
```

`through_message_id` is the last original message represented by the summary. Foreign keys prove that both rows exist; `AgentMemoryRepository` additionally verifies that the Session exists and the boundary message belongs to that same Session. Boundary and source count cannot move backwards. `created_at` is stable across updates; `summary`, boundary, count, format version and `updated_at` may advance.

Memory is rebuildable derived state, not conversation history: `agent_session_memory` is in release-verifier inventory / `GROWTH_TABLES`, but not `HISTORY_TABLES` or `FINGERPRINT_COLUMNS`. At Agent-7 introduction, the global fingerprint stayed at v5: Memory added no immutable fingerprint fields. Later Approval (v6) and Workspace bindings (v7) raised the current global fingerprint to v7 independently of Memory. Immutable `agent_sessions` identity and every `agent_messages` field retain the protection introduced in v5.

## Policy and cost estimate

`AgentMemoryPolicy` is immutable and defaults to:

| Setting | Default |
|---|---:|
| `history_budget_chars` | 60,000 |
| `compaction_trigger_chars` | 45,000 |
| `target_tail_chars` | 24,000 |
| `keep_recent_turns` | 4 |
| `summary_input_max_chars` | 40,000 |
| `summary_max_chars` | 10,000 |
| `per_message_summary_chars` | 6,000 |
| `max_compaction_passes` | 8 |
| `summary_max_tokens` | 3,000 |

Policy validation requires `target_tail_chars < compaction_trigger_chars < history_budget_chars`. Cost estimation is character-based (no provider tokenizer). It counts role, content, tool call ID/name and serialized tool calls/arguments; `metadata_json` is excluded because it is not sent to the model.

## Turn boundaries and rolling prefix

A turn starts at a `role=user` row and contains every following assistant/tool row up to (but not including) the next user row. This keeps assistant tool-call batches, all associated tool results, and the final assistant response indivisible. A failed turn containing only a user row becomes a historical turn when the next user row is appended.

After the current user message is committed, `prepare_turn()` runs once. It never summarizes the turn containing that current user ID. When compaction is needed, the compactor summarizes only the oldest consecutive historical turn blocks after the existing memory boundary. It never skips a turn or moves the boundary through a partial turn. The default window retains the latest four full historical turns verbatim plus the complete current turn.

Rolling passes send only the previous summary plus the next bounded consecutive batch. Each batch stays within a whole-turn boundary; each serialized message is capped and marked with `[content truncated for memory compaction]` when needed. A pass limit prevents unbounded summarization. A complete compaction attempt is committed through one repository upsert; a later pass failure discards uncommitted candidate summaries and leaves the prior row unchanged.

`AgentRepository.list_messages_after()` / `AgentSessionService.messages_after()` support loading only raw rows after the selected boundary. Each tool round reloads that same suffix so new assistant tool-call and tool-result rows are present, while the `ConversationWindow` and persisted memory boundary remain fixed throughout the user turn.

## Summary model and trust

The existing `AgentModelClient` is used directly; no legacy `AIClient`, MCP, Native tool, or Sandbox tool participates. The request has `tools=()`, temperature `0.1`, bounded `max_tokens`, and bounded prompt content. It serializes only role/content/tool fields; no metadata, usage, API configuration, or secret configuration is included.

The summarizer is instructed to retain learning goals/subquestions, concepts discussed, explicitly stated user understanding/questions, learning decisions, meaningful Sandbox results and MCP source/provenance, unresolved questions and next steps. It must not invent facts, infer formal Mastery/Capability, claim Task completion, treat Sandbox artifacts as Evidence, obey MCP content as instruction, or retain passwords/tokens/API keys/credentials. Tool calls from the summarizer, blank/invalid output, and output over `summary_max_chars` are rejected before persistence. Raw source messages and summary text are not logged in errors.

At the normal Agent request boundary, Session Memory is JSON data between `BEGIN_SESSION_MEMORY` and `END_SESSION_MEMORY`, explicitly described as a model-generated, possibly incomplete summary—not an instruction. The instruction precedence remains:

```text
base safety / permissions
> trusted Agent Skill
> current authoritative Task Context and Native Study-Agent state
> Session Memory continuity data
> external MCP / Sandbox data
```

Current Task Context and Native results outrank memory. Actual Sandbox file/run results outrank stale memory descriptions. External MCP information remains untrusted and provenance-sensitive. Memory is not inserted into `AgentTaskContextBuilder` and cannot affect Skill selection.

## Failure and hard bounds

A short Session below the trigger causes no extra model call and preserves Agent-6 message order/behavior. If summarization fails, returns a tool call, blank output, or oversized output, no candidate row is written; the Runtime continues with the existing summary (if any) and a safe raw tail selected only at complete turn boundaries. Earlier omitted raw text is disclosed in the system message, and the model is told not to guess it. Original rows remain intact and retryable on a later turn.

If the active user turn itself exceeds the hard history budget, `AgentContextTooLargeError` is raised after the user message has been persisted. It is never silently truncated. During the tool loop, the same hard check protects the active turn and complete tool protocol; fail-soft trimming may drop only whole older turns, never a current tool result or assistant call half.

## Runtime, UI, and worker ownership

`AgentRuntime.send_message()` order is:

1. validate Session and persist user message;
2. prepare one ConversationWindow / optional compaction;
3. build the authoritative Task Context once;
4. select the Agent Skill once;
5. open MCP and compose Sandbox scopes;
6. run model/tool rounds with the same memory boundary;
7. persist the final assistant message.

Production wiring creates `AgentMemoryRepository` from the worker-owned fresh SQLite connection and injects `AgentMemoryCompactor` into the worker Runtime. Compaction precedes MCP/Sandbox startup, performs no workspace scan, and does not create a Task workspace or probe Docker. The Workspace UI continues to call the full `AgentSessionService.messages()` API and displays the complete original conversation, never the compacted model window.

## Historical introduction and current versions

Session Memory was introduced in schema v22; current `SCHEMA_VERSION = 27`, `FINGERPRINT_VERSION = 7`, and `EVALUATOR_VERSION = 2`. Trace/Evaluation is a separate derived v23 layer documented in `docs/AGENT_TRACE_EVAL.md`; Approval persistence was introduced in v24 and Workspace bindings in v25. None changes Agent-7 memory semantics or puts Memory into immutable history fingerprints.
