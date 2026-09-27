# Agent Architecture

> Agent is task-bound learning support, not a generic chatbot, another Planner, Todo manager, or Mastery/Capability editor.

## Long-term learning chain

```text
Learning Route → Phase → Topic → Learning Component → Task
→ Agent Study Session → Task Context + Agent Skill + Session Memory
→ Agent Runtime → Native / MCP / Sandbox Tools
→ Learning Interaction → Assessment / Evidence → Mastery / Capability
```

- Task Context = authoritative current application state; Agent Skill = how to teach; Session Memory = derived conversation continuity. Memory never becomes Mastery, Capability, Assessment, or Evidence.

## Agent-1 — Session and model runtime

```text
Task → AgentSessionService → AgentRuntime → AgentModelClient
     → OpenAI-compatible chat completion
```

- `agent_sessions.task_id` is required; one task has at most one active session.
- `agent_messages` are append-only. Runtime reloads message-id ordered full history when no memory boundary exists; with Agent-7 it reloads only raw rows after one fixed Session Memory boundary.
- Legacy `AIClient` / `DeepSeekClient.chat()` / `AdaptiveAIClient.chat()` / `send_chat_request()` remain unchanged.

## Agent-1.1 — History verification

Verifier fingerprint v5 protects immutable Session identity/title and all Message fields. Session `status`, `updated_at`, and `closed_at` remain mutable lifecycle fields and are deliberately not fingerprinted. New Session/Message rows are allowed growth; prior rows may not be deleted or changed. v1–v4 verifier snapshots remain compatible.

## Agent-2 — Read-only native tools

```text
Task
→ AgentSessionService (resolve active session.task_id)
→ AgentRuntime
→ AgentToolRegistry
→ Agent Tool
→ existing Service
→ Repository
→ SQLite
```

Production package:

```text
app/agent/tools/
├── __init__.py
├── base.py       # AgentToolContext / AgentTool / AgentToolSpec / controlled errors
├── registry.py   # uniqueness, strict argument validation, OpenAI declarations, result envelopes
└── learning.py   # six session-scoped read-only learning tools + factory
```

The six tools are exactly:

1. `get_task_context` → `TaskService`
2. `get_route_context` → `TaskService` → `LearningRouteService`
3. `get_topic_context` → `TaskService` → `StudyPlanService.get_topic/get_phase`
4. `get_learning_components` → `TaskService` → `TopicLearningProfileService`
5. `get_mastery` → `TaskService` → `AssessmentService.get_knowledge_point`
6. `get_capability` → `TaskService` → `CapabilityService.get_current_capability`

All tool schemas are `{type: object, properties: {}, additionalProperties: false}`. Model-provided task/route/topic/KP IDs are rejected. Runtime derives `AgentToolContext(session_id, task_id)` from the persisted Session, not from model arguments. Tools receive injected Services only—no connection, Repository, API key, or AI config.

Registry returns JSON-safe envelopes:

```json
{"ok": true, "data": {}}
{"ok": false, "error": {"code": "invalid_arguments", "message": "..."}}
```

Unknown tool, invalid JSON / non-object / extra arguments, and handler exceptions become controlled results; no traceback or internal exception detail is shown to the model. Missing learning context is a successful `available: false` data result.

## Persistent tool loop

```text
persist user
→ model.complete(messages, tools)
→ assistant tool-call message (canonical provider-neutral JSON)
→ execute each call in order
→ persist tool result (`role=tool`, call id + name + JSON content)
→ reload complete raw turns after the fixed memory boundary and call model again
→ final assistant message
```

Assistant tool calls are reconstructed from `tool_calls_json` into `ModelMessage.tool_calls`; tool rows reconstruct `role`, `tool_call_id`, and `name`. Recreating `AgentRuntime` does not lose conversation state. The loop is capped at four tool rounds; over-limit calls are not executed. Existing Agent-1 behavior remains when `tool_registry=None`: request has `tools=()` and unexpected provider tool calls fail safely.

Agent-2 tools do not mutate Task, Assessment, Mastery, Capability, Evidence, Practice, or other business state. Assessment remains the only Mastery path; qualifying Evidence remains the Capability path.

## Agent-3 — Task Context and task-driven Workspace

```text
Today active Task → [开始学习]/[继续学习]
→ AgentSessionService.start_or_resume(task_id)
→ internal Agent Workspace (Sidebar remains Today)
→ AgentTaskContextBuilder snapshot + persistent AgentRuntime
→ AgentTurnWorker with fresh worker-owned SQLite connection
```

