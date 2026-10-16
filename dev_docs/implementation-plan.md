# Implementation Plan

| | |
|---|---|
| Status | Draft v0.1 - track progress in `STATUS.md` |
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

| Tier A - MVP (do first) | Tier B - after MVP |
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
| M10 | Integrations | - | **Done 2026-10-16**: Jira Cloud, TestRail, Zephyr Scale (bring your own key, per user); replay verification (script) and Confluence pull also built |

---

## M0 - Spike & environment  ✅ DONE (2026-10-15 - see `spike-results.md`)
_Outcome: go. Embeddings changed to 1024-d via OpenRouter; `confidential` flag added; compact snapshots/settle detection added to M3 scope. Still pending from M0: measure free-tier daily cap, verify OrangeHRM (or use Redmine), compare tool-calling across free models, test arq run and pooled AGE connection from Python._
**Goal:** de-risk the stack before building on it.
- Check `uv sync` on Python 3.14 with `langchain`, `langgraph`, `langchain-openai`, `langgraph-checkpoint-postgres`, `playwright`, `arq`, `docling`, `sentence-transformers`; record results. If anything fails, decide: lower Python floor or swap library.
- Add `redis` to `compose.yml`; add a `worker` target with Chromium.
- Add demo target services behind profiles `demo-juice` and `demo-hrm` (TRD §2.1); verify they start and that the worker can reach them by service name. Developer creates the test accounts manually.
- Draft the first sample BRD (`demo/brds/template-app.md`) for the Tier 1 app.
- OpenRouter: tool-calling + structured output with 2–3 candidate models; confirm privacy-routing parameters, usage/cost fields, embeddings availability.
- AGE under psycopg: create graph, run Cypher via a pooled connection (`LOAD 'age'`, search_path).
- Minimal vertical: arq job runs a LangGraph agent with one Playwright tool against the template frontend → events to Redis Stream → SSE endpoint → bare React page printing events.
- **Exit:** a written spike result in `dev_docs/spike-results.md`; decisions D-list in TRD updated; go/no-go on each library.

## M1 - Foundations  ✅ DONE (2026-10-15)
_Delivered and verified: 113 tests (58 template + 55 new), ruff + mypy strict clean on new code, live end-to-end through real API + arq worker + SSE locally and in docker (pause: 0 events; stop: all agents stopped; persisted events == streamed; replay by `after`/`Last-Event-ID`; unauthenticated stream → 401). Real Groq models verified through the gateway and gate._

**Deviations from the original task list**
- Tables created now: `project`, `app_role`, `role_credential`, `run`, `run_stage`, `agent_run`, `agent_event`, `crawl_event`. KM, BRD, mapping and test-case tables are created in their own milestones (avoids untested schema churn). `run_stage` exists but is not populated yet.
- Models live in `app/qa/models/` next to the untouched template `models.py` (rather than splitting it).
- `echo` run kind (scripted supervisor + parallel scripted sub-agents) exercises the runtime without an LLM or browser; real kinds arrive in M3+.
- Embeddings/chat configured separately (`LLM_*`, `EMBEDDING_*`); see TRD §2, D8.
- Added: isolated test database (D13), checkpoint setup at startup (D14), persist-before-terminal-event and ordered, graceful flushing (D15), SSE auth design (D16).
- Alembic `include_object` now excludes `checkpoint*` tables **and AGE's `ag_*` catalog tables** (autogenerate otherwise proposed dropping AGE's catalog).

**Bugs found by live testing and fixed (each has a regression test where practical)**: agent rows stuck at `running` (concurrent/cancelled flushes; terminal event before persistence), control events not persisted, unauthorized-tool probing not consuming budget.

**Still open for later milestones**: arq job-level `abort` is not used (control is cooperative via Redis flags); `run_stage`/token cost rollups; per-agent cost; credentials decryption/auth module (M3); Playwright browsers in the worker image (M3).

