# Technical Requirements Document (TRD)

| | |
|---|---|
| Status | Draft v0.1 |
| Related | `PRD.md`, `docs/architecture.md`, `backend-schema.md`, `implementation-plan.md` |

This document says **how** the system is built. Design rationale lives in `docs/architecture.md`; table definitions live in `backend-schema.md`.

## 1. Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python ≥3.14 (repo constraint), TypeScript | **Verified in M0:** LangChain/LangGraph/Playwright/psycopg/redis install and run on 3.14; no need to lower `requires-python`. |
| API | FastAPI, SQLModel, Alembic, Pydantic v2 | Existing. Sync `Session` for CRUD routes (existing pattern). |
| Agents | `langchain`, `langgraph`, `langchain-openai`, `langgraph-checkpoint-postgres` | Pin versions in the spike; confirm current prebuilt-agent API names. |
| Browser | `playwright` (async) | Chromium only in v1. |
| Queue | Redis + `arq` | Run jobs, plus Redis Streams for live events and `SET NX` for state claims. |
| DB | PostgreSQL 18, AGE 1.8, pgvector | Existing `db/` image. |
| Embeddings | OpenRouter `/embeddings`, 1024-d. Default `liquid/lfm-2.5-embedding-350m:free` (512-token limit); quality option `voyageai/voyage-4` (paid) | HNSW cannot index >2000 dims, so 2048-d models (nemotron embed) are not used. Vectors are model-specific: store `embedding_model` per row and re-embed on switch. Spike: `spike-results.md` §2. |
| BRD parsing | `pymupdf` + `python-docx` (default); `docling` optional extra | Docling pulls torch/triton (heavy). |
| Exports | `openpyxl`, `csv` | |
| Frontend | React 19, TanStack Router/Query/Table, Tailwind 4, shadcn/ui, hey-api generated client | Existing. |
| LLM | Groq via its OpenAI-compatible API: `openai/gpt-oss-120b` (supervisor, test author, mapper, extractor) and `openai/gpt-oss-20b` (explorer, critic) | `LLM_BASE_URL=https://api.groq.com/openai/v1`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST`. Measured in M0.1: 0.5–1.3 s per call, correct tool calls, strict `json_schema` output. Any OpenAI-compatible endpoint (e.g. OpenRouter) works by changing these variables. |

## 2. Runtime topology (docker compose)

```
db        PG18 + AGE + pgvector                (existing)
redis     redis:7                              (new)
backend   FastAPI: REST + SSE                  (existing, extended)
worker    arq worker + Playwright + LangGraph  (new, replicable; own Dockerfile target with browsers)
frontend  Vite dev server                      (existing)
adminer                                        (existing)

# demo targets (new; all behind compose profiles, never started by default)
juice-shop   profile: demo-juice    OWASP Juice Shop (Angular SPA)
orangehrm    profile: demo-hrm      OrangeHRM + its DB   (or redmine; see §2.1)
```

- `worker` image = backend image + `playwright install --with-deps chromium`.
- Shared volume `artifacts:` mounted at `/data/artifacts` in `backend` and `worker` (BRD files, screenshots, exports). Storage accessed through an `ArtifactStore` interface so S3 can replace it later.
### 2.1 Demo / test target applications

No real application is provided, so we run open-source apps locally. Credentials are **not** seeded by us: the developer creates accounts in each app and enters them in the QA Agent UI (role credentials screen), exactly as an end user would.

| Tier | App | Profile | Why | Roles |
|---|---|---|---|---|
| 1 | This repo's template app (`frontend` + `backend`, already in the default stack) | none (default) | Zero setup; React SPA with login, table, modals, forms with validation, admin area | superuser (`FIRST_SUPERUSER`) and a normal user created via sign-up/admin |
| 2 | OWASP Juice Shop (`bkimminich/juice-shop`) | `demo-juice` | One container; Angular SPA; self-registration; customer vs admin; dynamic menus/dialogs. Intentionally vulnerable, so keep it on the compose network only, never exposed publicly | customer (self-register), admin (account created/known per Juice Shop docs) |
| 3 | OrangeHRM (preferred) or Redmine | `demo-hrm` | Business-style multi-module app with realistic BRD-able requirements and several roles. **Verify the official image/compose and resource needs before committing**; Redmine (official `redmine` image) is the fallback | Admin / ESS (OrangeHRM) or Admin / Developer / Reporter (Redmine) |

Notes:
- Target URLs inside compose use service names (e.g. `http://juice-shop:3000`). The target-URL validator allows hosts listed in `ALLOWED_TARGET_HOSTS` (dev default includes the demo service names); in other deployments private/internal addresses are rejected.
- Demo containers join the same compose network as `worker`; their ports are published to localhost only for manual account setup.
- Sample BRDs live in `demo/brds/` (one per app), written by us. Each deliberately (a) omits some features the app has and (b) includes some requirements the app lacks, so gap reporting and the "flag vague requirements" path can be exercised and measured.
- Demo assets are for development/evaluation and are not part of the product runtime.

