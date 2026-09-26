# Product Baseline — after S1–S6 (before Agent-1)

> Canonical product boundary. Product simplification is complete; do not continue product removal. Future Agent features below are **NOT IMPLEMENTED YET**.

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

`SCHEMA_VERSION = 20`; `FINGERPRINT_VERSION = 4`. Do not drop or rewrite historical `review_schedule`, `knowledge_points.review_count/next_review_date/interval_days`, `weekly_summaries`, `monthly_summaries`, historical `task_type=review` / `source=daily_retention`, manual `task_type=manual` / NULL-route rows, or historical Monthly prompt overrides. Migration Gate / Release Verifier protect historical rows and fingerprints. Legacy data is not a production feature.

## Agent boundary — NOT IMPLEMENTED YET

Planner decides **WHAT** to learn. A future Agent Runtime may help the user actually learn it:

```text
Task → Agent Study Session → Model + Agent Skills + Tools + MCP + Sandbox
     → Learning Interaction → Assessment / Artifact / Evidence
```

Future Agent Tools must call **existing Service → Repository → SQLite**, never Repository or raw SQLite directly. Agent may neither set Mastery nor Capability directly: **Assessment → Mastery** and **Evidence → Capability**. Future Agent Skills must use distinct names (`AgentSkill`, `AgentSkillRegistry`, `agent/skills/`); current `SkillService` and `skills` table describe career/technical skills and must not be repurposed. None of Agent Runtime, session, Tools, Agent Skills, MCP or Sandbox is implemented at this baseline.
