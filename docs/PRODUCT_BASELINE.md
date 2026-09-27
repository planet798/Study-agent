# Product Baseline — after S1–S6 (Agent-2 core)

> Canonical product boundary. Product simplification is complete; do not continue product removal. Agent implementation status is recorded below; future capabilities are explicitly marked not implemented.

## Position and surfaces

Study-Agent is an agentic learning environment, not a generic Todo manager, review scheduler, career dashboard or generic chatbot. The current Sidebar has **Today**, **Learning Routes**, **Practice**, and **Settings** (footer).

- **Curriculum**: six canonical learning routes, Route → Phase → Topic → Learning Component → Task; route ownership, isolation and prerequisites remain authoritative.
- **Planning**: Planner hard gates select legal work; Global Scheduler allocates routes with existing budget/fairness semantics.
- **Today**: execution-focused learning surface: date, pending count, estimated minutes, route filter, current phase, Planner status/explanation, 今日学习 tasks and manual learning entry. It does not display JD/Skill dashboard data.
- **Assessment / Mastery**: completed task ≠ mastered. Formal Mastery updates happen only through Assessment; weak points and latest assessment remain evidence for learning decisions.
- **Capability**: Task / Assessment / Experiment / Project evidence contributes according to existing rules. Capability and Mastery remain separate; L5 PROJECT requires confirmed qualifying project-use evidence.
- **Practice**: separate projects, milestones, outputs, requirements/readiness and evidence. AI Profiles / Prompt overrides and the theme system remain active in Settings. TaskReviewService (AI review of the user's *unfinished-task reason*) remains active; it is not the retired Review Scheduler. Obsidian notes remain available.

## Manual learning

| Type | Storage | Outcome |
|---|---|---|
| Learning Activity | `source=manual`, `task_type=manual`, no KP | One-off study behavior; no Assessment, Mastery or Capability evidence |
| Knowledge Learning | `source=manual`, `task_type=new`, Topic or route-scoped temporary KP | Assessment-capable; Assessment is the formal Mastery path |

The new UI requires a route when active learning routes exist. If none are available, NULL is allowed. Historical `task_type=manual, route_id=NULL` rows remain readable and actionable; `create_todo()` is a legacy compatibility alias, not a product entry point.

## Planner inputs and backend signal

Curriculum, Mastery/weak-point evidence, Capability/Practice requirements, Planner feedback and JD market signals can influence legal planning. JD samples → `JdSummaryService` → `MarketSignal` → `SkillService` → Planner. JD maintenance CLI (`add-jd`, `add-jd-summary`, `jd-trends`) remains available; this data is not a Today dashboard.

## Retired product surfaces

Daily Review / Review Scheduler / Daily Retention / generated review tasks; Monthly Dashboard / SummaryService / StatsService / AI Monthly Summary; Today Career Dashboard / JD Trend / Candidate Skill / Skill Gap panels; Generic Todo Manager. No new scheduled review task, Monthly page or generic Todo UI is part of the current product.

## Legacy DB and migration compatibility

`SCHEMA_VERSION = 21`; `FINGERPRINT_VERSION = 5` (v5 protects immutable Agent Session/Message history). Do not drop or rewrite historical `review_schedule`, `knowledge_points.review_count/next_review_date/interval_days`, `weekly_summaries`, `monthly_summaries`, historical `task_type=review` / `source=daily_retention`, manual `task_type=manual` / NULL-route rows, or historical Monthly prompt overrides. Migration Gate / Release Verifier protect historical rows and fingerprints. Legacy data is not a production feature.

## Agent core — Agent-1 / Agent-1.1 / Agent-2 / Agent-3 / Agent-4 / Agent-5 implemented

Planner decides **WHAT** to learn. Agent Runtime helps the user actually learn that task:

```text
Task → Agent Study Session → persistent multi-turn messages
     → AgentRuntime → AgentModelClient → OpenAI-compatible chat completion
```

Implemented:

- **Agent-1**: task-bound session persistence, immutable multi-turn messages, independent `AgentModelClient`, and persistent Runtime.
- **Agent-1.1**: verifier v5 protects immutable Agent Session/Message history while allowing Session lifecycle changes and legitimate new conversation rows.
- **Agent-2**: read-only `AgentToolRegistry` and six task-scoped learning tools; Runtime persists assistant tool calls/results, reconstructs history, and limits tool rounds.
- **Agent-3**: Today task cards open/resume the task-bound internal Workspace. Each user turn gets one compact Service-backed Task Context snapshot; model turns run in `AgentTurnWorker` with a worker-owned SQLite connection. Workspace is not a Sidebar page.
- **Agent-4**: static Agent learning-behavior Skills are selected deterministically from `learning_activity_kind`; they grant no permissions and are not persisted.
- **Agent-5**: optional official-SDK MCP Tools for operator-configured external servers, gated by exact local allowlist plus `readOnlyHint=True`; transport/client lifecycle is per worker turn and no external integration can mutate Study-Agent state.

NOT IMPLEMENTED:

- write tools / approvals / mutation policy
- Sandbox
- memory / context compaction
- trace / evaluation

Agent Tools must call **existing Service → Repository → SQLite**, never Repository or raw SQLite directly. Agent may neither set Mastery nor Capability directly: **Assessment → Mastery** and **Evidence → Capability**. `AgentSkill` / `AgentSkillRegistry` in `app/agent/skills/` are static learning-behavior strategies; current `SkillService` and `skills` table describe career/technical skills and must not be repurposed. See `docs/AGENT_ARCHITECTURE.md`.
