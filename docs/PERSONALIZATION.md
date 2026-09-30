# Personalization — P-1B / P-1C / P-1D / P-1E-A

Current versions: **schema 27 / fingerprint 7 / evaluator 2**.

P-1B established persistence; P-1C added Settings UI and manual memory management.
P-1D adds **Agent personalization context injection** through worker-owned dependencies.
There is still **no automatic Session extraction**. Structured workflows, Skill
selection, Session compaction, PromptRegistry and Prompt Manager semantics are unchanged.

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
are independent persisted preferences: P-1D uses `memory_enabled` to gate context
injection; `auto_memory_enabled` remains consent for future extraction only.

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
only, passing it through MainWindow to AISettingsPage. P-1D separately constructs
its repository/service from the worker-owned fresh connection in `build_agent_runtime`.
There is no global DB singleton or
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
feedback, never raw exceptions. Saving is immediate persistence (P-1C did not add
runtime behavior; P-1D reads saved state on the next turn). Explicit refresh loads persisted state; there is no
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

## Agent context injection (P-1D)

`app/agent/personalization_context.py::AgentPersonalizationContextBuilder` reads
Service settings and enabled memories only. No SQL lives in AgentRuntime. The
production chain is `AgentTurnWorker.run()` → fresh SQLite connection →
`build_agent_runtime(fresh_conn, ...)` → worker-owned PersonalizationRepository →
PersonalizationService → context builder. The Settings service is never passed to
workers. The runtime dependency is optional; absence and empty defaults preserve
baseline system-message text exactly.

System-message order is base instructions and existing tool/Workspace/Approval/MCP
availability guidance → Agent Skill → **Personalization** → Task Context → Session
Memory → existing omission notice. Conversation messages remain separate after the
system message. Personalization has independent `Personal Instructions` and
`Personal Memories` sections within BEGIN/END_PERSONALIZATION. They are long-term
preferences/background, not safety policy or irrevocable system authority. **The
current user's explicit request wins over conflicting long-term preferences**;
base safety/tool permissions still apply. The original current user message is
not rewritten (e.g. a request for Python overrides a default C++ preference).

Non-empty instructions are injected regardless of the memory switch. Memories are
read only when `memory_enabled` is on, filtered to enabled rows, ordered by existing
`id ASC`. Only their content is rendered, never database IDs or provenance metadata.
Multiline Instructions retain their text; multiline memories use indented list
continuations. `auto_memory_enabled` does not affect injection or create rows.
There is no top-N limit, truncation, retrieval, ranking, deduplication or merge.
The existing compactor budget applies to conversation history/summary, not this new
layer. The audited local DB was v26 without personalization tables; it was inspected
read-only and not migrated, so it supplies no evidence about Windows memory volume.

One read-only personalization snapshot is built per accepted turn and reused across
tool rounds; the next turn reads current saved state again. Runtime never updates
personalization timestamps or rows. Session Memory remains Session-scoped and is
not used to store or extract cross-session preferences; the compaction model does
not receive the new layer.

If a read/build fails, the whole optional layer is omitted and the Agent turn
continues. A warning logs exception class and code function/line, without raw
exception text, paths, source lines, preferences or traceback. Programming failures
thus remain diagnosable while SQLite/internal details do not enter model requests
or user-facing output. P-1D changes neither Settings UI nor schema/domain APIs.
P-1D left the P-1C Settings helper text unchanged; P-1D.1 subsequently corrected
that wording to describe Agent context injection and future automatic extraction.

## Memory candidate contract and safe extraction (P-1E-A)

This is a **standalone capability**, not automatic runtime behavior. The only input
is one original successful-turn user message plus application-supplied Session and
Message IDs. `PersonalMemoryExtractor` accepts an `AgentModelClient`, builds a
separate two-message request (extraction instructions + JSON-wrapped source data),
with no tools or conversation context, and returns transient candidates. It reads
no database, produces no Agent messages and saves no Personal Memories. It does not
verify successful-turn status or database provenance: those are caller obligations
for future integration, not reasons to read history in E-A.

`app/agent/memory_candidate.py` defines the frozen `MemoryCandidate` DTO, normalization
and independent strict parser. Model objects contain exactly `content`, `kind`,
`evidence`, `reason`. Source IDs, normalized key, extractor version (currently 1)
and reserved `possible_conflict=False` are application-owned. The conflict flag is
not a claim that existing memories were checked; E-A never accesses them.

Allowed kinds are `long_term_preference` and `stable_background`. The prompt treats
source text as untrusted data, prohibits following its commands, inference,
expansion, ability/Mastery/Capability assumptions, third-party/quoted information,
credentials and sensitive private information, and requests `[]` when ambiguous.
Conservative local source screening additionally abstains on known secret/private,
third-party or quoted patterns before making a model call, and requires explicit
long-term/default preference or self-authored stable background cues. Temporary,
questioning, context-dependent and negated statements are rejected. This lexical
screening deliberately trades recall for safety; it is not an exhaustive language
or privacy classifier and may reject otherwise valid messages (including negative
preferences or messages mixing quotes/private text with valid preferences).