### Original M1 task list (for reference)
- **Schema/migrations** for core, runs, KM, tests (see `backend-schema.md` §9); SQLModel models split into `app/models/`.
- **Config**: new env vars; `Settings` additions; `.env.example` updated (no values).
- **Security**: Fernet crypto module, `role_credential` storage, secret resolver (worker only), outgoing-payload canary scanner.
- **LLM gateway**: model factory, metering callback, retry/backoff, concurrency limit, fake model for tests.
- **Agent runtime**: `AgentSpec` + validator, budget enforcer, subgraph builder, supervisor skeleton, trace/event publisher (Redis + Postgres).
- **Tool Gate**: authorize, budget, safety classifier, request guard, result sanitizer, logging - with unit tests against a fixture page.
- **Jobs**: arq worker settings, `run` lifecycle, control channel (pause/resume/stop), checkpointer wiring.
- **API**: runs endpoints, SSE stream, control, events.
- **Exit:** a scripted fake agent run executes end-to-end, events stream and persist, stop/pause work, canary-secret test passes.

## M2 - Projects, roles, BRD ingest  ✅ DONE (2026-10-15)
_Projects/roles landed in M1/M3. BRD ingestion delivered and measured on the real model (see `extraction-results.md`: 27/27, 4/4 vague flagged, 0 invented, on MD/DOCX/PDF)._

