# Implementation Plan

| | |
|---|---|
| Status | Draft v0.1 — track progress in `STATUS.md` |
| Approach | Spike first, then a **thin vertical slice through every stage**, then deepen. Live view is built in from the start, not added at the end. |
| Sizing | S ≈ ≤1 day, M ≈ 2–3 days, L ≈ 4–6 days (single developer, rough). Calendar time to be set once the deadline is known. |

## Guiding rules
1. Safety and secrecy features (tool gate, encryption, redaction) land **before** any agent touches a real app.
2. Every milestone ends with something demonstrable and tests passing (`bash backend/scripts/test.sh`, lint, mypy).
3. Rule-based and deterministic pieces first; LLM pieces behind interfaces with a fake model for tests.
4. Regenerate the frontend client (`openapi-ts`) after every API change.
5. Update `STATUS.md` and the relevant doc when a decision changes.

## Fast-track cut (deadline: as soon as possible)

Because the deadline is ASAP, build in two tiers. **Tier A is the demo-able MVP and must be done first**; Tier B is added only after Tier A works end to end.

| Tier A — MVP (do first) | Tier B — after MVP |
|---|---|
| M0 spike (compressed) | AGE graph projection (use a recursive query over `transition` for `kb_path` first; project to AGE afterward) |
| M1 foundations: schema, secrets, gate, event stream, runtime | Graph view UI, run replay scrubbing |
| M2 BRD ingest → requirements with flags | Critic agent (grounding is checked in code already) |
| M3 + M4 crawl: deterministic explorer first, then supervisor + explorers | Human OTP flow UI, `storage_state` import, TOTP (unit-tested only) |
| M5 multi-role (two roles, permission matrix) | Version-diff UI, bulk operations beyond approve/reject |
| M6 mapping + gap report | Excel styling polish (CSV/JSON first) |
| M7 generation (rules first, then LLM author) | Confluence pull, OCR |
| M8 review table, RTM, CSV/JSON/Excel exports | Replay verification as a product feature (still run as an eval script) |
| M9 evaluation + docs + demo | **M10: Jira / TestRail / Zephyr (last, as agreed)** |

