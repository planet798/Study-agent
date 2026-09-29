# Study-Agent Codebase Audit

**Baseline:** `1a0362228aa7c76dbdf43377e52cbbb0cafeb54a` · **Date:** 2026-09-29 · **Method:** seven parallel, read-only reviews (architecture, legacy, quality, tests, database, Qt, Agent safety), followed by independent source checks. No production/test edits, migration, dependency installation, or full pytest in this audit. Findings are source-established risks unless explicitly called runtime-reproduced; no runtime reproduction is claimed. Severity is remediation priority, not proof that a user has encountered a failure.

## Executive Summary

The repository is **structured, not indiscriminately messy**: Service/Repository boundaries, migrations, release fingerprints, Agent tool scopes, and the fast fresh-DB test fixture are deliberate. It has concentrated complexity in a few orchestration modules and a few **real correctness and lifecycle risks**. The most consequential are destructive replan cleanup despite persistent Task-linked Sessions, insufficient validation of model-suggested carry-overs/minutes, old-schema fingerprint comparisons, and two untracked dialog-owned QThreads. Agent workspace isolation and approval/trace boundaries should not be simplified away.

Development is slow primarily because the broad `not slow` selection still includes Qt and integration work, the full suite has thousands of tests, tests create isolated DBs/widgets, and some GUI tests wait for actual asynchronous work. The developer guide describes a 20–38-minute full run (`docs/AGENT_GUIDE.md:9-30`); **no timings were measured in this read-only audit**. Nine methods in three shadowed test classes are not collected. Small, sequential fixes with affected-test tiers are preferable to a wholesale refactor.

**Disposition:** P0 **0**, P1 **10**, P2 **11**, P3 **6**. Intentional historical preservation and candidate removals are listed separately. The obsolete Workspace back button is **not** a product defect: Sidebar Today navigation deliberately replaced it in Learning Shell-1 (`app/ui/main_window.py:358-387`, `app/ui/agent_workspace_page.py:198-210`).

## Repository Metrics

Tracked-file static measurements (`git ls-files`, `wc -l`, AST; baseline): **422 files**, **360 Python files**, approximately **88,572 Python lines**; production `app/`: **171 Python files / 46,052 lines**; `tests/`: **187 Python files / 41,611 lines**. Subsystems (`app/`, physical lines): UI 49/12,540; services 40/13,813; database 18/6,526; Agent 40/5,742; AI 15/4,247; diagnostics 4/1,151; remaining root/utilities. Counts depend on tracked-file/path classification, not runtime imports. The two `scripts/` Python files account for the difference between 360 overall and 171+187.

Top 20 production files by physical lines:

| File | Lines | File | Lines |
|---|---:|---|---:|
| `app/main.py` | 1,827 | `app/database/schema.py` | 1,755 |
| `app/ui/main_window.py` | 1,670 | `app/ui/routes_page.py` | 1,424 |
| `app/services/daily_planner_service.py` | 1,150 | `app/services/study_plan_service.py` | 1,005 |
| `app/services/skill_service.py` | 942 | `app/database/practice_repository.py` | 874 |
| `app/ui/practice_page.py` | 833 | `app/agent/runtime.py` | 824 |
| `app/diagnostics/release_migration.py` | 810 | `app/ui/ai_settings_page.py` | 760 |
| `app/ui/agent_workspace_page.py` | 709 | `app/ui/practice_dialogs.py` | 703 |
| `app/ai/schemas.py` | 700 | `app/services/jd_summary_service.py` | 658 |
| `app/services/canonical_routes.py` | 650 | `app/ai/prompts.py` | 597 |
| `app/ai/prompt_defaults.py` | 583 | `app/services/jd_service.py` | 568 |

Largest AST spans (including methods): `MainWindow` 1,579 lines, `DailyPlannerService` 1,048, `RouteDetailDialog` 902, `SkillService` 858, `StudyPlanService` 846, `AgentRuntime` 747. Largest standalone function: `app/main.py:1347` `main()` ~477 lines; large methods include `StudyPlanService.generate_daily_tasks` ~203 and `DailyPlannerService._validate_and_create` ~169. Size is a change-cost signal, **not itself a bug**.