- `AgentTaskContextBuilder` reuses the six Agent-2 Registry tools to create one compact, read-only snapshot per user turn. It writes no tool-call/result history; actual model-requested tools remain persisted by the Runtime.
- Runtime injects the JSON snapshot inside explicit delimiters and instructs the model to treat its text as application data, not system instructions. Free-text fields are truncated at 2000 characters.
- Workspace is an internal stack page appended after formal pages, not a `PageKey`, `PageSpec`, or Sidebar item. Returning to Today does not close the Session.
- `AgentTurnWorker` receives only database path, runtime factory, session id, and user text; factory builds all SQLite-backed dependencies from a fresh connection inside `run()`. Worker outcome reloads persisted history; failure preserves the committed user message. Stale completions never render into another Session.

## Agent-4 — Static learning behavior strategies

```text
Task Context.task.activity_kind
→ AgentSkillSelector (deterministic, no DB/model call)
→ AgentSkillRegistry → trusted AgentSkill instruction
→ AgentRuntime system message
```

- `app/agent/skills/{base,registry,selector,learning}.py` contains six immutable built-ins: `general-study`, `teach-concept`, `code-reading`, `experiment-coach`, `interview-drill`, `practice-coach`.
- These are static application behavior configuration, not database entities, Tools, Career Skills, user-managed prompts, or permissions. Selection uses only the current turn's Task Context snapshot and happens once per user turn; one tool loop reuses the selected Skill.
- Prompt order is base safety/read-only policy → trusted Agent Skill (`BEGIN_AGENT_SKILL`) → authoritative untrusted Task Context JSON (`BEGIN_TASK_CONTEXT_JSON`) → derived untrusted Session Memory JSON (`BEGIN_SESSION_MEMORY`) → conversation. Skill does not add tools or grant permissions; selected key is returned in `AgentTurnResult` but not persisted.
- Code/experiment/practice instructions conditionally allow only currently exposed `sandbox_*` capabilities; they never claim host repository/filesystem access.

## Agent-5 — Optional read-only MCP Tools

```text
per-user-turn MCP scope (AgentTurnWorker thread)
→ official MCP Client (stdio / Streamable HTTP)
→ list_tools pages
→ local exact allowlist AND annotations.readOnlyHint is True
→ namespaced MCPAgentTool
→ effective Registry = Native tools first + approved MCP tools
```

- `app/agent/mcp/{config,client,tools,provider}.py` reads operator-local `data/mcp_servers.json` (or `STUDY_AGENT_MCP_CONFIG`). Missing config means MCP disabled; no MCP database tables or Settings UI.
- Official `mcp>=2,<3` SDK is used for transports/protocol negotiation. One private asyncio loop/AsyncExitStack and connected Clients live across discovery and all MCP tool rounds for one user turn, then close; no global client, SSE transport, or `asyncio.run()` per tool call.
- Every external tool requires both an exact local `allowed_tools` entry and `readOnlyHint=True`. Wildcards and mutation tools are rejected. Names are namespaced (`mcp_<server>_<tool>`); collisions are skipped. Remote descriptions/schema/results are untrusted external data.
- MCP Resources/Prompts/etc. are ignored. Results are provenance-tagged, JSON-safe and bounded; image/audio/binary content is described, not base64-forwarded. Server failures degrade to Native tools for that turn.
- Task Context remains Native-only and authoritative; Agent Skill remains selected once from that same Task Context and grants no permission. MCP never changes the Skill or Native read-only policy.

## Agent-6 — Task-scoped Sandbox

```text
AgentToolRegistry(default: read-only)
→ explicit allowed_mutation_scopes=("sandbox",)
→ SandboxProvider(task_id from AgentToolContext)
→ data/agent_workspaces/task_<id>/
→ optional Docker-only sandbox_run
```

- `AgentToolSpec` adds `mutation_scope`; read-only tools must leave it empty. The default Registry remains read-only. Only the Sandbox composition Registry may authorize the sole supported mutation scope, `sandbox`; application/database/task/mastery/capability scopes are not supported.
- `app/agent/sandbox/{config,workspace,backend,tools,provider}.py` uses a local operator config (`data/sandbox.json`, `STUDY_AGENT_SANDBOX_CONFIG`). Missing/invalid config disables Sandbox without affecting Native/MCP tools. Workspace roots derive only from integer `task_id`, persist across Sessions, and are created lazily on an actual Sandbox tool call.
- Fixed tools: `sandbox_list_files`, `sandbox_read_file`, `sandbox_write_file`, `sandbox_make_directory`, optional `sandbox_run`. Paths are relative, traversal and symlink/junction escape are rejected, text/results are bounded, writes are atomic, and there is no delete tool.
- `sandbox_run` is exposed only when Docker is configured, reachable, and its image already exists locally. It uses a fixed argv-list Docker CLI, `shell=False`, `--network none`, only the current Task workspace bind mount, dropped capabilities, no-new-privileges, read-only container root, resource limits, timeout, bounded output, and container cleanup. Docker failure has no host subprocess fallback.
- Sandbox never imports application Services/Repositories/SQLite and cannot update Task, Mastery, Capability, Assessment, Practice, or Evidence. Task Context stays Native-only and Skill selection stays once-per-turn.

