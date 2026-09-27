# Agent Architecture

> Agent is task-bound learning support, not a generic chatbot, another Planner, Todo manager, or Mastery/Capability editor.

## Long-term learning chain

```text
Learning Route → Phase → Topic → Learning Component → Task
→ Agent Study Session → Agent Runtime → Tools / Skills / MCP / Sandbox
→ Learning Interaction → Assessment / Evidence → Mastery / Capability
```

## Agent-1 — Session and model runtime

```text
Task → AgentSessionService → AgentRuntime → AgentModelClient
     → OpenAI-compatible chat completion
```

- `agent_sessions.task_id` is required; one task has at most one active session.
- `agent_messages` are append-only. Runtime reloads message-id ordered history for each turn.
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
→ reload all history and call model again
→ final assistant message
```

Assistant tool calls are reconstructed from `tool_calls_json` into `ModelMessage.tool_calls`; tool rows reconstruct `role`, `tool_call_id`, and `name`. Recreating `AgentRuntime` does not lose conversation state. The loop is capped at four tool rounds; over-limit calls are not executed. Existing Agent-1 behavior remains when `tool_registry=None`: request has `tools=()` and unexpected provider tool calls fail safely.

Agent-2 tools do not mutate Task, Assessment, Mastery, Capability, Evidence, Practice, or other business state. Assessment remains the only Mastery path; qualifying Evidence remains the Capability path.

## Still not implemented

- TaskContext auto injection / Builder
- Agent Workspace UI (Sidebar remains Today / Learning Routes / Practice / Settings)
- write tools, approvals, mutation policy
- Agent Skills (separate from career `SkillService` / `skills` table)
- MCP, Sandbox
- memory / context compaction
- trace / evaluation

## Tests

- `tests/test_agent_tool_registry.py`: registration, duplicate/read-only checks, schemas, errors, strict arguments, JSON safety.
- `tests/test_agent_learning_tools.py`: six Service-backed tools, missing context, mastery/capability evidence, read-only regression.
- `tests/test_agent_tool_runtime.py`: ordered tool loops, error paths, round cap, session isolation, persistence/reconstruction after Runtime recreation, verifier growth.
- Agent-1 session/model/runtime and `tests/test_agent_architecture.py` continue to guard legacy boundaries.
