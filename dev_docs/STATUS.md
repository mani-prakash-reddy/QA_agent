# Project Status

_Last updated: 2026-10-16 (M9 done)_

## Overall
Implementation in progress. BRD ingest, the deterministic crawler/knowledge model and requirement↔screen mapping with a two-way gap report work end to end (API, worker, live UI). Grounded test generation (M7) works too; review, RTM and exports (M8) work. The deliverables checklist is complete except the items listed as not built in `docs/evaluation.md` §8 (agentic explorer, MFA/OTP, AGE graph, Jira/TestRail/Zephyr).

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
| Spike results | `dev_docs/spike-results.md` | Done |
| Extraction results | `dev_docs/extraction-results.md` | Done (M2) |
| Mapping results | `dev_docs/mapping-results.md` | Done (M6) |
| Generation results | `dev_docs/generation-results.md` | Done (M7) |
| Evaluation report | `docs/evaluation.md` | Done (M9) |

## Milestones

| # | Milestone | Status | Notes |
|---|---|---|---|
| M0 | Spike & environment | **Done** (2026-10-15) | See `spike-results.md`. Pending small items listed there §6 |
| M1 | Foundations | **Done** (2026-10-15) | 113 backend tests pass; lint + mypy strict clean on new code; verified live in containers. Deviations listed in `implementation-plan.md` M1 |
| M2 | Projects, roles, BRD ingest | **Done** (2026-10-15) | Parse/chunk/extract/ground/flag/review. Measured: `extraction-results.md`. 252 backend tests |
| M3 | Crawler vertical slice | **Done** (2026-10-15) | Deterministic explorer, knowledge model, read API and UI incl. Live Run; verified on fixture, template app and Juice Shop. See plan M3 for limits |
| M4 | Knowledge model & agentic explorer | **Done** (2026-10-16) | Agentic explorer (planner + per-module agents + coverage replan), AGE projection, masked static text, canonical page titles. Compared with the deterministic explorer: not better (evaluation.md §5) |
| M5 | Multi-role & login challenges | **Done** (2026-10-16) | TOTP, human OTP prompt, stored session, CAPTCHA refusal, session re-login, forbidden-route probing, role matrix with observed denials |
| M6 | Mapping & gap report | **Done** (2026-10-15) | Hybrid retrieval + candidate-constrained judge with verified evidence; two-way gaps; manual confirm/reject/link; UI + CSV/JSON gap export. Measured on Juice Shop: `mapping-results.md`. 270 backend tests, UI driven end to end |
| M7 | Test generation | **Done** (2026-10-16) | Rules + grounded author agent, planned click paths, de-dup, Test cases UI; replay 92% executable on the template app. See `generation-results.md`. 286 backend tests |
| M8 | Review, RTM, exports | **Done** (2026-10-16) | Edit/versions/bulk/regenerate with feedback, RTM that reconciles with the table, xlsx/csv/json exports with a configurable template, metrics, `docs/samples/`. 298 backend tests |
| M9 | Evaluation, docs, demo | **Done** (2026-10-16) | `docs/evaluation.md`, README/setup, demo script, samples, harness. Coverage target (90%) **not met** (60-63%); see evaluation §3. 301 backend tests |

## Repo baseline
FastAPI full-stack template; Postgres 18 with Apache AGE + pgvector (migration `a1b2c3d4e5f6`); React frontend with shadcn/ui and TanStack; `.env` (untracked) holds `LLM_*` (Groq) and `EMBEDDING_*` (OpenRouter) settings.

