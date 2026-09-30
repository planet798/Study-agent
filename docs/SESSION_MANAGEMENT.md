# Session Management-1B

Versions: schema **26**, fingerprint **7**, evaluator **2**.

The real v25→v26 migration adds `display_title TEXT NOT NULL DEFAULT ''`,
`pinned_at TEXT`, and `archived_at TEXT` to `agent_sessions`. Existing rows
receive empty override and NULL timestamps; historical data is unchanged.

- `title` remains the immutable Task-title snapshot captured at creation.
  Rename writes only `display_title`; reset stores `''`. Visible title resolves
  to stripped override, otherwise original title, otherwise `学习会话` (never
  stored as fallback). Rename accepts 1–120 trimmed characters, without control
  characters. No automatic title generation.
- Pin sets a timestamp once; unpin clears it. Archived Sessions must be restored
  before pinning. Metadata operations do not change conversation `updated_at`.
- Archive atomically sets `archived_at` and clears `pinned_at`. It does not close,
  delete, or alter conversation history. Restore clears archive without re-pin.
  Both operations are idempotent. Metadata mutations require active Sessions.
- `AgentSessionService` exposes `rename`, `reset_title`, `pin`, `unpin`,
  `archive`, and `restore`; core `get` and `messages` still read archived Sessions.
- `list_sidebar_sessions(limit=10)` returns all active non-archived pins ordered
  by `pinned_at DESC, id DESC`, followed by latest N unpinned active non-archived
  Sessions ordered by `updated_at DESC, id DESC`. Pins do not consume N.
- `list_archived_sessions(limit=None)` returns active archived Sessions ordered
  by `archived_at DESC, id DESC`. Existing active/recent APIs keep their lifecycle
  semantics, including archived Sessions and independence from Task status.

The new columns are mutable projections, excluded from immutable fingerprints.
`id / task_id / title / created_at` and all historical messages remain protected.
No Session menu, Sidebar rendering change, archive UI, or Delete API is added.
SM-1C will wire these domain APIs into UI.