## P0 — Correctness / Safety

**None source-established.** Do not infer an exploited sandbox escape, lost historical data, or a verified crash from the static risks below.

## P1 — High-value cleanup and source-established defects

| ID | Kind · file / symbol | Reason and recommended action |
|---|---|---|
| P1-01 | Correctness · `app/ui/main_window.py:1380-1460`, `_on_replan` | Both branches call `task_service.repo.delete` on generated Tasks. A studied Task has a non-cascading `agent_sessions.task_id` FK (`app/database/schema.py:1367-1370`; enabled by `app/database/connection.py:62-71`), so delete can fail; unreferenced Task Workspace bindings can cascade away (`schema.py:1545-1558`). Move replacement into a Service transaction preserving referenced Tasks/history; test replan with Session and Workspace. **Also a UI→Repository boundary violation.** |
| P1-02 | Correctness · `app/ui/main_window.py:1401-1421`, scheduler replan branch | Deletes all active generated/new Tasks before scheduling, whereas `app/services/route_scheduler.py:183-197` only allocates to eligible routes. Paused/non-plannable routes' Tasks can disappear without replacement. Scope the cleanup to plannable routes in the same Service operation; retain terminal/historical rows. |
| P1-03 | Correctness · `app/services/daily_planner_service.py:911-949`, `_validate_and_create` | AI-provided carry-over ID is not checked against `self._route_id()` before postponing; route-specific planners are used by `app/services/route_scheduler.py:225-230`. Validate Task route ownership before writes and cover cross-route IDs. |
| P1-04 | Correctness · `app/services/daily_planner_service.py:915-918,946-949,1121-1133`, `_is_recent_unfinished` | Only `done` is excluded; a dated `cancelled` Task can pass and `repo.set_status(..., active)` bypasses TaskService transitions (`app/services/task_service.py:27-33`). Permit only specified active/not_done states; use canonical transition rules. |
| P1-05 | Correctness · `app/services/daily_planner_service.py:803-806,858-865,940-951,1017-1048`, `_validate_and_create` | Checks declared `daily_minutes` and, for a route slot, the Topic estimate, but persists the recommendation's estimate without aggregating actual accepted Tasks/carry-overs. Enforce budget against accepted persisted estimates and existing commitments; cover adversarial model output. |
| P1-06 | Migration/verifier · `app/diagnostics/release_migration.py:281-289,728-768`, `fingerprint`/`verify` | Pre-v4 Task rows project absent `task_type` as NULL; v4 migration adds it with default `new` (`app/database/schema.py:284-288`). Per-row hashes can disagree after a legitimate migration of populated old DBs. Check pre-migration projection against post rows, distinguishing migration-owned defaults from historical changes; add populated v1–v3 inventory→migrate→verify cases. Source-derived; not reproduced here. |
| P1-07 | Agent filesystem correctness · `app/agent/sandbox/workspace.py:192-193`, `read_text_file` | `"\\t\\n\\r"` contains literal backslash letters, not control characters; ordinary multiline/tabbed UTF-8 is rejected. Accept actual tab/newline/CR while preserving other-control rejection; test managed and local read-only reads (`tests/test_agent_sandbox_workspace.py:130-137` covers single-line only). |
| P1-08 | Qt lifecycle risk · `app/ui/assessment_dialog.py:165-182`, `_on_submit` | Dialog-parented grading `AssessmentWorker` is not in MainWindow's worker drain (`app/ui/main_window.py:1570-1620`); closing/tearing down during grading risks a live QThread. Give the dialog explicit stop/wait or register it with shutdown; test close/quit while grading. No crash reproduced here. |
| P1-09 | Qt lifecycle risk · `app/ui/routes_page.py:923-958`, `RouteDetailDialog._on_ai_generate` | Dialog-parented `AIRouteBuilderWorker` is likewise not drained by MainWindow; a late completion may open preview after dialog close. Drain/invalidate on close and at application shutdown; test in-flight teardown. |
| P1-10 | Test correctness · `tests/test_main_window.py:208,279,335,370,441,497` | Three `TestAIReview*` classes each have a duplicate definition; later classes shadow **nine earlier test methods** (2+5+2). Compare assertions, consolidate each class once, and verify collected node IDs. No production behavior claim. |