- `.env.example` gains (implemented): `REDIS_URL`, `CREDENTIAL_ENCRYPTION_KEY`, `ALLOWED_TARGET_HOSTS`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`. Chat and embeddings are configured **separately** because Groq has no embeddings endpoint. The API service receives only Redis/encryption settings; LLM keys go to the `worker` only. (`ARTIFACT_DIR` arrives in M2.)

## 3. Code layout (additions under `backend/app`)

```
app/
  qa/
    llm/          factory (init_chat_model → ChatOpenAI w/ base_url), metering callback,
                  redaction filter, provider preferences, retry policy
    security/     crypto (Fernet), secret resolver (worker-only), payload scanner
    brd/          loaders, chunker, extractor (structured output), ambiguity heuristics
    agents/
      runtime/    AgentSpec, spec validator, budget enforcer, supervisor graph,
                  subagent subgraph builder, trace/event publisher
      prompts/    versioned prompt templates (*.md / *.jinja)
      tools/      supervisor_tools, browser_tools, kb_tools, brd_tools, gen_tools
    gate/         ToolGate: safety classifier, request guard, budgets, redaction, logging
    browser/      context manager, login recipes, TOTP, session refresh, a11y snapshot,
                  handle registry, state fingerprinting, screenshot capture
    knowledge/    repo (SQL), graph projection (AGE), embeddings, role matrix, JSON export
    mapping/      retriever, judge, gap analysis
    generation/   rules/ (boundary, validation, rbac, positive/negative), llm_author,
                  step schema, renderer, validator, dedup
    outputs/      rtm, exporters (csv/json/xlsx), integrations/ (stretch)
    jobs/         arq worker settings, run entrypoints
  models/         split from models.py: qa_*.py tables (see backend-schema.md)
  api/routes/     projects, roles, brd, requirements, runs, kb, mapping, tests, rtm, exports
```

Rules: no module under `agents/` imports Playwright or a model client directly - everything goes through `gate/`.

## 4. Agent runtime

### 4.1 Graph structure
- **Run graph** (LangGraph, Postgres checkpointer, `thread_id = run_id`): `ingest_check → authenticate → supervise → map → generate → validate → finalize`. Phases can be run individually (e.g. `generate` only) from a stored snapshot.
- **Supervisor node** loops: plan → `spawn_agent` calls → wait → review coverage → replan or `finish`.
- **Sub-agents** are compiled subgraphs parameterised by `AgentSpec`; parallel fan-out uses `Send`. Each runs a tool-calling loop with the spec's tool subset.
- Human input uses LangGraph `interrupt`; the API resumes via the control endpoint.

### 4.2 `AgentSpec`
```python
class AgentSpec(BaseModel):
    kind: Literal["explorer", "test_author", "mapper", "critic"]
    goal: str                         # supervisor-written, length-capped
    scope: Scope                      # role_ref, module, url_globs, max_depth
    prompt_extra: str = ""            # appended after fixed base prompt, length-capped
    resources: Resources              # requirement_ids, state_ids, sample_ids (references only)
    tools: list[str]                  # validated ⊆ KIND_TOOLS[kind] ∩ project allow-list
    budget: Budget                    # max_steps, max_tokens, max_seconds, max_cost_usd
    output_schema: str                # name of registered Pydantic result model
```
Validator rejects: unknown tools, tools outside the kind allow-list, budgets above project caps, `role_ref` not in project, scope outside include globs. All specs are persisted (`agent_run.spec`).

### 4.3 Budgets and stop rules
Hard caps per agent: steps, tokens, seconds, cost. Explorer additionally stops after *N* consecutive steps with no new state fingerprint. Supervisor caps: max concurrent agents, max total spawns, max replans. Breaching a cap ends the agent with status `budget_exceeded` and a partial result.

### 4.4 Tool catalogue

| Group | Tools | Available to |
|---|---|---|
| Orchestration | `spawn_agent(spec)`, `await_agents(ids)`, `get_agent_result(id)`, `coverage_report()`, `replan(note)`, `ask_human(prompt)`, `finish(summary)` | supervisor only |
| Browser (implemented, M3) | `browser_snapshot()`, `browser_click(handle)`, `browser_fill(handle, value)`, `browser_navigate(route, fresh?)`, `browser_back()`, `probe_validation()`, `mark_skipped(reason)` | explorer. Not yet: `browser_select`, `browser_press`, `browser_wait` |
| Recording | **none: observation records automatically.** Every snapshot/click/navigate writes states, elements, forms, fields and transitions to the knowledge model through the recorder. Facts come from the browser, never from an agent's claim | n/a |
| Knowledge | `kb_search_states(query)`, `kb_get_state(id)`, `kb_path(from_id,to_id,role)`, `kb_role_matrix()`, `kb_get_elements(state_id)` | all |
| BRD | `req_search(query)`, `req_get(id)`, `req_list(filter)` | supervisor, mapper, test_author |
| Generation | `gen_field_tests(form_id)`, `gen_role_tests(state_id)`, `submit_test_cases(cases[])` | test_author |
| Critic | `flag_test_case(id, reason)` | critic |

**Element handles.** `browser_snapshot` returns elements as opaque handles (`e12`) valid for that state. Agents never write CSS/XPath; the gate resolves handles to stored locator candidates (role+name first, test-id, then CSS). This removes a whole class of invented selectors.

### 4.5 Tool Gate (`app/qa/gate`)
Every tool call passes through `ToolGate.invoke(agent_ctx, tool, args)`:

1. **Authorize** - tool ∈ agent's spec tools; args schema-valid.
2. **Budget** - decrement step/time budget; reject if exhausted.
3. **Safety classify** (browser actions) - resolve handle → element metadata; match denylist (text, aria-label, role, `type=submit` within destructive-named forms, `href` patterns like `/logout`) and project `allow_destructive` allow-list. Blocked → return `blocked: reason`, log `crawl_event(status=blocked)`.
4. **Request guard** - Playwright route handler installed per context in safe mode: abort non-`GET/HEAD/OPTIONS` requests unless URL matches `safe_post_patterns`; for GraphQL endpoints parse the operation and abort `mutation`. Violations log `blocked_request`.
5. **Execute** - run tool; capture screenshot where configured.
6. **Sanitize result** - strip values of password/hidden/credit-card/OTP inputs; truncate; secret scanner pass.
7. **Emit** - `tool_call` and `tool_result` events; update metrics.

The authentication phase runs **before** the guard is armed and outside any agent; it is the only code that reads secrets.

### 4.6 Fingerprinting
`fingerprint = sha256(route_template + normalized_a11y_structure + overlay_signature)`. Normalization drops text content of data rows, numbers, dates, and ids; keeps roles, names of controls, headings and labels. Claiming uses Redis `SET claim:{snapshot}:{fp} agent_id NX EX ttl`; the KM unique constraint is the durable backstop.

### 4.7 Exploration algorithm (implemented in `qa/crawler/bfs.py`)
Per role, one isolated browser context, logged in by code (`qa/browser/auth.py`), then a frontier of page URLs:
1. **Pages:** navigate (route-deduplicated, depth- and glob-limited) → observe → record. Links are queued by URL (never clicked), carrying their referrer so the transition is recorded.
2. **Click targets** (buttons, tabs, menu items, comboboxes): skipped with a logged reason when they are shell toggles (`shell_toggle`), toggles/checkboxes, disabled, **repeated** (same control in later table rows `repeated_row_action`, or ≥3 look-alike controls such as product cards `repeated_component`: one is explored), denylisted, form submits, or external links.
3. **Effects of a click** are classified from the resulting snapshot: `overlay_opened` (modal/menu/listbox), `navigated`, `state_changed` (tab/panel), `no_change`. Overlays are recorded as states, forms are probed, comboboxes' options are read, menus are explored item by item by **replaying the click path** from a fresh page load.
4. **Restoration** after every click returns to the page's base state (closes overlays, and does a *fresh* reload when the route or fingerprint differs; a same-document `#fragment` navigation does not reset anything).
5. **Per-role exploration:** a screen another role already recorded is explored again by this role (hidden drawers/menus differ), while a screen *this role* already explored is not.
6. **Stops:** `max_states`, per-agent step/time budget (partial result, see D21), no queue left.
Form handling: fields come from DOM attributes; `probe_validation` triggers the app's own client-side validation (empty / wrong format / too long, using format-valid long values) and reads messages via `aria-invalid` + `aria-describedby` and native `validationMessage`, never submitting. Dropdown values are enumerated; large sets are marked `truncated`.

