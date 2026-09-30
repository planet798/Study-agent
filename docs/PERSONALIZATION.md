# Personalization — P-1B

Current versions: **schema 27 / fingerprint 7 / evaluator 2**.

This phase is persistence only: **no UI, no Agent injection, no automatic
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
operations. Construct the repository with the worker-owned fresh connection, then
construct the service from that repository. There is no global DB singleton or
main-thread connection stored in AgentRuntime. Each mutation commits, matching
existing Agent repository conventions. Fresh-schema snapshots derive from the
same real migration path and include the singleton seed.

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
