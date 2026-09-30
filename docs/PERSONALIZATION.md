# Personalization — P-1B / P-1C

Current versions: **schema 27 / fingerprint 7 / evaluator 2**.

P-1B established persistence. P-1C adds **Personalization Settings UI and manual
memory management**. There is still **no Agent injection and no automatic Session
extraction**. AgentRuntime, structured workflows, Skill selection, compaction,
PromptRegistry and Prompt Manager semantics are unchanged.

## Separate concepts

- **Personal Instructions:** Agent-specific user preferences, plain authored text.
  Not an internal workflow template, and never applied to structured JSON workflows
  in this phase. Strings only, surrounding whitespace stripped, empty means no
  personalization, at most 8000 characters after stripping. Multiline text and
  newline/carriage-return/tab are allowed; NUL, other Unicode control/format characters
  and surrogates are rejected before stripping. `{{variables}}` remain literal.
- **Personal Memory:** distilled cross-session user preferences stored locally in
  SQLite application data. Editable, disable-able and physically deletable. Content
  is non-empty stripped plain text, at most 1000 characters, with the same character
  safety rules. Callers supply distilled content; there is no raw conversation
  copying, model inference or automatic classification/extraction. This is not
  Mastery, Capability, Evidence, LearningOutcome, Task or conversation history.
- **Session Memory:** existing `agent_session_memory`, introduced in v22, remains
  Session-scoped rolling conversation compaction. It is neither renamed nor reused.
- **Prompt Overrides:** existing `prompt_overrides`, introduced in v14, remain
  internal AI workflow template overrides. Planner, Assessment and Task Review
  prompts are untouched; Personal Instructions are not rendered by PromptRegistry.

## Schema and ownership

The real `_migrate_v27` adds `agent_personalization_settings`, constrained to
singleton `id=1`, with default instructions `''`, memory off, auto-memory off and
`updated_at=''`. Existing users therefore retain identical behavior. Both toggles
are independent persisted preferences, not implemented runtime behavior.

`agent_personal_memories` has an autoincrement id, content, source type, optional
Session/Message references, enabled flag and creation/update timestamps. Manual
items have no provenance ids; Session items require an existing Session. An optional
Message must exist and belong to that Session (repository validation). Future
extraction should prefer user Messages; no role restriction or extraction is added
now. Edits preserve provenance and creation time. Listing is ascending id order,
including disabled items by default. Missing edits/toggles raise ValueError;
deletion is idempotent and physically removes only the personal-memory row.

`app/database/personalization_repository.py` owns SQL persistence and provenance
lookups, with no prompt construction, model calls or UI wording.
`app/services/personalization_service.py` owns plain-text/boolean validation and
operations. P-1C constructs the service from the main-thread connection for Settings
only, passing it through MainWindow to AISettingsPage. A future worker integration
must construct its own repository/service from the worker-owned fresh connection. There is no global DB singleton or
main-thread connection stored in AgentRuntime. Each mutation commits, matching
existing Agent repository conventions. Fresh-schema snapshots derive from the
same real migration path and include the singleton seed.

## Settings UX (P-1C)

Appearance remains above the tabs. Tab order is **个性化 → 模型 / API → 高级**;
个性化 is the default. The Settings shell subtitle is 个性化、模型与外观设置;
there is no new navigation PageKey.

`PersonalizationPanel` calls Service only. Agent 说明 uses a normal prose multiline
editor with a placeholder example (never saved as initial content), an 8000-character
counter and explicit 保存. Empty persisted instructions stay empty; no system prompt
fallback exists. Service validation is authoritative. Errors use controlled inline
feedback, never raw exceptions. Saving is immediate persistence, not a claim that
Agent turns already consume it. Explicit refresh loads persisted state; there is no
background polling or refresh during typing.

The memory master checkbox persists only `memory_enabled`. Turning it off disables
the auto-memory consent checkbox without clearing its checked preference or any
memory rows. Re-enabling restores interaction; 管理记忆 remains available while off.
The second checkbox stores consent for future extraction, not an operational feature.
No Sessions are scanned and no model calls or background jobs are added.

`PersonalMemoriesDialog` is a modal manager listing all memories, including disabled
ones. Source labels are 手动添加 / 学习会话, never raw source IDs. The empty state says
还没有本地记忆。 with an 添加记忆 action. Add/edit use a multiline editor and 1000-character
counter; Cancel does not mutate. Editing preserves provenance and creation time.
启用 / 停用 keep items listed using neutral theme styling. 删除 requires confirmation
with 取消 as default/escape; it physically removes only the personal-memory row and
immediately refreshes the list. Chat history and Session Memory are unaffected.

PromptManagerPanel lives intact under 高级 with a structured-workflow warning. Category
tree, variables, override save/reset, default viewing and final preview remain
independent of Personal Instructions. No personalization creates Prompt Overrides.
When the optional service is absent/unavailable, personalization controls are disabled
with concise feedback; Appearance and Model/API remain accessible.

## Release verifier

Both tables are mutable user settings/state: inventory counts only, **not**
`HISTORY_TABLES`, `FINGERPRINT_COLUMNS` or non-decreasing `GROWTH_TABLES`.
Legitimate edits/disables/deletions do not constitute history tampering. Existing
Session, Message, Approval and Workspace protection is unchanged. Fingerprint
version stays 7; evaluator stays 2. The historical v26 Session-management migration
and earlier chronology remain intact.

Tests include a populated real v26→v27 upgrade preserving Tasks, Sessions,
Messages, Session Memory, Approval history, Workspace bindings and Prompt Overrides,
plus mutable-state verification and continued immutable-history tamper rejection.

P-1B validation: 151 targeted tests passed across personalization repository/service,
real migration, Agent history verifier, schema migrations, PromptRegistry, Session
Memory repository, directly affected version/migration contracts and fresh-schema
parity. The focused `-m migration tests/test_personalization_migration.py` run passed
4 tests. `git diff --check` passed. No broad test suite or stress loops were run.

P-1C validation: **121 focused tests passed** across `test_personalization_ui.py`,
`test_ai_settings_ui.py`, `test_ui4_pages.py`, `test_theme_preferences.py`,
`test_foundation_components.py`, `test_main_window.py` and `test_main_window_exit.py`.
Coverage includes actual add/edit/delete modal flows, cancel-by-default confirmation,
master/auto toggle persistence, controlled validation feedback, independent Advanced
Prompt save/reset/preview, unavailable-service fallback and unchanged history/compaction
rows. Schema/domain semantics and all three versions remain unchanged. No broad test
suite or stress loops were run.
