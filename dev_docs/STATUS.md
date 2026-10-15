# Project Status

_Last updated: 2026-10-15_

## Overall
Planning phase. Product, technical and schema documents drafted; no application code written yet.

## Documents

| Document | Location | Status |
|---|---|---|
| Problem statement | `problem_statement.md` (gitignored) | Provided |
| System architecture | `docs/architecture.md` | Draft v0.1 |
| PRD | `dev_docs/PRD.md` | Draft v0.1 |
| TRD | `dev_docs/TRD.md` | Draft v0.1 |
| Backend schema | `dev_docs/backend-schema.md` | Draft v0.1 |
| App flow | `dev_docs/app-flow.md` | Draft v0.1 |
| Design brief | `dev_docs/design-brief.md` | Draft v0.1 |
| Implementation plan | `dev_docs/implementation-plan.md` | Draft v0.1 |
| Spike results | `dev_docs/spike-results.md` | Not started (M0) |
| Evaluation report | `docs/evaluation.md` | Not started (M9) |

## Milestones

| # | Milestone | Status | Notes |
|---|---|---|---|
| M0 | Spike & environment | Not started | Next up |
| M1 | Foundations | Not started | |
| M2 | Projects, roles, BRD ingest | Not started | |
| M3 | Crawler vertical slice | Not started | |
| M4 | Knowledge model & agentic explorer | Not started | |
| M5 | Multi-role & login challenges | Not started | |
| M6 | Mapping & gap report | Not started | |
| M7 | Test generation | Not started | |
| M8 | Review, RTM, exports | Not started | |
| M9 | Evaluation, docs, demo | Not started | |

## Repo baseline
FastAPI full-stack template; Postgres 18 with Apache AGE + pgvector (migration `a1b2c3d4e5f6`); React frontend with shadcn/ui and TanStack; `.env` holds `OPENAI_API_BASE_URL` / `OPENAI_API_KEY` for OpenRouter (untracked).

## Decisions made
- Supervisor + sub-agents on LangChain/LangGraph; sub-agents hold browser/crawl tools; supervisor holds orchestration tools (+ read-only KM/BRD queries).
- Live agent view via Redis Streams + SSE; Redis + arq for jobs.
- OpenRouter as LLM provider through the OpenAI-compatible API; self-hosted swap by base URL.
- Steps are structured element references rendered deterministically; rules-based generation for mechanical test types.
- Safe mode enforced at the network layer.
- Discovery is multi-source, not sitemap-only.
- Docs split: `docs/` product documents, `dev_docs/` development documents.
- Deadline is ASAP → two-tier plan (MVP first); see `implementation-plan.md` "Fast-track cut".
- Demo targets are open-source apps in compose profiles (`demo-juice`, `demo-hrm`) plus this repo's template app; the developer creates accounts and enters them in the UI.
- Sample BRDs are authored by us in `demo/brds/` with deliberate gaps.
- Jira/TestRail/Zephyr integration is the final item (M10).

## Open questions
See `implementation-plan.md` ("Open items needing input") and `TRD.md` §15.

## Changelog
- 2026-10-15 — Recorded deadline (ASAP), demo-app approach, and integration deferral; added fast-track cut and TRD §2.1.
- 2026-10-15 — Initial documentation set created.