### 4.7a Observation efficiency and page settling (from M0 findings)
- **Compact snapshots:** elements that repeat across states (sidebar, header) are returned once and referenced as "chrome"; after an action the tool returns only the *diff* plus the new heading/URL. Hard cap on elements per snapshot, with `truncated` flag.
- **History trimming:** older tool results in an agent's message history are replaced by one-line summaries. M0 showed 403 → 10,402 input tokens in 8 calls without this.
- **Settle detection:** wait for URL stable + main heading stable + DOM mutation quiescence (bounded timeout). `networkidle` alone left a stale heading after navigation.
- **Ignore list:** configurable selectors/text patterns for dev overlays (e.g. TanStack devtools buttons).

### 4.8 Deterministic baseline explorer
`BfsExplorer` drives the same tools with a fixed policy (no LLM). Used for (a) evaluation baseline, (b) fallback when `explorer.mode=deterministic`.

## 5. LLM gateway

- `get_chat_model(kind, overrides=, confidential=, scanner=, meter=)` → `ChatOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=…)` with a `SecretGuard` callback (aborts the call if an outgoing message contains a known secret) and a `UsageMeter`. `get_embeddings()` is the same for `EMBEDDING_*` (`check_embedding_ctx_length=False` is required for non-OpenAI endpoints). Implemented in `app/qa/llm/gateway.py`.
- **Privacy:** project flag `confidential`. When true the gateway refuses `:free` models (implemented). OpenRouter-specific provider routing (`provider: {data_collection: "deny", zdr: true}`) was verified in M0 to work only for non-free models; it is **not wired** because chat now goes to Groq, which has no such parameter. **Check Groq's data-retention/training terms for your account before using real confidential BRDs** - not verified here. Self-hosting remains the strict option (change `LLM_BASE_URL`).
- **Free-model constraints:** ~10 s/call and an unmeasured daily request cap → per-run LLM-call budget, retries with backoff on 429, deterministic explorer for bulk traversal.
- **Metering:** callback records input/output tokens and provider-reported cost into `agent_run` and emits `llm_usage`.
- **Redaction:** outgoing messages pass a scanner for known secret values (resolved per project at run start into an in-memory deny set); a hit aborts the call and raises a security event.
- **Retries:** exponential backoff on 429/5xx; per-run concurrency semaphore for LLM calls.
- **Model config:** `models: {supervisor, explorer, test_author, mapper, critic, extractor}`; defaults from env, overridable per project.
- **Structured output:** `with_structured_output`; one repair retry on validation failure.
- Embeddings go through OpenRouter (`/embeddings`), 1024-d, with the same `confidential` rule; batched, cached by content hash, never include secrets.

## 6. Live event pipeline

```
agent/tool/LLM callbacks → EventPublisher → ┬─ Redis Stream  run:{run_id}:events  (MAXLEN ~ 50k)
                                            └─ Postgres agent_event (batched insert)
API: GET /runs/{id}/stream?after=<stream-id>  (SSE; Last-Event-ID supported)
```
**Event envelope**
```json
{ "id": "…", "run_id": "…", "agent_id": "…", "parent_id": "…", "seq": 123,
  "ts": "2026-10-15T10:00:00Z", "type": "tool_call", "payload": { } }
```
**Types:** `run_status`, `agent_spawned`, `agent_status`, `agent_finished`, `llm_token`, `llm_message`, `llm_usage`, `tool_call`, `tool_result`, `screenshot`, `state_discovered`, `form_recorded`, `transition_recorded`, `skipped`, `blocked`, `needs_human`, `error`, `metric`.
- `llm_token` is Redis-only (not persisted); `llm_message` (final text) is persisted.
- Screenshots are stored as artifacts; events carry the artifact id/URL, not bytes.
- Reconnect: client sends last event id; server replays from Redis, falling back to Postgres for finished runs.