## P2 — Medium cleanup

| ID | Kind · file / symbol | Reason and recommended action |
|---|---|---|
| P2-01 | Boundary · `app/ui/routes_page.py:511-528`, `_needs_experiment_record`; `app/ui/capability_dialog.py:248-262`, `_find_done_experiment_task` | Two widgets acquire `task_repo.conn` and execute near-duplicate SQL. Move read queries into Repository/Service; UI should consume typed results. |
| P2-02 | UX lifecycle · `app/ui/practice_page.py:391-438`, `PracticeProjectDetailDialog.refresh/_restore_scroll` | `singleShot(0/60)` each launches another four restores (0/50/150/400); no epoch or user-scroll cancellation. Earlier refresh can overwrite newer/user scroll. Use one invalidatable, dialog-owned range/layout restore, as in Today; add late-range/manual-input/close tests. |
| P2-03 | Verifier compatibility · `app/diagnostics/release_migration.py:249-254,728-768`, `verify` | Claims v1–v6 snapshots are readable, but row-level hashes are compared without checking whether old/new column projections agree. Older stored fingerprints can misclassify an added fingerprint column as mutation. Compare using before snapshot's available columns and test a real older column set. Coordinate with P1-06. |
| P2-04 | Agent protocol · `app/agent/runtime.py:682-718,763-781`, `_validate_tool_calls` | Requires nonblank call IDs but not uniqueness; duplicate IDs create ambiguous persisted results. Approval binding already rejects duplicates (`app/database/agent_approval_repository.py:137-140`). Reject duplicates before persistence; add malformed-provider test. |
| P2-05 | Responsibility · `app/ui/main_window.py:90-222,688-731,1182-1259,1380-1460`, `MainWindow` | One window owns navigation, Today rendering, Task mutation, planner, Agent/approval and Assessment worker state. Split orchestration incrementally into focused UI controllers, starting with replan Service ownership; do not move domain logic to widgets. |
| P2-06 | Responsibility · `app/services/daily_planner_service.py:64-69,953-1015`, `DailyPlannerService` | Constructor mutates injected `StudyPlanService.assessment_repo`; some queries use `repo.conn` directly. Configure dependencies in composition root and move queries to repositories. Preserve planner order and fallback semantics. |
| P2-07 | Responsibility · `app/agent/runtime.py:115-313,343-485,487-766`, `AgentRuntime` | Prompt assembly, conversation context, MCP/Sandbox scopes, tool loop and trace adaptation live together. Extract request assembly and bounded scope composition, retaining a single sequencing owner and all security gates. No import cycle was demonstrated. |
| P2-08 | Maintenance · `app/database/schema.py:162-169,221-1568`, migration registry | 25-version migration timeline and current schema definitions share 1,755 lines. Extract immutable version-ordered historical migrations into dedicated modules without changing SQL, IDs, fingerprints, or `migrate_stepwise`; gate on real old DB migrations. |
| P2-09 | Test-tier accuracy · `tests/conftest.py:458-477`, `pytest_collection_modifyitems` | Name-pattern `slow` misses real migration/Agent integration, while `ui` requires `qtbot` and misses `qapp`-only tests (`tests/test_theme_runtime.py:28-35`). Explicit marks plus `--collect-only` audits; don't treat `not slow` as a fast tier yet. |
| P2-10 | Observability · `app/main.py:1584-1658`, startup optional actions | Several broad catches intentionally fail open for skill/JD/backfill work without actionable diagnostics. Log bounded non-sensitive operation/error codes; keep startup fail-open where required. |
| P2-11 | Fallback clarity · `app/services/study_plan_service.py:231-264,781-786`, legacy detection | Broad catches can interpret DB/programming errors as absent canonical routes or legacy completion. Narrow expected compatibility exceptions and expose unexpected errors; retain actual historical fallback. |

## P3 — Cosmetic / Documentation

