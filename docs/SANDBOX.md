# Task-scoped Sandbox (Agent-6)

Sandbox provides bounded file tools and optional execution for the user-selected Task Workspace. It is **not** a Study-Agent application-state write tool. Native Study-Agent data remains read-only; Sandbox writes are restricted to a managed Workspace. See [WORKSPACES.md](WORKSPACES.md) for user-facing binding and Learning Note vs file semantics.

## Default and configuration

Default operator config:

```text
data/sandbox.json
```

Override with `STUDY_AGENT_SANDBOX_CONFIG`. Missing config uses safe default file limits and disables execution; a managed Workspace binding still enables basic file tools. The real config and workspaces are ignored by Git. See the fake [`docs/examples/sandbox.example.json`](examples/sandbox.example.json).

The example enables bounded file tools but leaves `execution.enabled=false`. Enabling execution requires a managed binding, `backend="docker"`, an installed/reachable Docker daemon, and the configured image already present locally. Study-Agent never installs Docker, pulls/builds images, or falls back to host execution. Invalid config falls back to safe default file limits but disables execution; Native and MCP tools continue. The UI shows a sanitized warning.

## Workspace model

```text
data/agent_workspaces/task_<integer task_id>/
```

The managed root is derived only from the Session-bound integer Task ID—not Task title, Session title, or model input. It persists across Sessions for that Task. Its binding stores no internal absolute path. Opening a Session/runtime does not create the directory; an actual managed file action or the user's explicit Open Folder click can create it. Task A and Task B have separate bindings and physical roots. A local binding instead uses the user's selected canonical project directory as its root directly, with read-only tools. A missing local directory exposes no file tools; neither type of binding can be selected by the model.

Only relative paths are accepted. Absolute POSIX/Windows paths, drives, UNC paths, URIs, `~`, NUL, `..`, symlinks, and junctions are rejected. Listing is one level and bounded. Only UTF-8 text is readable; binary content is rejected. Per-file characters are bounded by `max_file_chars` (default 200,000). Writes use a same-directory temporary file and atomic replace/create; existing files require explicit `overwrite=true`. Directory creation is idempotent. No delete, move, chmod, symlink, Git push, or repository-import tool exists.

Sandbox package (`app/agent/sandbox/`) has no Service, Repository, SQLite, Assessment, or Capability dependency. Runtime resolves a fresh Task Workspace binding to an in-memory workspace spec at each turn and passes it to `SandboxProvider`; the provider does not query SQLite or import a Service/Repository. Tools use that frozen root and relative paths, never a model-supplied host path. Workspace artifacts are not Assessment or Practice evidence and do not complete Tasks.

## Tool permissions

The default `AgentToolRegistry()` remains read-only and rejects `read_only=False`. Agent-6's Sandbox provider explicitly builds an effective registry with only the `sandbox` mutation scope enabled. `AgentToolSpec` requires read-only tools to have no scope and mutating tools to declare an authorized scope. Sandbox file mutations use only the `sandbox` scope; application-state mutation uses a separate approval-request flow, never filesystem tool access. `database`, `task`, `mastery`, and `capability` scopes do not exist.

Fixed Sandbox tools (registered according to the frozen turn binding):

| Tool | None | Managed | Local project |
|---|---|---|---|
| `sandbox_list_files`, `sandbox_read_file` | — | read-only | read-only, sensitive-path deny policy |
| `sandbox_write_file`, `sandbox_make_directory` | — | `mutation_scope="sandbox"` | — |
| `sandbox_run` | — | `mutation_scope="sandbox"` only with configured and available Docker | — |

Local sensitive files (`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519`, `.npmrc`, `.pypirc`, `.netrc`, `.git/`, `.ssh/`) are denied with a controlled error, without exposing their contents or absolute paths.

Sandbox is composed after Native and approved MCP tools. Native names win; collisions fail closed. MCP tools remain read-only and cannot acquire Sandbox scope. Task Context still uses only the six Native tools, and the Agent Skill is selected once from that same Context snapshot.

## Docker execution

`sandbox_run` accepts an argv array, optional relative `cwd`, and bounded stdin—never a shell command string, environment, timeout, image, Docker flags, or container name from the model. The host invokes only the fixed Docker CLI with `shell=False`. The container mounts exactly the current Task workspace at `/workspace` and uses:

- `--network none`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- configured PID, memory, and CPU limits
- `--read-only` container root filesystem
- bounded `nosuid,nodev` `/tmp` tmpfs
- current host UID/GID on POSIX where available

No repository, home directory, Docker socket, SSH key, or host credential is mounted. The configured image must already exist; there is no `docker pull`. A missing Docker executable/daemon/image leaves file tools usable but omits `sandbox_run`—there is never a host-shell fallback.

Each run gets a host-generated random container name, an operator-configured timeout, bounded stdout/stderr, and best-effort `docker rm -f` cleanup on success, failure, and timeout. Sandbox execution is container-isolated with explicit filesystem/network/resource constraints; it is not claimed to be a perfect host security boundary.

## Runtime and trust boundaries

Sandbox tools run only in `AgentTurnWorker` on its worker-owned Runtime/connection lifecycle. The GUI thread does not execute Agent file tools or Docker; the user's explicit managed Open Folder action may create that directory through the Workspace Service. Sandbox file contents and stdout/stderr are untrusted task data, not system instructions. Sandbox tools cannot access or modify Study-Agent SQLite state, Mastery, Capability, Assessment, Practice, or Evidence.

No Sandbox/Terminal page is added to the Sidebar. There is no file explorer, terminal UI, local-project write or execution, diff approval flow, network access, automatic starter project, or workspace import. Managed Workspace writes use the existing sandbox mutation scope, not application Approval; saving a Study-Agent Learning Note is a separate approved SQLite action.