**Control**: `POST /runs/{id}/control {action: pause|resume|stop, agent_id?}` and `POST /runs/{id}/input {prompt_id, value}`. Control signals go over Redis pub/sub; the runtime checks a flag between steps. Input values (e.g. OTP) are delivered to the worker, never to the LLM.

## 7. API surface (`/api/v1`)

| Area | Endpoints |
|---|---|
| Projects | `POST/GET/PATCH/DELETE /projects`, `GET /projects/{id}` |
| Roles & secrets | `POST/GET/PATCH/DELETE /projects/{id}/roles` (credentials write-only; responses never include secrets) |
| BRD | `POST /projects/{id}/brd` (multipart), `GET /projects/{id}/brd`, `DELETE /brd/{id}` |
| Requirements | `GET /projects/{id}/requirements`, `PATCH /requirements/{id}`, `POST /requirements/{id}/approve`, merge/split |
| Runs | `POST /projects/{id}/runs` (kind, config overrides), `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/stream` (SSE), `GET /runs/{id}/events`, `POST /runs/{id}/control`, `POST /runs/{id}/input` |
| Agents | `GET /runs/{id}/agents`, `GET /agents/{id}` (spec, result, trace) |
| Knowledge | `GET /projects/{id}/snapshots`, `GET /snapshots/{id}/states`, `/states/{id}`, `/graph`, `/matrix`, `GET /snapshots/{id}/export.json`, `GET /runs/{id}/crawl-log` |
| Review / exports | `PATCH /tests/{id}` (edit), `GET /tests/{id}/versions`, `POST /tests/{id}/restore`, `POST /tests/{id}/regenerate`, `POST /projects/{id}/tests/bulk`, `GET /projects/{id}/rtm`, `GET /projects/{id}/exports/{tests\|rtm\|gaps\|km}?format=xlsx\|csv\|json`, `GET/PUT /projects/{id}/template`, `GET /projects/{id}/metrics` |
| Mapping | `POST /projects/{id}/mapping/run`, `GET /projects/{id}/mapping/summary`, `GET /projects/{id}/mappings`, `PATCH /mappings/{id}` (confirm/reject), `POST /projects/{id}/mappings` (link by hand), `GET /projects/{id}/gaps`, `PATCH /gaps/{id}` (accept/reopen), `GET /projects/{id}/gaps/export?format=csv\|json` |
| Tests | `GET /projects/{id}/test-cases` (filters), `PATCH /test-cases/{id}`, `POST /test-cases/{id}/approve|reject|regenerate`, bulk endpoints, `GET /test-cases/{id}/versions` |
| Outputs | `GET /projects/{id}/rtm`, `POST /projects/{id}/exports` (format, filters), `GET /exports/{id}/download` |
| Metrics | `GET /runs/{id}/metrics` |

Auth: existing JWT (`CurrentUser`). Projects are owned by a user; all queries are scoped by ownership (superuser sees all). The frontend client is regenerated with hey-api after API changes.

## 8. Configuration schemas (Pydantic, stored as JSONB)

```python
class CrawlConfig(BaseModel):
    max_depth: int = 4
    max_states: int = 300
    max_steps_per_agent: int = 150
    include_globs: list[str] = []; exclude_globs: list[str] = []
    include_modules: list[str] = []; exclude_modules: list[str] = []
    allow_destructive: bool = False
    destructive_allowlist: list[str] = []      # element text/regex explicitly permitted
    safe_post_patterns: list[str] = []         # POSTs allowed in safe mode (e.g. search)
    explorer_mode: Literal["agentic", "deterministic"] = "agentic"
    max_concurrent_agents: int = 3
    screenshots: Literal["all", "new_states", "off"] = "new_states"
    seeds: list[str] = []                      # extra routes; sitemap used if present
    stale_after_days: int = 14

class GenerationConfig(BaseModel):
    test_types: list[TestType] = [...all...]
    max_cases_per_requirement: int = 12
    map_confidence_threshold: float = 0.6
    dedup_cosine: float = 0.92
    include_flagged_requirements: bool = False
    models: dict[str, str] = {}
    template: TestCaseTemplate                 # columns, order, id format, priority scale

class BudgetCaps(BaseModel):                   # project-level hard caps
    max_cost_usd_per_run: float; max_tokens_per_agent: int; max_runtime_minutes: int
```
A resolved copy is written to `run.config_snapshot`.

## 9. Test-case step schema

```python
class Step(BaseModel):
    action: Literal["navigate","click","fill","select","check","expect_visible","expect_text","expect_error","expect_disabled", ...]
    element_ref: str | None          # KM element id
    state_ref: str | None            # KM state id (for navigate/expect)
    value: ValueSpec | None          # literal | generated(kind, params) | placeholder("<ADMIN_PASSWORD>")
    note: str | None
```
Renderer builds text: navigation path from `kb_path` + label of `element_ref` + element type noun ("button", "field"). Validator rejects unknown refs or refs unreachable for the test's role.

## 10. Deduplication and trivial filtering
- Signature = hash of (requirement, role, normalized ordered (action, element_ref, value-class) list).
- Semantic check = cosine similarity of title+steps embedding ≥ `dedup_cosine` against kept cases for the same requirement → keep higher priority/quality.
- Trivial: single `navigate` + `expect_visible` with no requirement-specific assertion; duplicates of rule-generated cases.