## Agent-7 — Session Memory / Context Compaction

```text
append-only full agent_messages
→ AgentMemoryCompactor.prepare_turn() exactly once, after persisting current user
→ optional rolling prefix summary + recent complete turns + current turn
→ fixed ConversationWindow for every model/tool round
```

- `app/agent/memory/{policy,compactor}.py` uses immutable character-based `AgentMemoryPolicy`, `estimate_message_chars()`, `AgentSessionService.messages_after()`, `AgentMemoryRepository`, and the existing `AgentModelClient`. No tokenizer, embeddings, vector DB, retrieval, cross-session or cross-task memory.
- `agent_session_memory` (v22) is one derived row per Session. It stores a summary of the original prefix through a same-Session message ID, source message count, format version, and timestamps. Rolling updates preserve `created_at` and move the prefix forward; the table is in verifier `GROWTH_TABLES` / inventory only, never `HISTORY_TABLES` or v5 fingerprints.
- The compactor groups at each `role=user` boundary. It only summarizes consecutive oldest complete turns; tool-call assistant rows, every associated tool result, and final assistant rows travel as one atomic turn. The active user turn is never summarized. Default retention is four full historical turns plus the active turn.
- Summary requests use the same `AgentModelClient`, low temperature and bounded tokens/input/output, always `tools=()`. They receive only role/content/tool fields (bounded per message); `metadata_json` is excluded. Invalid, blank, oversized, tool-call, or failed responses are not persisted. Multiple successful batches are committed as one memory upsert, so a later pass failure leaves the prior memory unchanged.
- If summarization fails, the Runtime uses the previous summary (if any) and a safe whole-turn recent tail, marks earlier raw text omitted, and preserves every original row. `AgentContextTooLargeError` is raised only when the active indivisible turn cannot fit; the already committed user message remains in SQLite.
- System prompt encloses summary JSON in `BEGIN_SESSION_MEMORY` / `END_SESSION_MEMORY`, labels it derived, possibly incomplete data rather than instructions, and gives current Task Context / Native state priority. Sandbox actual results and external MCP trust rules remain explicit. Compaction happens before Task Context, Skill, MCP, and Sandbox lifecycle; it never scans workspace files or starts external tools.
- Runtime reloads rows after the fixed raw boundary on each tool round, so new current-turn assistant/tool protocol rows appear without moving the memory boundary. `AgentWorkspacePage` continues to load and render the complete original message history.
- No memory UI, controls, cross-Session import, Mastery/Capability inference, or application writes.

## Still not implemented

- write tools, approvals, mutation policy
- trace / evaluation

## Future roadmap

| Stage | Scope |
|---|---|
| Agent-7 | Session Memory / context compaction — implemented |
| Agent-8 | Trace / evaluation |
| Later / separately designed | write tools + approval policy |

## Tests

- `tests/test_agent_tool_registry.py`: registration, duplicate/read-only checks, schemas, errors, strict arguments, JSON safety.
- `tests/test_agent_learning_tools.py`: six Service-backed tools, missing context, mastery/capability evidence, read-only regression.
- `tests/test_agent_tool_runtime.py`: ordered tool loops, error paths, round cap, session isolation, persistence/reconstruction after Runtime recreation, verifier growth.
- `tests/test_agent_task_context.py` / `test_agent_workspace_integration.py`: one context snapshot per turn, no context tool-history writes, task-to-session flow, failure reload, and stale worker isolation.
- `tests/test_agent_workspace_ui.py` / `test_agent_ui_worker.py`: plain-text UI visibility, busy/config state, Sidebar boundary, and worker connection ownership.
- `tests/test_agent_skills.py` / `test_agent_skill_runtime.py`: static strategies, deterministic mapping/fallback, prompt boundary, one selection per turn, and no extra model call.
- `tests/test_agent_mcp_config.py`, `test_agent_mcp_client.py`, `test_agent_mcp_discovery.py`, `test_agent_mcp_tools.py`, `test_agent_mcp_runtime.py`: offline config, official SDK bridge lifecycle, dual authorization gate, bounded results, and Runtime composition.
- `tests/test_agent_sandbox_config.py`, `test_agent_sandbox_workspace.py`, `test_agent_sandbox_permissions.py`, `test_agent_sandbox_tools.py`, `test_agent_sandbox_backend.py`, `test_agent_sandbox_runtime.py`: config, isolation/path protections, scope authorization, Docker command constraints, and end-to-end tool history.
- `tests/test_agent_memory_repository.py` / `test_agent_memory_compaction.py`: repository ownership/monotonicity, turn-safe rolling batches, short-session no-call behavior, tool protocol, bounded inputs, failure fallback, pass limit, hard budget, and Runtime tool-loop reuse.
- Agent-1 session/model/runtime and `tests/test_agent_architecture.py` continue to guard legacy boundaries.
