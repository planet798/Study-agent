# Task Workspaces (Workspace-1)

A Workspace is an explicit, persistent **Task-scoped** filesystem binding selected by the user in the Agent Session UI. Sessions for the same Task share the binding; different Tasks do not. Learning Shell-1 presents the binding as a compact conversation-header selector/menu rather than a large body Card. Opening a Session does not select a Workspace or create a directory. Without a binding, the Agent has **no filesystem tools**.

| Binding | Location | Agent filesystem access | Execution |
|---|---|---|---|
| None | No directory selected | None | None |
| Study-Agent managed | `data/agent_workspaces/task_<task_id>/` under the application's sandbox workspace root | Bounded relative-path list/read/write/mkdir | Optional Docker-only `sandbox_run`, if configured and available |
| Local project | An existing directory explicitly selected with the folder picker | Bounded relative-path list/read **only** | Never |

The selector tooltip/menu lets the **user** view and copy the physical absolute path without keeping it on a conversation row; the model receives only a trusted per-turn capability description (managed/read-write, local/read-only, or none). No host absolute path is added to the system prompt, Task Context, Trace or Evaluation. Tool paths such as `src/train.py` and `notes/loss_mask.md` are always relative to the selected root; the model cannot select, switch, clear, or open a Workspace. Only user UI actions change the binding. Binding changes are disabled during an Agent turn or approval execution; the next turn reads the latest binding and freezes it for that turn.

## Choosing and managing a binding

- **使用托管工作区** binds the Task to its deterministic Study-Agent-managed directory. The database stores `kind=managed` and an empty `local_path`, not an absolute internal path. Existing artifacts at `task_<task_id>` remain in place. The directory is created only by a file action or the user's explicit **打开文件夹** click, not by opening a Session.
- **选择本地项目** opens `QFileDialog.getExistingDirectory`. Cancel leaves the binding untouched. A successful selection stores a canonical absolute, existing non-root directory path (up to 4096 characters; no NUL). It does not scan, index, or modify the project.
- The selector menu offers **使用托管工作区** and **选择本地项目**; neither choice happens automatically. **解除绑定** removes only the database binding, never filesystem contents. Rebinding managed to the same Task restores access to the same physical directory.
- If a local directory is later missing, the selector retains the original path and offers **重新选择** or **解除绑定**. No parent-directory fallback, managed fallback, or automatic mkdir occurs. No local file tools are exposed until a valid binding is selected again.
- **打开文件夹** uses the desktop URL handler for an available directory, never a shell command. The local directory must still exist; only an explicitly opened managed folder may be created for this action.

## Learning Note is not a Workspace file

“保存成学习笔记” requests a **Study-Agent Learning Note**: `request_save_learning_note` → explicit application Approval → `LearningOutcome(kind="note")` in SQLite. It does not create a `.md` file. An explicit request to generate/export a file (for example, “生成 `learning_note.md`”, “创建 `README.md`”, or “把内容写成 Markdown 文件”) uses Workspace filesystem tools when the binding is writable. Managed file writes need no application Approval because the user explicitly selected that isolated Workspace. Local project bindings are read-only in Workspace-1: the Agent can explain that changes require a future Workspace-2 diff and approval flow, but it cannot register a write, mkdir or execution tool for that project.

## Read safety and privacy

Both workspace types reuse bounded one-level listing, relative-path validation, symlink/junction escape protection, UTF-8-only reads, bounded text, and controlled errors. Managed writes are atomic and overwrites must be explicit. For local projects, sensitive basenames/patterns (`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519`, `.npmrc`, `.pypirc`, `.netrc`) and directories (`.git/`, `.ssh/`) are denied with `workspace_sensitive_path_denied`; their contents must never be forwarded to the model. Ordinary files read from a selected local project **do** enter the model request as expected, but Trace/Evaluation/Approval and binding audits do not copy file contents or host paths.

Missing or invalid `data/sandbox.json` does not remove managed basic file tools: safe default limits apply, while execution is disabled. Docker execution requires a managed binding, valid execution configuration, and a successful Docker backend probe. There is no host-shell fallback. Workspace-1 adds no local writes/execution, diff editor, Git operations, terminal UI, file explorer, recursive indexing, embeddings/RAG, workspace inheritance, or automatic project detection. See [SANDBOX.md](SANDBOX.md) for the filesystem and Docker boundaries.