| ID | Kind · file / symbol | Action |
|---|---|---|
| P3-01 | Test-only alias · `app/ui/agent_workspace_page.py:84`, `back_requested`; `app/ui/main_window.py:582-585`, `_on_agent_back` | No production emission/connection; three tests call the handler (`tests/test_agent_approval_ui.py:101,124`, `tests/test_agent_workspace_integration.py:323`). Migrate tests to Sidebar Today before retiring. Do **not** restore the old back button. |
| P3-02 | Test-only alias · `app/ui/main_window.py:236`, `nav_layout` | Only `tests/test_practice_ui.py:135-136` reads it. Move test to `sidebar.items_layout`, then remove alias. Other `nav_*_btn` aliases still serve production `setEnabled` calls (`main_window.py:298-332`) and are **not** safe wholesale removals. |
| P3-03 | Unused readers · `app/database/assessment_repository.py:369-397` | Four review-schedule read methods have no tracked call sites. Conditional removal only after checking supported external API/script callers; preserve table, migrations, rows and verifier. |
| P3-04 | Documentation drift · `docs/AGENT_MEMORY.md:35,105`, `AGENT_TRACE_EVAL.md:21,70`, `AGENT_APPROVALS.md:42`, `ARCHITECTURE.md:74-84,110-121`, `README.md:7` | Current code is schema **25**, fingerprint **7**, evaluator **2** (`schema.py:162`, `release_migration.py:253`, `evaluator.py:7`), but these documents variously claim current v24/v6/v5, future Assessment/Note actions, or future Agent Session/no Sidebar. Update current-state sentences only; keep accurately labeled historical version chronology. `docs/PRODUCT_BASELINE.md:35` is current. |
| P3-05 | Display constraint · `app/ui/main_window.py:204`; `app/ui/components/navigation.py:34-35,244` | 900×620 minimum and fixed expanded sidebar 228 logical px may exceed small available displays; test screen-aware compact geometry. No clipping/crash established by this audit. |
| P3-06 | Small duplication · `app/services/ai_route_service.py:37-89` | Two AI route methods repeat configured/check/chat/error normalization. Optional private helper, but preserve separate parse/filter behavior and user-facing errors. |

## Intentional Legacy That Must Stay

- **Historical tables/fields/rows:** `review_schedule`, `weekly_summaries`, `monthly_summaries`, historical Task `review`/`daily_retention`/manual and NULL-route records, legacy Knowledge Point review fields and Monthly prompt overrides. Retired product UI is **not** deletion authority (`app/database/schema.py:124-145,221-310`; `app/diagnostics/release_migration.py:27-43,196-239`; `docs/PRODUCT_BASELINE.md:35`).
- **Immutable history:** `agent_sessions.task_id` is origin identity; `agent_messages`, Agent/approval event history, Task Workspace bindings and v7 fingerprints remain protected. Session Memory and Trace/Evaluation are derived/operational, not a reason to rewrite conversations (`app/database/schema.py:1367-1568`; `app/diagnostics/release_migration.py:28-68,196-239`). Do not collapse these into one lifecycle.
- **Migration fallback:** `LearningRouteRepository.get_default_learning_route` is used in `RouteMigrationService` (`app/services/route_migration_service.py:114-119`); `ManualTaskService.create_todo` is a documented legacy script alias (`app/services/manual_task_service.py:117-119`; `docs/PRODUCT_BASELINE.md:23`). Legacy `AIClient` still serves non-Agent flows; Agent's no-tool mode remains explicit (`app/agent/runtime.py:670-675`). These are compatibility, not dead product UI.
- **Agent security boundaries:** native tools take session-bound Task identity, MCP requires local allowlist/read-only annotation, Sandbox has Task-scoped path checks and local read-only mode, approvals reread persisted calls, and Trace details are allowlisted (`app/agent/tools/learning.py:14-18`; `app/agent/mcp/tools.py:187-194`; `app/agent/sandbox/workspace.py:78-125,276-312`; `app/agent/approval/service.py:159-170`; `app/agent/trace/collector.py:365-376`). Retain even when extracting modules.

## Safe Removal Candidates

**Not approved for deletion in this phase.** Exhaustive tracked-symbol searches were performed; external script/API consumers still need a compatibility decision.

