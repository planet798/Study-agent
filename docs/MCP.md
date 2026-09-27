# MCP in Study-Agent (Agent-5)

MCP adds optional external tools to the task-bound Agent Runtime. It does **not** make MCP a permission bypass.

## Three separate concepts

- **Native Tool**: read-only access to Study-Agent state through an existing Service.
- **Agent Skill**: static teaching behavior strategy.
- **MCP Tool**: user-configured external server capability.

The MCP server, its tool metadata, schemas, and results are an **external untrusted-data boundary**. They are never system instructions and never grant application permissions.

## Supported scope

Runtime dependency: official Python SDK `mcp>=2,<3`.

Supported transports:

- `stdio` — command, args, and only explicitly allowlisted process environment variables.
- `streamable_http` — URL-only via the official SDK client.

Not supported: SSE as a new configuration transport, Resources, Resource Templates, Prompts, Sampling, Elicitation, Roots, MCP Apps/UI, OAuth, custom Authorization headers, or write MCP tools.

## Config file

Default path:

```text
data/mcp_servers.json
```

Override with:

```text
STUDY_AGENT_MCP_CONFIG=/path/to/mcp_servers.json
```

The repository ignores `data/mcp_servers.json`. A missing file disables MCP; Native Study-Agent tools and Agent-4 Skills continue normally. See the fake example at [`docs/examples/mcp_servers.example.json`](examples/mcp_servers.example.json).

A server entry has a stable local key, one supported transport, and an explicit per-server `allowed_tools` list. Wildcard `"*"` is forbidden. Stdio `env_from_process` stores **environment variable names only**; values are read from the process at turn time and passed only for listed names. Do not put tokens in JSON, command args, or URLs. Streamable HTTP authentication is not configured in Agent-5.

## Authorization: both gates are required

An MCP tool is exposed only if:

1. its raw remote name exactly matches a locally configured `allowed_tools` entry; and
2. the server explicitly reports `annotations.readOnlyHint is True`.

`readOnlyHint` alone is not authorization. Allowlisted tools without the hint, missing annotations, and `readOnlyHint=False` are hidden. Write/destructive MCP tools are never exposed in Agent-5. Tool names are namespaced, e.g. `docs/search` becomes `mcp_docs_search`; only hyphens are normalized, and collisions are skipped rather than overwritten.

## Turn lifecycle and failures

Each Agent user turn opens one MCP scope inside `AgentTurnWorker`: one private asyncio event loop, one SDK Client per enabled server, discovery (including pagination), all calls in that turn's tool loop, then deterministic client/process/loop cleanup. There is no global client, main-thread client, or `asyncio.run()` per call.

A server that cannot connect or list tools is unavailable for that turn; other servers and Native tools continue. MCP tool errors are returned as bounded, provenance-tagged tool data; transport failures become a generic `mcp_tool_failed` result. No exception, traceback, environment value, or secret is forwarded to the model.

Text and structured content are bounded. Images, audio, links, and binary resource blocks are described as unsupported rather than base64-encoded into context. Resource APIs are not called. Tool-call/result messages use existing append-only `agent_messages`; no MCP cache or schema is added.

## Trust and permissions

MCP names, input schemas, descriptions, structured content, and returned text are untrusted external data. Study-Agent system instructions say so explicitly. They cannot alter the native registry, Agent Skill selection, read-only policy, Task state, Mastery, Capability, Evidence, or Practice.

Agent-5 does not add a server-management UI, OAuth, write approval flow, filesystem, shell, browser, or Sandbox. MCP is optional external context, not Study-Agent's own execution environment.

## Troubleshooting

- No configured MCP tools: check that the file exists at the default/override path, has `version: 1`, and that the server entry is `enabled: true`.
- A discovered tool is missing: confirm its exact raw name is in that server's `allowed_tools` and the server returns `annotations.readOnlyHint: true`.
- Stdio server unavailable: verify command/args locally and that each `env_from_process` name is set in the Study-Agent process environment. Secret values are not copied from the config file.
- Streamable HTTP unavailable: check the URL and local/network server availability. Agent-5 does not configure HTTP auth.
- MCP unavailable does not disable Native Study-Agent tools.
