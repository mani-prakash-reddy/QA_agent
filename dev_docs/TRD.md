# Technical Requirements Document (TRD)

| | |
|---|---|
| Status | Draft v0.1 |
| Related | `PRD.md`, `docs/architecture.md`, `backend-schema.md`, `implementation-plan.md` |

This document says **how** the system is built. Design rationale lives in `docs/architecture.md`; table definitions live in `backend-schema.md`.

## 1. Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python ≥3.14 (repo constraint), TypeScript | **Verify** LangGraph/LangChain/Playwright/Docling wheels on 3.14 in the M0 spike; lower `requires-python` if needed. |
| API | FastAPI, SQLModel, Alembic, Pydantic v2 | Existing. Sync `Session` for CRUD routes (existing pattern). |
| Agents | `langchain`, `langgraph`, `langchain-openai`, `langgraph-checkpoint-postgres` | Pin versions in the spike; confirm current prebuilt-agent API names. |
| Browser | `playwright` (async) | Chromium only in v1. |
| Queue | Redis + `arq` | Run jobs, plus Redis Streams for live events and `SET NX` for state claims. |
| DB | PostgreSQL 18, AGE 1.8, pgvector | Existing `db/` image. |
| Embeddings | `sentence-transformers` (default `BAAI/bge-small-en-v1.5`, 384-d) | Dimension fixed in schema; changing models requires a migration + re-embed. |
| BRD parsing | `docling` primary; `pymupdf` + `python-docx` fallback | |
| Exports | `openpyxl`, `csv` | |
| Frontend | React 19, TanStack Router/Query/Table, Tailwind 4, shadcn/ui, hey-api generated client | Existing. |
| LLM | OpenRouter via OpenAI-compatible API | `OPENAI_API_BASE_URL`, `OPENAI_API_KEY` already in `.env`. |

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

- `.env.example` gains: `REDIS_URL`, `OPENAI_API_BASE_URL`, `OPENAI_API_KEY` (blank), `CREDENTIAL_ENCRYPTION_KEY`, `ARTIFACT_DIR`, per-kind `MODEL_*` defaults, `EMBEDDING_MODEL`.

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

Rules: no module under `agents/` imports Playwright or a model client directly — everything goes through `gate/`.

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
| Browser | `browser_snapshot()`, `browser_click(handle)`, `browser_fill(handle, value)`, `browser_select(handle, option)`, `browser_press(key)`, `browser_navigate(route)`, `browser_back()`, `browser_wait(condition)`, `probe_validation(form_handle)` | explorer (read-only subset `snapshot` for supervisor optional) |
| Recording | `record_state`, `record_form`, `record_transition`, `mark_skipped(reason)` | explorer |
| Knowledge | `kb_search_states(query)`, `kb_get_state(id)`, `kb_path(from_id,to_id,role)`, `kb_role_matrix()`, `kb_get_elements(state_id)` | all |
| BRD | `req_search(query)`, `req_get(id)`, `req_list(filter)` | supervisor, mapper, test_author |
| Generation | `gen_field_tests(form_id)`, `gen_role_tests(state_id)`, `submit_test_cases(cases[])` | test_author |
| Critic | `flag_test_case(id, reason)` | critic |

**Element handles.** `browser_snapshot` returns elements as opaque handles (`e12`) valid for that state. Agents never write CSS/XPath; the gate resolves handles to stored locator candidates (role+name first, test-id, then CSS). This removes a whole class of invented selectors.

### 4.5 Tool Gate (`app/qa/gate`)
Every tool call passes through `ToolGate.invoke(agent_ctx, tool, args)`:

