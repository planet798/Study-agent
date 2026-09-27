# Task-scoped Sandbox (Agent-6)

Sandbox is an optional, per-Task workspace and execution capability. It is **not** a Study-Agent application-state write tool. Native Study-Agent data remains read-only; Sandbox writes are restricted to the bound Task workspace.

## Default and configuration

Default operator config:

```text
data/sandbox.json
```

Override with `STUDY_AGENT_SANDBOX_CONFIG`. Missing config disables Sandbox. The real config and workspaces are ignored by Git. See the fake [`docs/examples/sandbox.example.json`](examples/sandbox.example.json).

The example enables bounded file tools but leaves `execution.enabled=false`. Enabling execution requires `backend="docker"`, an installed/reachable Docker daemon, and the configured image already present locally. Study-Agent never installs Docker, pulls/builds images, or falls back to host execution. Invalid config disables only Sandbox; Native and MCP tools continue.

## Workspace model

```text
data/agent_workspaces/task_<integer task_id>/
```

The root is derived only from the Session-bound integer Task ID—not Task title, Session title, or model input. It persists across Sessions for that Task. Opening Workspace/runtime does not create the directory; the first actual Sandbox tool call creates it lazily. Task A and Task B have separate physical roots.

Only relative paths are accepted. Absolute POSIX/Windows paths, drives, UNC paths, URIs, `~`, NUL, `..`, symlinks, and junctions are rejected. Listing is one level and bounded. Only UTF-8 text is readable; binary content is rejected. Per-file characters are bounded by `max_file_chars` (default 200,000). Writes use a same-directory temporary file and atomic replace/create; existing files require explicit `overwrite=true`. Directory creation is idempotent. No delete, move, chmod, symlink, Git push, or repository-import tool exists.

Sandbox package (`app/agent/sandbox/`) has no Service, Repository, SQLite, Assessment, or Capability dependency. Sandbox tools derive their workspace solely from `AgentToolContext.task_id` and relative paths. Workspace artifacts are not Assessment or Practice evidence and do not complete Tasks.

## Tool permissions

The default `AgentToolRegistry()` remains read-only and rejects `read_only=False`. Agent-6's Sandbox provider explicitly builds an effective registry with only the `sandbox` mutation scope enabled. `AgentToolSpec` requires read-only tools to have no scope and mutating tools to declare an authorized scope. The only supported mutation scope is `sandbox`; `application`, `database`, `task`, `mastery`, and `capability` scopes do not exist.

Fixed Sandbox tools:

| Tool | Permission |
|---|---|
| `sandbox_list_files` | read-only |
| `sandbox_read_file` | read-only |
| `sandbox_write_file` | `mutation_scope="sandbox"` |
| `sandbox_make_directory` | `mutation_scope="sandbox"` |
| `sandbox_run` | `mutation_scope="sandbox"`, exposed only when Docker probe succeeds |

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

Sandbox tools run only in `AgentTurnWorker` on its worker-owned Runtime/connection lifecycle. The GUI thread never reads/writes workspace files or starts Docker. Sandbox file contents and stdout/stderr are untrusted task data, not system instructions. Sandbox tools cannot access or modify Study-Agent SQLite state, Mastery, Capability, Assessment, Practice, or Evidence.

No Sandbox/Terminal page is added to the Sidebar. There is no file explorer, terminal UI, write approval flow, network access, automatic starter project, or workspace import.