**What exists now**
- Parsing (`qa/brd`): PDF (PyMuPDF: font-size headings, ruled tables, bullets, repeated headers/footers dropped, page numbers), DOCX (styles, tables, lists), Markdown/text. Format detected from the bytes. A **word-coverage check** warns when parsing drops text. Scanned PDFs are rejected with a clear message (no OCR yet).
- Section-aware chunking keeping section path and pages; tables split by rows with the header repeated.
- Extraction: one structured-output call per chunk (parallel, retried); each requirement must carry a **verbatim quote that is verified against the source** (near-misses are replaced by the document's own sentence; anything else is discarded); duplicates merged; BRD-native ids kept, others numbered `REQ-001…` and never colliding with the project.
- Ambiguity: deterministic wording checks (vague terms, TBD/placeholders, unmeasurable NFRs; "no acceptance criteria" is an info note, not a block) merged with the model's own judgement; flagged requirements **cannot be approved** until a human rewrites them or explicitly accepts the wording. Editing re-runs the checks.
- API: upload (20 MB cap, byte-sniffed type, hash de-duplication, safe storage path), list/read/delete, chunks viewer, re-extract (keeps reviewed requirements, replaces the rest), requirements list/filter/detail-with-source, edit, approve/reject/accept-wording/reset, bulk, manual add, summary.
- Embeddings (OpenRouter, 1024-d, HNSW index) stored per requirement; cleared on edit.
- UI: Requirements tab (upload, live processing status, summary, filters, bulk approve), review drawer (reasons, highlighted verbatim source, edit/approve/reject/accept), ingest on the Live Run page (sections, requirements, flagged, discarded counters).
- Sample BRDs in `demo/brds/` (MD/DOCX/PDF + gold lists), builder script, evaluation script.

**Deviations / not done**
- Merge/split of requirements, Confluence import, OCR and a BRD source viewer page (the drawer shows the section instead) are not built.
- Ingest uses the generic run infrastructure; the API holds no LLM keys, so a missing model is reported by the worker as a failed document with a readable reason.
- Embedding calls are best-effort (a failure never blocks ingestion); requirements without embeddings are re-embedded when mapping needs them (M6).

### Original M2 task list (for reference)
- Project + role CRUD (credentials write-only), config forms.
- Artifact store; BRD upload; Docling/fallback parsing; chunking with section paths and page numbers.
- Requirement extractor (structured output, per-section), ID assignment/preservation, source anchors, ambiguity heuristics + LLM flags.
- Requirements API + UI (table, source quote, edit/approve, flags).
- Embeddings for requirements.
- **Exit:** a sample BRD yields requirements with stable IDs and flags; tests with a fake model; manual review on a real BRD.

## M3 - Crawler vertical slice (deterministic first)  ✅ DONE (2026-10-15)
_Delivered and verified on three targets: the local **fixture SPA** (CI: 182 backend tests (124 new) incl. real Chromium), the **template app** (host worker, Admin + User roles), and **OWASP Juice Shop** (container worker, Admin + Customer, through the real UI)._

**What exists now**
- Browser layer (`qa/browser`): one logged-in Playwright context per role; code-only login (heuristics + recipe overrides, overlay dismissal, form-scoped submit); page extraction with opaque handles, regions, forms, overlays; settle detection; validation probing without submit; fingerprinting; route templating incl. hash routes.
- Tool gate integration: `browser_snapshot/click/fill/navigate/back`, `probe_validation`, `mark_skipped`; **observation auto-records** into the knowledge model (states, elements, forms, fields, transitions, role access, screenshots); network guard with transport/external-origin rules; safety classifier; submit detection.
- `BfsExplorer` (deterministic) and the `crawl` run kind (parallel roles, snapshot lifecycle, partial results).
- Knowledge model tables + migration, and the read API: snapshots, states (filters), state detail, role matrix v1, JSON export, crawl log, screenshots.
- **UI** (earlier than planned): Projects list/create, project page with Roles (write-only credentials), Crawl runs, Screens browser with detail drawer, Role access matrix, Crawl log, and the **Live Run page** (SSE agent tree, feed, screenshot, counters, pause/resume/stop, replay of finished runs).
- Infra: `worker` image with Chromium, shared `artifacts` volume, Juice Shop profile.

**Deviations / scope that moved**
- Projects & roles UI (planned M2) and the role matrix v1 + JSON export (planned M4/M5) were pulled forward because the crawl needs a way to be configured and inspected.
- Not done from the original list: `browser_select/press/wait`, wizard-step detection, observed API endpoints, forbidden-route probing, the crawl-config form (limits are set through the API), AGE projection and embeddings (M4).

**Findings that shaped the design** (full list: TRD D17–D25): region must come from `<main>` first; app-shell toggles must never be clicked or fingerprinted; same-document `#fragment` navigation does not reset an SPA (use a fresh load); realtime transports POST but are not mutations; denylist verbs, not nouns; icon-font ligatures are text; user data in headings splits one screen per user; a budget stop is a partial result.

**Known limitations**
- Data-dependent variants: a page whose controls depend on data (an empty Items page vs one with rows; a basket) is recorded once per variant. They are grouped by route in the UI, but not yet merged (M4: summaries + canonical-screen grouping).
- Deterministic explorer only; click exploration explores one representative of repeated controls.
- A worker inside docker cannot crawl an app whose *browser* calls an API on `localhost` (the template app): use a host worker or a self-contained target.
- No MFA/CAPTCHA/session-refresh yet (M5); screenshots may contain prefilled personal data.
- Crawl limits are API-only; the UI has no config form yet.

### Original M3 task list (for reference)
- Browser module: context per role, login recipe execution, a11y snapshot → handles, fingerprinting, settle detection, screenshots.
- Browser tools + recording tools via the gate; safe-mode request guard.
- `BfsExplorer` (no LLM): nav chrome seed, link/tab/modal exploration, form field extraction, validation probing.
- KM repository: write states/elements/forms/fields/transitions; crawl log.
- Live Run UI v1: event feed, screenshot panel, counters.
- **Exit:** crawling the fixture app (this repo's template frontend, superuser role) finds login-protected pages, Items table, Add Item modal/form with validations; **no data mutated** (row counts unchanged); crawl log shows blocked Delete.

## M4 - Knowledge model & agentic explorer  ✅ DONE (2026-10-16; remainder of the M4 list built, measured in `docs/evaluation.md` §5)
- Supervisor planning loop; `spawn_agent`/`await`/`coverage_report`/`replan`; explorer sub-agent graph using the same tools.
- Prompts (versioned) for supervisor and explorer; state summaries (LLM) + embeddings.
- AGE projection; `kb_*` tools; KM JSON export; snapshot lifecycle + staleness.
- Agent tree + per-agent feed in the UI.
- Compare agentic vs BFS (states found, steps, cost) - first evaluation datapoint.
- **Exit:** supervisor spawns ≥2 explorers on different modules; coverage report drives a replan; trace viewable.

## M5 - Multi-role & login challenges  ✅ DONE (2026-10-16)
- Parallel explorers per role × module with Redis state claiming.
- Role-state access + element visibility capture; forbidden-route probing; role matrix view.
- TOTP generation; session-timeout detection + re-auth + resume; `needs_human` OTP flow with UI prompt card; `storage_state` import option; CAPTCHA documented behavior (clear failure message).
- **Exit:** two roles (superuser vs normal user on the fixture) produce a correct permission matrix (e.g. Admin page visible only to superuser).

## M6 - Mapping & gap report  ✅ DONE (2026-10-15 - measured in `mapping-results.md`)
_Delivered: a `map` run (live, like crawl/ingest) that links every non-rejected requirement to the screens that implement it, with a confidence, a one-line rationale and evidence labels verified to exist on the screen; two-way gap report; manual confirm / reject / link; UI and CSV/JSON gap export. 270 backend tests; app code ruff + mypy strict clean; UI driven end to end in a browser._

**What exists now**
- `qa/mapping/cards.py` (screen cards, variant grouping, shell-control separation), `retrieve.py` (IDF lexical + cosine, reciprocal-rank fusion), `judge.py` (candidate-constrained structured output, evidence verification, spread/limit rules), `gaps.py` (derived two-way gaps), `runs/kinds/map.py`.
- API (`api/routes/mapping.py`): run, summary, mappings (list/confirm/reject/add by hand), gaps (list/accept/reopen/export).
- UI: **Mapping** tab (requirement → screens with evidence and Confirm/Reject/Undo; Requirement gaps; Undocumented screens; "Link a screen / requirement" dialog; partial-crawl warning; CSV/JSON export) and live run page counters/feed for `map` runs.
- Migration `e5f6a7b8c9d0`: `requirement_mapping`, `gap`, `ui_state.embedding`.

**Deviations from the original task list**
- No "BRD-guided exploration hint" to the supervisor (that needs the M4 agentic explorer).
- No graph view (needs the M4 AGE projection).
- Cosine is computed in Python over the snapshot's cards, not as a pgvector query (D34).
- Gap statuses are `open|accepted|resolved`; the UI uses open/accepted/reopen.

**Known limitations** (details and numbers in `mapping-results.md`)
- A card holds only what the crawler recorded (controls, headings, form fields). Requirements about what a screen *displays* (columns, totals, "cards with image and price") are often not provable from it and become gaps. Capturing masked static text / table headers is the main accuracy lever; it needs a crawler change and a decision about personal data in staging screens.
- Gaps on a partial crawl may only mean "not reached" (warned in the UI).
- Partial matches (55–70%) can link a loosely related screen; they are shown as weak and need a person's confirmation.
- Judge variance between identical runs was measured and reduced (D37), not eliminated.
- Gold labels are written by the tool's developer, so the numbers are a regression check, not independent accuracy.
- Mapping is run per snapshot; it is not yet re-triggered when a requirement is edited (re-run mapping).

## M7 - Test generation  ✅ DONE (2026-10-16 - measured in `generation-results.md`)
_Delivered: a `generate` run (live) producing grounded, structured test cases per requirement from rules and an author agent; Test cases tab; dry-run replay against the live app (92% executable on the template app). 286 backend tests; app code ruff + mypy strict clean; UI driven end to end._

**What exists now**
- `qa/generation/`: `schema.py` (structured steps + deterministic renderer, signatures), `rules.py` (field/boundary/format/option/positive/role-access rules), `gather.py` (forms with planned paths, role access), `author.py` (candidate-constrained scenario writer + grounding), `validate.py` (trivial filter, exact and semantic de-dup), `runs/kinds/generate.py`; `qa/mapping/paths.py` (click paths planned over the recorded transitions).
- API: start generation, list/filter, summary, read, approve/reject/back to draft. Table `test_case` (migration `f6a7b8c9d0e1`).
- UI: **Test cases** tab (filters, detail drawer showing steps, data, expected result and how that result is known) and live run page for `generate`.
- `scripts/replay_cases.py`: dry-run replay (an executability check).

**Deviations from the original task list**
- No separate critic agent pass; the validator is deterministic. No `gen_*` tool calls: rules run in code, the author returns structured output.
- Test-case template (column set/order, ID format) is not configurable yet; `TC-0001` and fixed columns (arrives with exports in M8).
- `test_case_version` / `test_case_requirement` tables wait for M8 (history); `also_covers` in `evidence` carries the extra requirement links meanwhile.
- Exit criterion "sampled cases replay with ≥90% executable" met on the template app (92%, 23 of 25), not on Juice Shop (accounts must be re-created).

**Known limitations** (details in `generation-results.md`): expected results are often inferred (the crawler never submits); forms behind unlabelled controls are skipped; per-row cases need data; author scenarios are grounded in labels, not proven in behaviour.

## M8 - Review, RTM, exports  ✅ DONE (2026-10-16)
_Delivered: review of generated cases (edit, version history and restore, bulk approve/reject, regeneration with reviewer feedback), requirement traceability matrix that reconciles with the test table, Excel/CSV/JSON exports for test cases, RTM, gaps and the knowledge model, a per-project test-case template, run metrics. 298 backend tests; UI driven end to end in a browser._

**What exists now**
- API: `PATCH /tests/{id}` (new version, status `edited`), `/versions`, `/restore`, `/regenerate` (requirement-level, with a free-text comment passed to the author), `POST /projects/{id}/tests/bulk`, `GET /rtm`, `GET /exports/{tests|rtm|gaps|km}?format=xlsx|csv|json`, `GET/PUT /template`, `GET /metrics`. Table `test_case_version` (migration `a7b8c9d0e1f2`).
- UI: bulk select/approve/reject, case drawer with Edit / History (restore) / Regenerate…, "requirement changed" warning, Traceability matrix sub-tab, Export menu (Excel/CSV/JSON for each artefact), Template dialog (columns, order, headers, ID prefix), Metrics tab.
- `docs/samples/`: real exports for both demo apps.
- Editing a requirement queues its unreviewed cases (`needs_regen`) and marks approved/edited ones; regeneration replaces only drafts and queued cases (D48–D52).

**Exit criterion:** "RTM counts reconcile with the test table" is asserted in a test and checked in the browser (RTM 27 test cases = table 27 = Excel rows 27).

**Deviations**
- Regeneration is per requirement (reachable from any case), not per single case (D50).
- No stored `export` rows and no `test_case_requirement` table: exports are generated on request, extra requirement links live in `evidence.also_covers`.
- Metrics report time and tokens per kind of run; **cost is not priced** (the provider's token counts only) and there is no per-agent breakdown.
- Editing step text does not re-parse into structured steps (D48).
- Template is JSON-backed and edited in a dialog; no import of an external template file (e.g. an existing Excel layout).
- Knowledge-model export is a screens + fields workbook (and the existing JSON), not the full graph.

## M9 - Evaluation, documentation, demo  ✅ DONE (2026-10-16)
_Delivered: an evaluation harness (`scripts/evaluate.py`), `docs/evaluation.md` with measured results and limitations, a refreshed architecture document with an implementation-status table, a root README with setup, `demo/DEMO.md`, real sample outputs in `docs/samples/`, and a QA rating sheet. 301 backend tests._

**Result against the PRD targets** (full table in `docs/evaluation.md`): grounding 0 of 156 steps; executable 96% (template app; Juice Shop not replayed); duplicates 0%; mapping 0.92/0.92 on a developer-labelled set; **requirement coverage 60–63%, below the 90% target** (crawl reach, features the app lacks, displayed data invisible to the crawler, flagged requirements); business relevance not measured (sheet provided).

**Bugs the evaluation found and fixed**: single-page apps navigated by address had no recorded click path (31 forms unreachable; now opened by route); headings were accepted as clickable targets; drafts of requirements that were no longer generatable stayed behind after a full run; the first duplicate metric counted boundary pairs and different roles as repeats.

**Not delivered**: agentic vs deterministic explorer comparison (the agentic explorer was never built), a QA relevance score, Juice Shop replay, a demo on a customer-supplied application.

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