E-A.1 separates source-wide hard secrets/identifiers from sensitive personal
self-disclosures. Medical/diagnosis/diabetes, political, religious and similar
**learning topics are not private disclosures by themselves**. Long-term preferences
for diabetes datasets, political text classification, or studying medical diagnosis
datasets can proceed. Chinese/English personal assertion patterns (e.g. 我患有…,
我的诊断是…, 我的政治立场是…, 我是…患者, I have diabetes, My income is…) remain
excluded. Model phrasing about “the user” is screened as well. The same check covers
content, evidence and reason after NFC/whitespace normalization; valid topic words
in candidate explanations no longer trigger rejection. A mixed source containing
both a learning preference and a sensitive personal disclosure is still withheld
in full. This remains conservative lexical screening, not a complete privacy NLP
classifier; credentials/identifier fail-closed rules and extractive evidence checks
are unchanged. E-A.1 validation: **184 directly affected extractor tests passed**,
including existing secret/credential cases, allowed learning topics, rejected
personal self-disclosures, mixed sources and hostile candidate field rewrites.
`git diff --check` passed; no runtime/thread/consent/UI/DB/schema changes or broad suite.

E-A.2 likewise distinguishes security learning topics from actual credential
exposure. Bare password/token/secret/credential/API key and 密码/密钥/口令/令牌
terms no longer trigger source-wide exclusion. Credential assignments (`:`, `=`,
是/为, is/are with a value), `sk-…`, Bearer values, private-key block headers and
unpunctuated value-shaped credentials remain fail-closed across source, content,
evidence and reason. Explicit references to a concrete personal credential and
existing identifier protections remain conservative; opaque values without labels
cannot be classified exhaustively by lexical rules. Password hashing, token
mechanisms, API key management/OAuth, cryptography and key exchange are valid
learning topics. The stable-background cue includes 主要学习 as well as 主要使用.
Mixed learning preferences plus actual leaked credentials are withheld entirely;
model rewrites referring to the user's credentials do not bypass screening. This
refinement adds no runtime/thread/consent/UI/DB/schema integration. E-A.2 validation:
**239 directly affected extractor tests passed**, including all earlier credential,
sensitive-disclosure and learning-topic tests; assignment/value/block detection,
candidate field rewrites and mixed cases. `git diff --check` passed. Versions remain
27 / 7 / 2; no broad suite was run.

Validation is atomic for the whole response: strict JSON array, exact fields and
string types, whitelisted kind, no duplicate JSON keys or non-finite constants,
maximum **3 candidates before deduplication**. Three limits first-version output
without scanning additional context. Content is stripped non-empty plain text,
maximum 1000 characters, with the same control/format/surrogate rejection as the
Personal Memory service. Evidence is at most 1000 characters and must occur literally
in the source; both evidence and its full containing statement must qualify, so a
model cannot crop away temporary scope or negation. Reason is non-empty safe text,
at most 500 characters. Source IDs must be positive integers (not bools).

**E-A is extractive, not a semantic paraphrase engine**: normalized content must be
a continuous excerpt of normalized evidence. This extra conservative check rejects
unverifiable additions and rewordings. NFC + stripped/collapsed whitespace generates
the key; technical punctuation and case are preserved. Identical keys within one
response are deduplicated in first-occurrence order. No database dedupe or semantic
merge occurs. Literal evidence/excerpts do not prove every semantic interpretation;
future user confirmation remains necessary.

Malformed/oversized/deep JSON, unsupported structures/fields, invalid evidence/text,
excess candidates, tool calls, incomplete responses and model exceptions fail closed
to `[]` at the extractor boundary, without repair or retry. The parser exposes a
controlled validation error for direct callers. Logs include only application-owned
validation codes, exception type and code function/line, never source, candidates,
credentials, raw exception text or paths. The output-size limit is 65,536 characters;
model requests use temperature 0 and a 2048-token output ceiling, with truncated
responses rejected rather than accepted partially.

Confirmed future product decisions remain: generation gate is `memory_enabled AND
 auto_memory_enabled`; candidates require user confirmation before saving; pending
candidates stay in memory, without old-history scans, automatic merge, rejected-row
persistence or cross-restart exactly-once. **E-A implements none of that scheduling,
consent orchestration, confirmation UI, QThreads or persistence.** It must not be
wired into automatic turns until E-B supplies the consent boundary. Personal
Instructions, Session Memory/compaction and PromptRegistry are unchanged. Schema /
fingerprint / evaluator stay **27 / 7 / 2**.

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

P-1D validation: **111 targeted tests passed**, including the new personalization
context/runtime tests; existing runtime, Skill, Task Context, compaction, QThread,
tool, Trace, MCP, sandbox and Approval runtime tests; Workspace production binding;
and three selected production factory/integrated worker tests. Checks cover SQLite
read-only access, unchanged personalization timestamps/rows, all enabled memories
in deterministic order, tool-loop snapshot stability, current-request precedence,
cross-session context, safe failure fallback, and real compaction requests excluding
personalization. `git diff --check` passed. Versions remain **27 / 7 / 2**. No
schema/domain, PromptRegistry, Session Memory or Settings changes; no extraction,
dedupe/merge, ranking, implicit truncation or broad test suite.

P-1E-A validation: **191 targeted tests passed** across
`test_personal_memory_extractor.py`, `test_personalization_service.py`,
`test_agent_personalization.py` and `test_prompt_registry.py`. Fake-model coverage
includes permitted self-statements, conservative abstention, secrets/private text,
strict malformed/hostile output rejection, evidence/content grounding, application-owned
fields, deterministic normalization and local duplicates. A SQLite authorizer test
confirms no DB reads/writes, new messages or changes to existing context/prompt rows.
`git diff --check` passed. No runtime wiring, consent scheduling, QThread, UI,
automatic saving, schema migration or broad suite was added/run.