1. **Authorize** — tool ∈ agent's spec tools; args schema-valid.
2. **Budget** — decrement step/time budget; reject if exhausted.
3. **Safety classify** (browser actions) — resolve handle → element metadata; match denylist (text, aria-label, role, `type=submit` within destructive-named forms, `href` patterns like `/logout`) and project `allow_destructive` allow-list. Blocked → return `blocked: reason`, log `crawl_event(status=blocked)`.
4. **Request guard** — Playwright route handler installed per context in safe mode: abort non-`GET/HEAD/OPTIONS` requests unless URL matches `safe_post_patterns`; for GraphQL endpoints parse the operation and abort `mutation`. Violations log `blocked_request`.
5. **Execute** — run tool; capture screenshot where configured.
6. **Sanitize result** — strip values of password/hidden/credit-card/OTP inputs; truncate; secret scanner pass.
7. **Emit** — `tool_call` and `tool_result` events; update metrics.

The authentication phase runs **before** the guard is armed and outside any agent; it is the only code that reads secrets.

### 4.6 Fingerprinting
`fingerprint = sha256(route_template + normalized_a11y_structure + overlay_signature)`. Normalization drops text content of data rows, numbers, dates, and ids; keeps roles, names of controls, headings and labels. Claiming uses Redis `SET claim:{snapshot}:{fp} agent_id NX EX ttl`; the KM unique constraint is the durable backstop.

### 4.7 Exploration algorithm (explorer)
Seed from nav chrome → for each unvisited interactable in current state: classify → act → wait for settled (network idle + DOM quiescence, bounded) → fingerprint → record state/transition → if new overlay/step, recurse (depth-capped) → restore via `browser_back`/close overlay/re-navigate. Form handling: record fields from DOM attributes; `probe_validation` triggers blur/invalid-input events to capture messages without submit. Dropdown values enumerated; large option sets sampled and marked `truncated`.

### 4.8 Deterministic baseline explorer
`BfsExplorer` drives the same tools with a fixed policy (no LLM). Used for (a) evaluation baseline, (b) fallback when `explorer.mode=deterministic`.

## 5. LLM gateway

- `get_chat_model(kind, project_cfg)` → `ChatOpenAI(base_url=OPENAI_API_BASE_URL, api_key=..., model=cfg.models[kind], extra_body={"provider": {...}})`.
- **Privacy routing:** default `extra_body` requests no-retention/no-training providers. **Confirm exact OpenRouter parameter names in the spike.**
- **Metering:** callback records input/output tokens and provider-reported cost into `agent_run` and emits `llm_usage`.
- **Redaction:** outgoing messages pass a scanner for known secret values (resolved per project at run start into an in-memory deny set); a hit aborts the call and raises a security event.
- **Retries:** exponential backoff on 429/5xx; per-run concurrency semaphore for LLM calls.
- **Model config:** `models: {supervisor, explorer, test_author, mapper, critic, extractor}`; defaults from env, overridable per project.
- **Structured output:** `with_structured_output`; one repair retry on validation failure.
- Embeddings are local and never leave the worker.

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
| Mapping | `GET /projects/{id}/mappings`, `GET /projects/{id}/gaps`, `PATCH /mappings/{id}` |
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
| D8 | OpenRouter via OpenAI-compatible API | Direct provider SDKs | Model flexibility; self-host swap by URL |
| D9 | Local embeddings (384-d) | Hosted embeddings | Privacy, no extra provider dependency |
| D10 | Opaque element handles for agents | Agent-written selectors | Prevents invented selectors |
| D11 | Credentials resolved only in worker auth module | Pass into agent tools | Guarantees LLM never sees them |

## 15. Open technical questions
1. Python 3.14 compatibility of the agent/browser/parsing libraries (spike).
2. OpenRouter: exact privacy-routing parameter names; embeddings availability; per-model tool-calling reliability; usage/cost fields.
3. AGE with psycopg pooling: per-connection `LOAD 'age'` and `search_path` handling.
4. Which BRD formats beyond PDF/DOCX (Confluence export format?) and whether OCR is needed.
5. ~~Demo target application~~ — resolved: open-source apps via compose profiles (§2.1). Remaining: confirm the OrangeHRM (or Redmine) image and its resource needs. MFA is not present in these apps, so TOTP/OTP handling is verified with unit tests and a mocked login page rather than a live app.
6. Fixed `vector` dimension (384 assumed) vs a larger embedding model.