| File / symbol | Tracked references | Why / tests affected | Risk |
|---|---|---|---|
| `app/ui/agent_workspace_page.py:84` `back_requested` | Declaration only; no `.emit` or connected consumer. | Absent button by design; remove alongside unused MainWindow handler after tests switch to Sidebar navigation. | Low; tests directly invoke handler. |
| `app/ui/main_window.py:582` `_on_agent_back` | Definition + `tests/test_agent_approval_ui.py:101,124` and `tests/test_agent_workspace_integration.py:323`. | Replace tests' direct calls with `sidebar.item(PageKey.TODAY).click()`. | Low; confirm no external UI extension caller. |
| `app/ui/main_window.py:236` `nav_layout` | Definition + `tests/test_practice_ui.py:135-136`. | Test should inspect `sidebar.items_layout`; remove only this alias. | Low. |
| `app/database/assessment_repository.py:369-397` four review-schedule getters | Definitions only in tracked app/tests/scripts (no callers). | Retire read API conditionally; no direct tests to migrate. **Never drop review_schedule.** | Low–medium, possible external callers. |

`LearningRouteService.get_default_learning_route`, `LearningRouteRepository.get_default_learning_route`, `create_todo`, other `nav_*` aliases, and old schemas are **not** confirmed safe removals. A repository keyword hit alone is insufficient evidence.

## Refactor Candidates

| Module | Responsibility problem | Incremental split / benefit | Migration risk |
|---|---|---|---|
| `app/ui/main_window.py` | ~1,670 lines; multiple independent worker lifecycles and direct Task deletion. | First move replan mutation to a Service, then separate Agent/Approval and Assessment UI controllers. Tests can target transitions independent of Qt. | High: state, worker ownership, Sidebar/Today scroll behavior. |
| `app/database/schema.py` | Current DDL and 24 historical upgrade steps in one file. | Freeze historical SQL in ordered modules; preserve registry and exact DDL. Smaller navigation surface. | High: old user DBs, verifier and fingerprint. |
| `app/services/daily_planner_service.py` | Context enrichment, eligibility/budget, AI fallback and persistence mixed. | Extract pure proposal validation and route-scoped queries; leave orchestration in Service. Easier adversarial AI tests. | High: daily plan behavior and Task state. |
| `app/agent/runtime.py` | ~824 lines coupling prompt construction, context/memory, MCP/Sandbox, tools and telemetry. | Extract request builder and provider scope adapters while preserving a single turn coordinator. Easier protocol/security testing. | High: tool authorization, history and trace. |
| `app/ui/agent_workspace_page.py` | ~709 lines of rendering, approval UI, composer and scroll state. | Separate approval-card rendering and optional header/metadata widget; retain one outer scrollbar and session-specific epoch. | Medium: Qt ownership, UX-3 scroll/drafts. |
| `app/ui/routes_page.py` / `app/ui/practice_page.py` | Large dialogs with workers and repeated dynamic layout construction. | Isolate lifecycle ownership/scroll helper **after** targeted regressions. | Medium: modal worker teardown/layout. |

No verified cyclic dependency was established in this audit. Moving code just to lower line counts is not a goal.

## Test Cost Audit

`tests/conftest.py:46-55` already provides an **intentional fast fresh-schema DB path**; do not replace it with real migrations for every test. Real migrations belong in focused migration/release tests. The function-scoped DB and QSettings isolation (`conftest.py:29-55`), Qt widgets/events, repeated historical migrations in designated tests, explicit waits (`tests/test_main_window.py:539-550`; `tests/test_agent_conversation_scroll.py:100`) and >2,000 selected cases dominate aggregate cost. A `waitUntil(timeout=7000)` is a *ceiling*, not necessarily seven seconds of runtime. Markers currently derive from filenames and `qtbot` (`tests/conftest.py:458-477`), so `-m 'not slow'` is broad; collection should be audited when changing tiers. Shadowed AI Review tests (P1-10) undermine coverage regardless of runtime.

**Recommended default Pi test matrix** (proposed policy; exact paths chosen per diff):