Practical speed-ups:
- Use **Tier 1 demo app (this repo's template)** for all early development; move to Juice Shop and OrangeHRM/Redmine only after the loop works.
- Write sample BRDs early (they are also test fixtures for M2/M6/M7).
- Keep the deterministic BFS explorer working at all times; it unblocks mapping and generation if agent behavior is still being tuned.
- Build the live event feed first, then reuse it everywhere; screenshots-after-each-action is enough (no video stream).
- Fake chat model for tests so most work doesn't need paid LLM calls.

## Milestone overview

| # | Milestone | Size | Outcome |
|---|---|---|---|
| M0 | Spike & environment | M | Libraries verified on the target Python; OpenRouter tool calling works; one agent streams events to a page |
| M1 | Foundations | L | Redis, worker, schema, secrets, LLM gateway, agent runtime, tool gate, event pipeline |
| M2 | Projects, roles, BRD ingest | L | Upload BRD → reviewable requirements with IDs and flags |
| M3 | Crawler vertical slice | L | Single role, deterministic explorer through the gate, KM rows, crawl log, live view |
| M4 | Knowledge model & agentic explorer | L | Supervisor + explorer sub-agents, graph projection, embeddings, JSON export |
| M5 | Multi-role & login challenges | M | Parallel per-role exploration, permission matrix, TOTP/session refresh/human OTP |
| M6 | Mapping & gap report | M | Requirement↔screen mapping and two-way gaps, UI |
| M7 | Test generation | L | Rule engine, author agents, renderer, validator, dedup |
| M8 | Review, RTM, exports | M | Edit/approve/regenerate, RTM, CSV/JSON/Excel |
| M9 | Evaluation, docs, demo | M | Metrics, evaluation report, sample outputs, README, demo run |
| M10 | Stretch | — | Jira/TestRail/Zephyr, replay verification, Confluence pull |

---

## M0 — Spike & environment
**Goal:** de-risk the stack before building on it.
- Check `uv sync` on Python 3.14 with `langchain`, `langgraph`, `langchain-openai`, `langgraph-checkpoint-postgres`, `playwright`, `arq`, `docling`, `sentence-transformers`; record results. If anything fails, decide: lower Python floor or swap library.
- Add `redis` to `compose.yml`; add a `worker` target with Chromium.
- Add demo target services behind profiles `demo-juice` and `demo-hrm` (TRD §2.1); verify they start and that the worker can reach them by service name. Developer creates the test accounts manually.
- Draft the first sample BRD (`demo/brds/template-app.md`) for the Tier 1 app.
- OpenRouter: tool-calling + structured output with 2–3 candidate models; confirm privacy-routing parameters, usage/cost fields, embeddings availability.
- AGE under psycopg: create graph, run Cypher via a pooled connection (`LOAD 'age'`, search_path).
- Minimal vertical: arq job runs a LangGraph agent with one Playwright tool against the template frontend → events to Redis Stream → SSE endpoint → bare React page printing events.
- **Exit:** a written spike result in `dev_docs/spike-results.md`; decisions D-list in TRD updated; go/no-go on each library.

## M1 — Foundations
- **Schema/migrations** for core, runs, KM, tests (see `backend-schema.md` §9); SQLModel models split into `app/models/`.
- **Config**: new env vars; `Settings` additions; `.env.example` updated (no values).
- **Security**: Fernet crypto module, `role_credential` storage, secret resolver (worker only), outgoing-payload canary scanner.
- **LLM gateway**: model factory, metering callback, retry/backoff, concurrency limit, fake model for tests.
- **Agent runtime**: `AgentSpec` + validator, budget enforcer, subgraph builder, supervisor skeleton, trace/event publisher (Redis + Postgres).
- **Tool Gate**: authorize, budget, safety classifier, request guard, result sanitizer, logging — with unit tests against a fixture page.
- **Jobs**: arq worker settings, `run` lifecycle, control channel (pause/resume/stop), checkpointer wiring.
- **API**: runs endpoints, SSE stream, control, events.
- **Exit:** a scripted fake agent run executes end-to-end, events stream and persist, stop/pause work, canary-secret test passes.

## M2 — Projects, roles, BRD ingest
- Project + role CRUD (credentials write-only), config forms.
- Artifact store; BRD upload; Docling/fallback parsing; chunking with section paths and page numbers.
- Requirement extractor (structured output, per-section), ID assignment/preservation, source anchors, ambiguity heuristics + LLM flags.
- Requirements API + UI (table, source quote, edit/approve, flags).
- Embeddings for requirements.
- **Exit:** a sample BRD yields requirements with stable IDs and flags; tests with a fake model; manual review on a real BRD.

## M3 — Crawler vertical slice (deterministic first)
- Browser module: context per role, login recipe execution, a11y snapshot → handles, fingerprinting, settle detection, screenshots.
- Browser tools + recording tools via the gate; safe-mode request guard.
- `BfsExplorer` (no LLM): nav chrome seed, link/tab/modal exploration, form field extraction, validation probing.
- KM repository: write states/elements/forms/fields/transitions; crawl log.
- Live Run UI v1: event feed, screenshot panel, counters.
- **Exit:** crawling the fixture app (this repo's template frontend, superuser role) finds login-protected pages, Items table, Add Item modal/form with validations; **no data mutated** (row counts unchanged); crawl log shows blocked Delete.

## M4 — Knowledge model & agentic explorer
- Supervisor planning loop; `spawn_agent`/`await`/`coverage_report`/`replan`; explorer sub-agent graph using the same tools.
- Prompts (versioned) for supervisor and explorer; state summaries (LLM) + embeddings.
- AGE projection; `kb_*` tools; KM JSON export; snapshot lifecycle + staleness.
- Agent tree + per-agent feed in the UI.
- Compare agentic vs BFS (states found, steps, cost) — first evaluation datapoint.
- **Exit:** supervisor spawns ≥2 explorers on different modules; coverage report drives a replan; trace viewable.

## M5 — Multi-role & login challenges
- Parallel explorers per role × module with Redis state claiming.
- Role-state access + element visibility capture; forbidden-route probing; role matrix view.
- TOTP generation; session-timeout detection + re-auth + resume; `needs_human` OTP flow with UI prompt card; `storage_state` import option; CAPTCHA documented behavior (clear failure message).
- **Exit:** two roles (superuser vs normal user on the fixture) produce a correct permission matrix (e.g. Admin page visible only to superuser).

## M6 — Mapping & gap report
- Retriever (pgvector + trigram), judge agent with candidate-constrained output, thresholds, confidence/rationale.
- Gap analysis both directions; manual confirm/reject; mapping UI + Gaps UI; graph view.
- BRD-guided exploration hint: supervisor passes BRD screen names to explorers (feedback loop between M4 and M6).
- **Exit:** labelled mapping set for the demo/fixture reaches target precision/recall (see PRD).

## M7 — Test generation
- Step schema, value specs, renderer (labels + nav path from `kb_path`).
- Rule engine: field validation, boundary (from min/max/length/pattern), equivalence classes, required/format negatives, role-access tests from matrix, basic positive flows.
- Test-author agent: business-rule and E2E scenarios; priority; expected results; uses `gen_*` tools.
- Validator (grounding, reachability), dedup (signature + cosine), trivial filter, one feedback regeneration; critic pass.
- Template-driven IDs/columns.
- **Exit:** for the fixture: 0 grounding violations; duplicates under threshold; sampled cases replay in Playwright (manual script) with ≥90% executable.

## M8 — Review, RTM, exports
- Test-case table + drawer, edit/approve/reject/bulk, versioning, regenerate with feedback (case/requirement level), `needs_regen` on requirement edit.
- RTM view + export; Excel/CSV/JSON for tests, RTM, gaps, KM; template-based column mapping.
- Metrics page: stage timing, tokens, cost per agent/stage.
- **Exit:** full flow BRD → exports demonstrated; RTM counts reconcile with the test table.

## M9 — Evaluation, documentation, demo
- Evaluation harness: coverage %, grounding/hallucination (automatic), executable rate (replay sample), duplicate rate, mapping P/R, QA rating sheet, cost/time.
- Agentic vs deterministic explorer comparison table.
- `docs/evaluation.md`, `docs/samples/*` (KM JSON, tests CSV/Excel/JSON, RTM, gap report), README + setup instructions, `docs/architecture.md` refreshed to what was built, demo script and run on the provided app.
- **Exit:** deliverables checklist (PRD §10) complete.

## Dependencies and critical path
```
M0 → M1 → M2 ─────────────┐
        └→ M3 → M4 → M5 ──┼→ M6 → M7 → M8 → M9
                          │
M2 (requirements) feeds M6; M3's KM feeds M6/M7
```
M2 and M3 can proceed in parallel after M1.

## Risk-driven checkpoints
| Checkpoint | After | Question | Fallback |
|---|---|---|---|
| C1 | M0 | Do the libraries run on the chosen Python? | Lower `requires-python`; swap library |
| C2 | M0 | Does a candidate model do reliable tool calls? | Pick another model; use deterministic explorer |
| C3 | M3 | Is state fingerprinting stable (no explosion/duplication)? | Tune normalization; cap states |
| C4 | M4 | Does agentic beat BFS enough to justify cost? | Default to BFS + agentic summaries |
| C5 | M7 | Are grounding violations truly zero and cases useful? | Tighten schema/validator; more rules, fewer LLM cases |

## Definition of done (per task)
Code typed and linted (`ruff`, `mypy`), tests added, migrations included, API client regenerated if API changed, docs/STATUS updated, no secrets in code or logs.

## Decisions received
1. Deadline: as soon as possible → fast-track cut above.
2. Demo apps: open-source, run locally via compose profiles; accounts created by the developer and entered in the UI (no seeded credentials).
3. Jira/TestRail/Zephyr: last (M10).

## Open items needing input
1. Preferred OpenRouter models and a spend budget for development and evaluation runs.
2. Confirm the Tier 3 demo app (OrangeHRM vs Redmine) after checking image availability in M0.
3. Any real BRD or sample test-case format you can share later; until then we use authored BRDs and the default template.