## Decisions made
- Supervisor + sub-agents on LangChain/LangGraph; sub-agents hold browser/crawl tools; supervisor holds orchestration tools (+ read-only KM/BRD queries).
- Live agent view via Redis Streams + SSE; Redis + arq for jobs.
- LLM: Groq (`openai/gpt-oss-120b` / `-20b`) for chat; OpenRouter for embeddings; both OpenAI-compatible and configured separately; self-hosted swap by base URL.
- Steps are structured element references rendered deterministically; rules-based generation for mechanical test types.
- Safe mode enforced at the network layer.
- Discovery is multi-source, not sitemap-only.
- Docs split: `docs/` product documents, `dev_docs/` development documents.
- Deadline is ASAP → two-tier plan (MVP first); see `implementation-plan.md` "Fast-track cut".
- Demo targets are open-source apps in compose profiles (`demo-juice`, `demo-hrm`) plus this repo's template app; the developer creates accounts and enters them in the UI.
- Sample BRDs are authored by us in `demo/brds/` with deliberate gaps.
- Jira/TestRail/Zephyr integration is the final item (M10).
- Embeddings via OpenRouter, 1024-d (`liquid/lfm-2.5-embedding-350m:free` default, `voyageai/voyage-4` optional). Free chat models were replaced by Groq after measurement (0.5–1.3 s/call vs ~10 s).
- `confidential` project flag: free models only for non-confidential data. Groq's data-retention terms are **not yet verified** for confidential BRDs.
- Tests run on an isolated `app_test` database (the template's fixtures wipe users/items; running them on the dev DB deleted the dev users once - superuser restored).
- BRD parsing defaults to PyMuPDF + python-docx; Docling optional.

## Open questions
See `implementation-plan.md` ("Open items needing input") and `TRD.md` §15.

## Next
**All milestones except M10 are done** (M0-M9). What remains: (1) verify the integrations against a real Jira/TestRail/Zephyr trial account; (2) the one target still missed: requirement coverage 60-80% vs 90% (see `docs/evaluation.md` §3: deeper crawls with demo data, a human confirming weak links); (3) a real QA relevance rating (sheets are in `docs/samples/`), a real BRD, a real Confluence and a real scan; (4) the agentic explorer did not beat the deterministic one, so it stays optional. Four apps measured: template, Juice Shop, Redmine, Gitea.
Run counts for the bounded Juice Shop crawl: 36 states, 12 forms, 52 paths in ~2m45s (120 steps per role, partial).

(Earlier note) M2 (projects/roles UI + BRD ingest) and M3 (crawler vertical slice) can proceed in parallel. Pending from M0: measure free embedding rate limits, verify OrangeHRM/Redmine, test AGE from Python under a pooled connection.

## Changelog
- 2026-10-16 - Gap closing against the assignment: estimated dollar cost per run (D68), glossary + OpenAPI reference documents (D65), OCR of BRD diagrams (D67), opt-in multi-step form walking (D66), crawl/generation settings dialog, `docs/privacy.md`, AI-judge proxy for business relevance (D69, `evaluate.py --judge`), access-rights requirements no longer produce field cases, sample exports for all seven apps (`scripts/export_samples.py`). Final measurements in evaluation.md §6b: coverage meets 90% on 1 of 7 apps, judge proxy 2.8-3.5 (target 4.0, AI not human), executable rate 84-97% (template app stale). Not verified: settings dialog in a browser, real Jira/TestRail/Zephyr.
- 2026-10-16 - M10: Jira Cloud, TestRail and Zephyr Scale integrations (bring your own key, per user; sidebar *Integrations* page; *Push to…* on the Test cases tab with preview, create/update/unchanged, per-case results). 13 tests against mocked APIs; end to end against a TestRail stand-in (`demo-testrail-mock`). TRD D64.
- 2026-10-16 - Three purpose-built test apps (`demo/apps`: LeaveTrack, Stockroom, HelpDesk; compose profiles `demo-leave`, `demo-stock`, `demo-help`) with BRDs and answer keys (`demo/build_app_brds.py`). Found and fixed: PDF ligatures broke quote matching, hash-route links ignored by the crawler, sign-in screen not recorded, sign-in cases replayed signed in. See evaluation.md §6a. 339 backend tests.
- 2026-10-16 - UI pass: real Dashboard (project progress stepper, mapped/covered bars, recent runs; `GET /dashboard/`), Items tab removed from the UI, run page agents shown as an inverted tree of cards (latest screen, auto-scrolling log, per-agent pause/stop/feed), template branding removed, Azure OpenAI embeddings (1536-d, D62), login page discovery for hash-routed apps.
- 2026-10-16 - M4/M5 remainder and extras: agentic explorer, AGE projection, masked table headers/captions, login challenges (TOTP, human OTP, stored session, CAPTCHA refusal, re-login on expiry), forbidden-route probing with observed role denials, OCR for scanned PDFs, Confluence import, two more demo apps (Redmine, Gitea; compose profiles `demo-redmine`, `demo-gitea`), `run_pipeline.py` and `compare_explorers.py`. Bugs found on the new apps are listed in evaluation.md §6. 330 backend tests. TRD D53-D61.
- 2026-10-15 - M2 complete: BRD ingest (MD/DOCX/PDF), grounded LLM extraction with verbatim quotes, ambiguity flagging that blocks approval, review UI, embeddings. Real-model results in `dev_docs/extraction-results.md`. Bugs found by tests: PDF one-row box swallowed a requirement; stale embeddings not cleared on edit (NumPy vs None change detection); API wrongly required LLM keys.
- 2026-10-15 - M3 complete: crawler (browser layer, gate tools, deterministic explorer, `crawl` run), knowledge model + read API, and the UI (projects, roles, runs, screens, role access, crawl log, Live Run). 182 backend tests (124 new). `worker` image now includes Chromium; `artifacts` volume added. Found and fixed 15+ crawler issues through real targets (see TRD D17–D25).
- 2026-10-15 - M1 complete: schema (project/role/credential/run/agent/event/crawl tables), credential encryption, secret scanner, LLM gateway (Groq + OpenRouter embeddings), AgentSpec validation, tool gate, network request guard, event pipeline, arq worker, runs API with SSE and pause/resume/stop. `worker` service added to compose.
- 2026-10-15 - M0 spike complete: stack verified on Python 3.14; design changes recorded (1024-d embeddings via OpenRouter, `confidential` flag, compact snapshots, settle detection). `compose.yml` now has `redis` and the `demo-juice` profile.
- 2026-10-15 - Recorded deadline (ASAP), demo-app approach, and integration deferral; added fast-track cut and TRD §2.1.
- 2026-10-15 - Initial documentation set created.