| Tier | When | Selection / examples |
|---|---|---|
| 1 — targeted fast | Every edit | Small relevant pure/Service unit files, e.g. `tests/test_agent_error_mapping.py`, `tests/test_task_service.py`; no Qt unless changed. |
| 2 — affected regression | Before handoff | Adjacent DB, worker, Qt and migration regression files for the changed seam, e.g. `tests/test_today_scroll_preservation.py`, `tests/test_agent_workspace_binding_ui.py`, `tests/test_past_task_confirmation.py`. |
| 3 — not-slow | Integration checkpoint, not each edit | `.venv/bin/pytest -m 'not slow' -q` **after marker repair**; currently still broad/slow and should not be called a quick tier. |
| 4 — full release | Final release/large cross-cutting change | `.venv/bin/pytest -q`, once on an exact commit candidate; required for migration/Qt lifecycle work when shipping. |
| 5 — explicit flaky/stress | Only on justified lifecycle/stability tasks | Bounded repetitions of focused Qt/stress files, never a default collector loop. |

For planner fixes use `test_scheduler_minute_budget.py`, multi-route/carry-over and affected Task transition tests. For verifier fixes use populated old-schema and fingerprint tests using **real** migration path. For Qt workers run targeted teardown tests before a release gate. No tests were run in this audit; `pytest --collect-only` was permitted but unnecessary for source-only conclusions.

## Documentation Drift

- `docs/AGENT_MEMORY.md:35,105` and `docs/AGENT_TRACE_EVAL.md:21,70` mingle true introduction versions (v22/v23) with obsolete *current* fingerprint/schema claims (v5/v6/v24).
- `docs/AGENT_APPROVALS.md:42` and `docs/ARCHITECTURE.md:84,110-121` assert current v24/v6 and future Assessment/Note actions, although `app/agent/approval/tools.py:87-114` registers those requests and current versions are 25/7/2.
- `docs/ARCHITECTURE.md:74-75` describes Workspace as exclusively a Today subflow with no Sidebar session navigation; `app/ui/components/navigation.py:234-300` and `app/ui/main_window.py:441-501` now provide direct Session navigation. `README.md:7` also calls Agent Session future work.
- `docs/MCP.md:19,40` claims an exact stdio environment; `app/agent/mcp/client.py:72-83` supplies explicit extras but the installed SDK includes a baseline environment. This is a documentation mismatch, **not evidence of credential-variable inheritance**. Confirm SDK behavior before changing the transport.

Do not overwrite historical descriptions as if v21–v24 never existed; label *then* versus *current*.

## Recommended Cleanup Roadmap

| Phase | Scope | Risk / benefit | Tests required when implementing |
|---|---|---|---|
| **Cleanup-1 — protect behavior** | Fix P1-01/02 together in Service-owned replan; P1-03/04/05 in pure planner validation; P1-07 multiline reads. Handle each as small separate change/commit. | High domain risk, immediate Task/history and Agent correctness benefit. | Agent Session/Workspace replan regression, paused routes, cancelled/cross-route carry-over, minute budget; managed/local sandbox reads; then relevant integration. |
| **Cleanup-2 — historical and Qt integrity** | Address P1-06/P2-03 projection-aware verifier without altering history; track/drain P1-08/09 dialog workers; replace Practice timers (P2-02). | High migration/Qt risk; prevents false verification and shutdown hazards. | Real populated v1–v3/v3–v6 snapshot migrations; worker teardown, Practice scroll last-wins/manual/close; Qt focused repetitions; full release gate. |
| **Cleanup-3 — development efficiency** | Consolidate shadowed tests, explicit marker policy, remove only confirmed test-only aliases/accessors, update docs current-state statements. | Low–medium; faster reliable feedback and clearer onboarding. | `--collect-only` node/marker assertions, targeted UI/legacy tests, no DB migration. |
| **Cleanup-4 — bounded architectural extraction (optional)** | Split one orchestration seam at a time: MainWindow, then Planner/Runtime; isolate historical migration modules only with strong old-DB tests. | High churn if combined; value is ownership clarity, not smaller files alone. | Targeted contract tests each step, migration verifier for schema moves, full suite at release. |

**Audit boundary:** report only. Every recommended fix requires separate approval and implementation; this document does not authorize rewriting historical data or weakening Agent/Workspace permissions.