## 11. Security requirements
- Credentials: Fernet-encrypted (`CREDENTIAL_ENCRYPTION_KEY`, with `key_version` for rotation); API never returns them; decrypted only in `browser/auth`.
- No secrets in logs, events, traces, prompts, exports. Pre-flight deny-set scanner on LLM payloads; CI test uses canary secrets.
- `.env` untracked; add new variables only to `.env.example` with empty values.
- Safe mode default on; disabling requires explicit project setting plus confirmation in UI.
- SSRF/abuse: target URL validated against `ALLOWED_TARGET_HOSTS` and private-address rules; worker egress should be limited to the app host and LLM endpoint in deployment docs.
- Uploaded files: size/type limits, stored outside web root, never executed.

## 12. Testing strategy

| Level | Approach |
|---|---|
| Unit | rules engine (BVA etc.), renderer, validator, dedup, fingerprinting, spec validator, gate classifier, crypto |
| Integration | API + DB (existing pytest fixtures), AGE projection, pgvector queries, Redis events → SSE |
| Agent | scripted fake chat model for deterministic graph tests; tool-gate tests against a local fixture site |
| Crawler | local fixture app (the repo's frontend + backend via compose) as a target; assertions on states/forms found, no mutations occurred (DB row counts unchanged) |
| Security | canary-secret test on LLM payloads; safe-mode test that a Delete click is blocked |
| LLM-in-the-loop | gated eval suite (not in CI by default) producing the evaluation metrics |
| Frontend | Playwright E2E for review/RTM/live view using a replayed event stream |

## 13. Observability
Structured JSON logs with `run_id/agent_id`; Sentry already configured; `run_stage` and `agent_run` carry timing, tokens, cost; events persisted for audit.

## 14. Decisions log

| ID | Decision | Alternatives | Reason |
|---|---|---|---|
| D1 | Supervisor + sub-agents on LangGraph | Fixed pipeline; hand-rolled loop | Open-ended SPA exploration; fan-out; checkpointing |
| D2 | Steps as structured refs + deterministic renderer | Free-text steps | Eliminates label hallucination |
| D3 | Rule-based generators for mechanical tests | LLM for everything | Cost, determinism, accuracy |
| D4 | Mutation blocking at network layer | Prompt/keyword only | Hard safety guarantee |
| D5 | Relational source of truth + AGE projection | AGE only | Simpler queries/migrations; graph for traversal |
| D6 | Redis Streams + SSE for live view | WebSockets; DB polling | Ordered replayable events, simple transport |
| D7 | arq for jobs | Celery; custom queue | Async-native |
| D8 | OpenAI-compatible endpoints for chat (Groq) and embeddings (OpenRouter), configured separately | Direct provider SDKs; one shared provider | Speed/quality of Groq gpt-oss; Groq has no embeddings; self-host swap by URL |
| D9 | OpenRouter embeddings, 1024-d (revised after M0; was local 384-d) | Local sentence-transformers; 2048-d models | No heavy local ML dependency; HNSW limit is 2000 dims; trade-off: BRD text leaves the network for retrieval, hence the `confidential` flag |
| D12 | `confidential` project flag refuses free models (strict OpenRouter routing was verified but is unwired with Groq) | Always strict | Free models can't satisfy no-retention routing (verified) |
| D13 | Tests run on an isolated `<db>_test` database (`tests/test_database.py`) | Shared dev DB | The template's fixtures delete all users/items at session end; running them on the dev DB wiped it (happened once during M1) |
| D14 | LangGraph checkpoint tables are created once at worker startup / test provisioning (`ensure_checkpoint_tables`), never per run | `setup()` per run | `setup()` runs `CREATE INDEX CONCURRENTLY`, which waits for every open transaction (deadlocked a test session) |
| D15 | Persist all buffered events *before* emitting the terminal `run_status`; flushes are serialized and shutdown is graceful | Fire-and-forget persistence | Clients refetch on the terminal event; concurrent/cancelled flushes lost or reordered events (found by live testing, regression-tested) |
| D16 | SSE endpoint authenticates with a short-lived DB session, not the request-scoped `SessionDep` | `CurrentUser` dependency | A request-scoped session would pin a pooled connection for the whole stream. Browsers' `EventSource` cannot send an Authorization header → the frontend must use a fetch-based SSE client |
| D17 | Screen identity (fingerprint) = route + overlay + sub-headings + main-region controls; excludes the page h1 (user data), data rows, app-shell chrome and shell toggles; overlays opened from the shell drop the route | URL-based identity | SPAs reuse URLs; user-specific headings split one screen per user (found on the template dashboard) |
| D18 | Region comes from the nearest `<main>` first, then nav/header/footer | Match sidebar-ish attributes first | shadcn wraps page content in `data-slot=sidebar-inset`, which classified *every* control as navigation |
| D19 | App-shell toggles (sidebar, theme) are never clicked and never part of identity; patterns configurable (`shell_toggle_patterns`) | Click everything | The app persists the choice, so one click made every later screen look new |
| D20 | Hash routes (`/#/search`) are routes; restoration uses a fresh load | URL path only | Angular apps; an open drawer's backdrop survived a "navigation" to the same document and blocked every later click |
| D21 | A budget stop is a **partial** result (`run.summary.partial`), not a failure | Mark failed | The model found so far is valid and saved; the UI says "partial" |
| D22 | Network guard: allow known realtime transports (socket.io, SignalR, SockJS) and explicit patterns; abort non-read requests to other origins; log one entry per (method, path) | Block every non-read request and log each | 486 socket.io polls drowned the crawl log and starved the app's live channel |
| D23 | Denylist matches action verbs ("Pay now", "Delete"), not nouns ("Orders and Payment menu") | Match any occurrence | Over-blocking hid whole sections; the network guard is the real protection |
| D24 | Login: dismiss cookie/welcome overlays first; username = text input in the password field's own form; submit = that form's submit button | First input / first matching button | Search box in the toolbar was filled instead of the email; "Log in with Google" matched the button name |
| D25 | A local fixture SPA (`tests/qa/fixture_app.py`) with a login (decoys, cookie overlay), SPA routing, modal form, tabs, row menu, Delete/Log out and request counters is the deterministic crawler test target | Only third-party demo apps | Lets CI prove "nothing was mutated" by counting requests that reached the server |
| D26 | Every extracted requirement must carry a quote that is verified against the section text; the stored quote is the document's own text | Trust the model's quote | Makes invented or paraphrased requirements impossible to persist; reviewers can verify each one against its source |
| D27 | Ambiguity = deterministic wording checks + model judgement; a flagged requirement cannot be approved until a human rewrites it or accepts it | Model-only flags; warn only | Vague wording must be a human decision, not a guess; model flags vary run to run, the rules do not |
| D28 | BRD-native ids are kept; others become `REQ-NNN` in document order, never colliding with existing keys | Always renumber | Traceability back to the BRD's own numbering |
| D29 | Parser sanity: reject degenerate one-row "tables"; word-coverage check warns when parsing drops text | Trust the PDF library | A boxed paragraph was parsed as a table and its requirement vanished silently |
| D30 | Ingest is a normal run (`ingest`), streamed live like a crawl; the API holds no LLM keys | Synchronous upload handler; keys in the API | Long, visible, resumable work; least-privilege secrets |
| D31 | Editing a requirement clears its embedding with `flag_modified` | Assign `None` | A loaded pgvector value is a NumPy array; SQLAlchemy does not detect `array -> None`, so the stale vector stayed |
| D32 | A screen is described to the judge by a **card** built only from recorded facts (headings, controls, form fields, validation messages, nav path); evidence the judge cites must exist on the card | Free-text screen summaries; screenshots | Makes every mapping checkable and keeps the judge from inventing UI; costs recall when a screen's meaning lives in page *data* the crawler does not store |
| D33 | Data-dependent variants of one route are **one candidate** (group key = kind + route template + heading); the richest variant represents the group | One candidate per recorded state | An empty-vs-populated Items page is one feature; otherwise duplicates crowd out real candidates and inflate "undocumented screen" gaps |
| D34 | Retrieval = lexical IDF coverage + embedding cosine, fused by reciprocal rank; cosine is computed in Python over the snapshot's few hundred cards (embeddings cached on `ui_state`); works with embeddings down (keyword only) | pgvector ANN query; embeddings required | A snapshot has tens to hundreds of screens: exact search is simpler and snapshot-scoped; an embedding outage must not block mapping |
| D35 | Judge output is candidate-constrained (aliases S1..Sn), evidence is verified against the card (no verifiable evidence ⇒ confidence capped at 0.4), at most 4 screens within 0.2 of the best are kept | Trust the model's confidence | Same grounding principle as BRD extraction (D-list): unverifiable claims cannot become confident mappings; a requirement is not spread across every screen that shares a toolbar |
| D36 | The model's "not visible in the UI" verdict is trusted only for NFR/business-rule requirements; a functional/validation requirement is always a gap ("no screen shows it"), never silently dismissed | Trust the flag | Measured: the flag dismissed "view product catalogue" as invisible on some runs |
| D37 | An **empty** judge answer for a behavioural requirement gets one "second look" call | Single call; always two calls | A false gap costs reviewer time and hides a feature; measured recall swung 0.69–0.92 between identical runs before this, and 0.92–1.00 after, at the price of one stable false positive (a loosely related screen at 55–68%) |
| D38 | Gaps are **derived** from the effective mappings (confirmed, or proposed ≥ threshold; rejected never count) and recomputed after every human decision without an LLM call; accepted gaps stay accepted while still true | Gaps written only by the run | One source of truth: confirming, rejecting or hand-linking immediately changes both gap lists |
| D39 | A gap on a **partial crawl** is shown with a warning: it may mean the screen was not reached | Report gaps as facts | The Juice Shop crawl ended at its step budget and never reached registration/login/administration; those requirements show as gaps |
| D40 | Test cases are **structured steps rendered to text** (`Step{action, target{label,kind}, value, screen, expect}`); text is produced by a deterministic renderer from real labels | Free-text steps from the model | Steps stay checkable against the knowledge model, re-renderable when labels change, and cannot name a control that was never recorded |
| D41 | Mechanical cases (required, format, length/numeric boundaries, option lists, positive form flow, role access) come from **rules over recorded constraints**; the author model only writes business-rule and end-to-end scenarios | Ask the model for everything | Reproducible, free, no hallucinated limits; the model is spent where judgement is needed |
| D42 | Every case records how its expected result is known: `observed` (the app's own message), `inferred` (recorded constraint, no message seen: the crawler never submits), `requirement`, or `not_observed` (role-denial never probed); the UI shows it | One undifferentiated expected result | The crawler cannot see what happens after submit, so honesty about where an expectation comes from is part of the output |
| D43 | Author cases are **grounded in code**: aliases S1..Sn, every target label must be on that screen's card, destructive controls and invented labels drop the case; one feedback regeneration; a case must interact or assert something | Trust the model | Same principle as extraction (verbatim quote) and mapping (verified evidence); measured drops are reported in the run summary |
| D44 | A form's path is the **chain of controls** that opens it (page -> row menu -> dialog), resolved from overlay/tab transitions; per-row controls add a "a row must exist" precondition | Use the dialog's own nav path | Measured: replay failed 3 cases because a menu item was named as a screen |
| D45 | "Role X cannot open S" is generated only for roles whose crawl **finished**, and access is evaluated per route group (a role-specific variant of the page counts as access) | Treat missing access rows as denial | Measured: the replay found the claim false for pages that differ by role and for budget-stopped crawls |
| D46 | Exact duplicates (same steps and expectation) collapse across requirements and the survivor records `also_covers`; near-duplicates (cosine >= 0.92, same requirement and type) are dropped when embeddings are available | Keep one case per requirement | Shared forms are mapped to several requirements; traceability must not lose the second one, and the review table must not triple |
| D47 | Re-generation replaces unreviewed drafts and keeps approved/rejected/edited cases; keys (`TC-0001`) are never reused | Delete and recreate | A reviewer's work and any reference to a TC key must survive a re-run |
| D48 | A person's edit to a case is a **new version** (`test_case_version`), sets status `edited`, and never rewrites history; restore creates another version. Editing the step text marks the case `hand_edited` (structured steps are kept as generated) | Overwrite in place; re-parse edited text into structured steps | Review must be auditable and reversible; parsing free text back into steps would invent structure |
| D49 | Editing a requirement queues its **unreviewed** cases (`needs_regen`) and only **marks** approved/edited ones (`requirement_changed`) | Reset every case | Nothing a reviewer decided is silently discarded; regeneration replaces only drafts and queued cases |
| D50 | Regeneration is requirement-level (also reachable from a case) with a free-text reviewer comment passed to the author prompt; approved, edited and rejected cases are kept and keys are not reused | Per-case regeneration | The author writes a requirement's scenarios together; replacing one in isolation would duplicate or contradict its neighbours |
| D51 | The RTM counts a case once under its primary requirement and shows identical cases it also covers as `tests_linked` (so `sum(tests_own)` equals the non-rejected case count); a requirement is covered if own + linked > 0, and every uncovered row carries a reason | One row per (case, requirement) pair | Reconciles exactly with the test table, yet no requirement loses traceability to a shared case |
| D52 | Exports are generated on request (no stored artefact); test-case columns, order, headers and the ID prefix come from a per-project **template**; every cell is neutralised against spreadsheet formulas (`=`, `+`, `-`, `@` prefixed with `'`) | Fixed columns; trusting BRD/app text | BRD- and app-derived text goes into Excel/CSV, where a leading `=` would execute |
| D53 | Login challenges are handled in the browser layer, not by agents: TOTP is computed in-process from a stored seed (RFC 6238, stdlib), a human-entered code travels person -> API -> Redis (read once, never in an event) -> `login()`, a CAPTCHA fails the role with a message that names the way out, and a pasted Playwright **storage state** replaces the password | An LLM-driven login; a CAPTCHA solver | Credentials and codes must never reach a model; solving CAPTCHAs is outside what a QA tool should do |
| D54 | A session that expires mid-crawl is detected (the requested page turned out to be the login page, or a snapshot lands on it) and the role signs in again with the guard lifted only for the login, then re-opens the page it wanted; capped at 6 re-logins | Failing the role; keeping the guard lifted | An expiring session is normal for real apps; a loop of re-logins is not |
| D55 | After a multi-role crawl each role opens, by address and read-only, the screens only other roles reached; the outcome (visible / forbidden / not found / redirected, with evidence) is stored per role and screen, and a "role cannot open X" case is `observed` only when that probe happened | Inferring denial from a missing menu entry | A missing link is not a denial; the app may still serve the route |
| D56 | Static content is captured as **masked** table column headers and short fixed captions (no table body cells; text with digits dropped unless it is a "Label:"; e-mails and long tokens masked; `data-private` subtrees skipped) and can be switched off per project (`capture_static_text`) | Capturing all visible text; capturing nothing | "The basket shows items, quantities and totals" can only be mapped if the screen's column headers and captions are known; data and identities are what must not be stored |
| D57 | The agentic explorer is a planner (one structured-output call, routes validated in code against the app origin, include/exclude globs and the denylist) plus one explorer agent per module over the same gated tools, a coverage report computed from what was recorded, and one replan for missed navigation targets; a failed plan falls back to one module per navigation link | Free-roaming single agent; trusting the planner's routes | Measured against the deterministic explorer on the template app: 28 vs 37 screens at ~866k tokens vs 0, so the deterministic explorer stays the default |
| D58 | Navigation by address is subject to the same denylist as clicking (a `/logout` link is never opened), download/feed addresses (`.csv`, `.atom`, `.pdf`…) are not followed, and a `beforeunload` prompt is accepted (the probe never saves, and refusing it made every later navigation fail) | Only checking click labels | Found on Redmine: a sign-out link opened by address, then every navigation failed |
| D59 | The knowledge model is also projected into Apache AGE (`qa_km` graph: Screen, Role, GOES_TO, ACCESS), rebuilt per snapshot; Postgres stays the source of truth and path planning for generation still uses the relational planner | Moving the model into the graph | A graph earns its place for path and reachability questions; the relational tables are what everything else already depends on |
| D60 | Scanned PDFs are read with RapidOCR (CPU, ONNX, bundled models): each page without a text layer is recognised at 300 dpi, low-confidence lines (< 0.7) are dropped and counted, and the text is laid back as invisible text so the ordinary PDF parser (headings by size, lists, tables) is reused; the parse carries an OCR warning | Docling; a hosted OCR API | No document leaves the machine; one parser path for all PDFs |
| D61 | Confluence import fetches one page (Cloud: e-mail + API token; Data Center: bearer token) through the SSRF guard, converts the export HTML to Markdown and feeds the ordinary ingest; the token is used for the request only | Storing the token; crawling a space | A BRD is one document; a stored token would be a new secret to protect |
| D62 | Embeddings come from Azure OpenAI (`text-embedding-ada-002`, 1536-d) when configured, else an OpenAI-compatible endpoint; stored vectors carry the model id (`azure:<deployment>`) and are recomputed when it differs; the vector columns are 1536-d (migration clears the old 1024-d vectors) | Truncating ada-002 to 1024-d; keeping the rate-limited free tier | The free tier hit its daily cap during mapping and fell back to keywords; ada-002 has no `dimensions` option, and truncating a non-Matryoshka vector degrades it |
| D63 | The agentic crawl is a **supervised** team: scouts (one per role, through the tool gate) read the navigation, open each section and menu once and measure every page (controls, forms, tabs, rows); the supervisor model groups the pages into packages and says which role takes each (a page every role has is explored once, a role-only page by that role); code validates the plan (routes must have been seen by that role, no page twice, nothing left out) and sizes the team from the *measured* complexity (1-3 explorer agents per package, step budget growing with size); one replan from a coverage report. Agent count follows the application, not the number of accounts | One agent per role; trusting the model's complexity estimate | Roles share most pages; exploring them once per role is waste, and a model's guess at complexity is worth less than a measurement |
| D64 | Integrations are **bring-your-own-key, per user and platform** (one saved connection each for Jira Cloud, TestRail and Zephyr Scale Cloud, shared by all the user's projects; Integrations page in the sidebar). The token is encrypted (same Fernet key as role credentials), write-only, never returned, never sent to a model, scrubbed from error text; the URL goes through the SSRF guard and must be https unless the host is private (allowed or a bare service name). A push sends approved cases (drafts optional), previews first, creates new cases and **updates** ones pushed before (external id + content hash per case and destination, so nothing is duplicated), tolerates per-case failures and rate limits | OAuth apps; one set of credentials per project; pushing from the worker | BYOK needs no registered app or callback; per-user keys keep each person's own permissions; only the three tools the team asked for are built |
| D65 | A project may carry two **reference documents**: a business glossary (CSV, JSON, Markdown table or "Term: definition") and an API specification (OpenAPI 3 / Swagger 2, JSON or YAML). The glossary's terms that occur in a text are added to the extraction, mapping and authoring prompts and their synonyms to retrieval; the API's input rules (max length, allowed values, required, min/max) are added to the recorded form fields **in memory** when a field name matches, so the generated cases say where the rule came from (`from_api_spec`), and related operations are shown to the author as the backend contract (only for expected outcomes, never for steps) | Treating them as more BRD text; changing the recorded model | They are reference, not requirements; the recorded application stays exactly what the browser saw |
| D66 | Multi-step forms are walked only when `walk_wizards` is on: valid sample values go into each step (the field's minimum, the first real option) and only a button named Next/Continue/Proceed/Review (and not Create/Save/Submit/Pay/Confirm/Delete...) is pressed, with the network guard letting that step's own same-origin request through; what was typed is stored on the transition so test paths replay it, and later steps are never reached by their address | Pressing every button; opening step addresses | A wizard is the main way features hide; but a step of some applications saves a draft, so it is opt-in |
| D67 | Images inside a BRD (flow charts, mock-ups) are read with the same OCR as scans: only their text labels, in reading order, under a "Diagrams" heading, with a parse warning that arrows and layout are not interpreted | A vision model; ignoring images | No document leaves the machine and no vision model is required; relationships between boxes are an honest gap |
| D68 | Run cost is an **estimate**: provider-reported tokens x configurable list prices (`LLM_PRICE_INPUT_PER_M` / `LLM_PRICE_OUTPUT_PER_M`), shown per run and kind; embeddings are not priced | Reading prices from the provider | Prices change and differ per contract; the tokens are exact, the dollars are labelled as an estimate |
| D69 | Business relevance has two measures: a human rating sheet (the real one, still to be filled in) and `evaluate.py --judge`, an AI model scoring the same stratified sample with a QA-lead rubric, reported as a **proxy** with its lowest-scored cases. An access-rights requirement ("Clerk limited access", mapped to every screen of the role) yields role-access cases only, not field tests of every screen | Reporting the proxy as the rating | The first proxy run (Stockroom, 30 cases, mean 3.2) showed field cases hung on access requirements as noise; that rule came from it |
| D10 | Opaque element handles for agents | Agent-written selectors | Prevents invented selectors |
| D11 | Credentials resolved only in worker auth module | Pass into agent tools | Guarantees LLM never sees them |

## 15. Open technical questions
1. ~~Python 3.14 compatibility~~ - resolved in M0 (works).
2. ~~OpenRouter parameters~~ - mostly resolved (privacy params work but free models fail them; usage/cost fields present; embeddings available). Remaining: free-tier daily cap and per-model tool-calling comparison.
3. AGE: works from psql. Remaining: per-connection `LOAD 'age'` / `search_path` under a pooled psycopg connection from Python.
4. Which BRD formats beyond PDF/DOCX (Confluence export format?) and whether OCR is needed.
5. ~~Demo target application~~ - resolved: open-source apps via compose profiles (§2.1). Remaining: confirm the OrangeHRM (or Redmine) image and its resource needs. MFA is not present in these apps, so TOTP/OTP handling is verified with unit tests and a mocked login page rather than a live app.
6. ~~Fixed vector dimension~~ - resolved: 1024 (see D9).
