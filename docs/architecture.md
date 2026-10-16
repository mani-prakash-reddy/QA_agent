# QA Agent - System Architecture

> Product document (deliverable 1 of the assignment). Describes components, data flow, technology choices with reasons, and the prompt strategy.
> Implementation-level detail (APIs, schemas, tool specs) lives in `dev_docs/TRD.md` and `dev_docs/backend-schema.md`.

## 0. What is built (read this first)

This document describes the target design. The table says what the delivered system actually does today; measured behaviour is in [evaluation.md](evaluation.md).

| Component | State | Notes |
|---|---|---|
| BRD ingest (PDF, scanned PDF, DOCX, Markdown, text, Confluence page) | **Built** | Quote-verified extraction, ambiguity flags, review UI. Scans are read with CPU OCR (RapidOCR) and flagged; Confluence import is tested against a mocked API only. |
| Agent runtime (specs, budgets, tool gate, live events, pause/stop) | **Built** | Exercised by the ingest, crawl, mapping and generation runs. |
| Supervisor that plans and spawns explorer sub-agents | **Built (optional mode)** | `explorer_mode: agentic`: scouts measure each role's pages, the supervisor splits the work (shared pages once, role-only pages by that role), the team is sized by measured complexity, explorers walk their pages, one coverage replan. Shown live as a tree of agent cards. |
| Explorer | **Deterministic (default) and agentic** | Both go through the same tool gate. Measured on the template app: agentic found fewer screens (28 vs 37) at about 866k tokens, so deterministic is the default; see evaluation.md §5. |
| Login handling | **Built** | Form login, TOTP from a seed, a one-time code typed by a person on the run page, stored browser session (the way past CAPTCHA/SSO), re-login when a session expires mid-crawl. A CAPTCHA fails the role with a clear message; it is never solved. |
| Safe mode (network guard, destructive-control classifier, staging acknowledgement) | **Built** | Forms are never submitted; writes are blocked even if a click slips through. |
| Knowledge model | **Built (relational + AGE graph)** | States, elements, forms/fields, transitions, role access (including observed denials from forbidden-route probing), masked table headers/captions, screenshots; JSON/Excel export; Apache AGE projection with a shortest-path API. Observed-API capture is not built. |
| Mapping and two-way gap report | **Built** | Hybrid retrieval, evidence-verified judge, human review. |
| Test generation | **Built** | Rules plus an author agent, grounded and de-duplicated. No separate critic agent; the validator is deterministic. |
| Review, versions, RTM, exports, template, metrics | **Built** | Excel/CSV/JSON. |
| Jira / TestRail / Zephyr | **Built, verified against mocked APIs only** | Bring-your-own-key per user (Integrations page): Jira Cloud (issues), TestRail (cases with steps), Zephyr Scale Cloud (cases with steps). Preview, push approved cases, update instead of duplicate. Not yet exercised against a real account. |

Sections below that mention a critic agent or observed API endpoints describe the design, not the current behaviour.

## 1. Purpose

QA Agent takes a Business Requirements Document (BRD) and access to a live web application, explores the application on its own for each user role, and generates **business-specific, traceable test cases** that use the application's real screen names, field labels and navigation paths.

Three properties drive every design decision:

1. **Grounded** - a test step can only reference a screen, field or button that was actually observed in the app.
2. **Traceable** - every test case links to a requirement; every requirement links to the screens that implement it; gaps are reported in both directions.
3. **Safe and private** - the crawler cannot perform destructive actions by default, and credentials never reach an LLM.

## 2. System context

```
   QA lead / QA engineer / BA
              │  browser
              ▼
   ┌─────────────────────────┐        ┌──────────────────────────┐
   │ Web UI (React)          │◄──SSE──┤ API (FastAPI)            │
   │ projects · BRD · live   │──REST─►│ auth · CRUD · run control│
   │ run · review · RTM      │        └───┬──────────┬───────────┘
   └─────────────────────────┘            │ enqueue  │ read/write
                                          ▼          ▼
                                   ┌──────────┐  ┌───────────────────────────┐
                                   │  Redis   │  │ PostgreSQL 18             │
                                   │ queue +  │  │ relational + JSONB        │
                                   │ event    │  │ Apache AGE (graph)        │
                                   │ streams  │  │ pgvector (embeddings)     │
                                   └────┬─────┘  │ LangGraph checkpoints     │
                                        │        └───────────────────────────┘
                                        ▼                    ▲
                              ┌───────────────────┐          │
                              │ Worker (arq)      │──────────┘
                              │ LangGraph agents  │
                              │ Playwright        │── HTTPS ──► Application under test (staging)
                              │ Tool gate         │── HTTPS ──► LLM (Groq / OpenRouter)
                              └───────────────────┘
```

## 3. Components

| Component | Responsibility |
|---|---|
| **BRD Ingest** | Parse PDF/DOCX (and Confluence export), preserve structure, chunk by section, extract requirements with source anchors, assign IDs, flag ambiguity. |
| **Agent Runtime** | A LangGraph supervisor + dynamically spawned sub-agents. Executes crawling, mapping and test authoring under budgets. |
| **Tool Gate** | The only path from an agent to the browser, the knowledge model and the LLM. Enforces safe mode, denylists, budgets, credential isolation, logging. Implemented in code, never in prompts. |
| **Crawler tools** | Playwright-backed tools giving agents controlled access to a logged-in browser context per role. |
| **Knowledge Model (KM)** | Versioned snapshot of the app: states/pages, elements, forms/fields, transitions, role-permission matrix, observed API calls. Stored relationally, projected into an AGE graph, embedded in pgvector, exportable as JSON. |
| **Mapper** | Links requirements to states (hybrid retrieval + LLM judge with citations). Produces the two-way gap report. |
| **Test Generator** | Rule engine (boundary, field validation, RBAC) + LLM authoring (business rules, E2E) emitting *structured, element-grounded steps*; deterministic renderer; validator; dedup. |
| **Review & Output** | QA edit/approve/reject/regenerate, versioning, RTM, CSV/JSON/Excel export, optional Jira/TestRail/Zephyr adapters. |
| **Live Run view** | Streams every agent event (reasoning, tool calls, screenshots, discoveries, cost) to the UI as it happens, with pause/stop/human-input controls. |
| **LLM Gateway** | One configuration point for the model provider; token/cost metering; redaction; retries. |

## 4. Multi-agent design

A **supervisor** agent plans the work and spawns **sub-agents**. Each sub-agent receives an `AgentSpec` written by the supervisor: goal, scope (role, module, URL globs, depth), extra system-prompt text, resource references, tool subset, budget and required output schema.

```
                     ┌──────────────────────────┐
                     │ Supervisor               │ orchestration tools:
                     │ plan → spawn → review    │ spawn, await, coverage, replan, finish, ask_human
                     └──┬──────────┬────────────┘ + read-only KM/BRD queries
        AgentSpec       │          │
   ┌────────────────────┼──────────┼────────────────────┐
   ▼                    ▼          ▼                    ▼
Explorer            Explorer     Test author          Critic
(Admin, "Users")    (User,"Items") (module X)          (grounding/relevance)
 crawl tools         crawl tools   KM + rule-gen tools  KM tools
   └────────── every tool call passes the TOOL GATE ───────────┘
```

Rules that hold the design together:

1. **Permissions are enforced by code.** The supervisor can *narrow* a sub-agent's tools and budget, never widen beyond what the agent kind allows or the project config permits.
2. **Credentials never enter any agent context.** Login (including TOTP generation and session refresh) is performed by code before an agent starts; agents hold only a `role_ref` to a pre-authenticated browser context.
3. **Untrusted text is data.** BRD text and page content are delimited as data in prompts and cannot change permissions, because permissions are not prompt-controlled.
4. **Sub-agents return structured results**, writing findings to the KM through tools. The supervisor's context grows with the number of agents, not the number of steps.
5. **No recursive spawning.** Only the supervisor spawns; concurrency and total spawn count are capped.
6. **Shared visited-state claiming.** Parallel explorers atomically claim a state fingerprint, so the same screen is explored once.
7. **Deterministic fallback.** The same crawl tools can be driven by a plain BFS explorer without an LLM. It is the baseline for evaluation and the fallback if a model handles tool use poorly.

## 5. End-to-end data flow

1. **Project setup** - user defines target URL, roles, credentials (encrypted), crawl/generation config, test-case template.
2. **BRD ingest** - upload → parse → chunk → extract requirements (structured output) → ambiguity flags → human can correct.
3. **Authenticate** - worker logs in per role in code (MFA/session handling), producing a browser context per role.
4. **Explore** - supervisor reads requirements + initial navigation, spawns explorers per role × module. Explorers record states, elements, forms, transitions, validation messages, screenshots, observed API calls. Everything streams to the UI.
5. **Build KM snapshot** - relational rows are the source of truth; the AGE graph and pgvector embeddings are derived. Role-permission matrix is computed from per-role observations.
6. **Map** - requirements ↔ states with confidence and citations; gap report generated.
7. **Generate** - rule engine + test-author agents produce structured test cases against the snapshot.
8. **Validate** - grounding check, role reachability, dedup, trivial filter; one regeneration attempt with validator feedback; failures logged.
9. **Review** - QA edits/approves/regenerates; versions kept.
10. **Output** - RTM, gap report, CSV/JSON/Excel, run metrics (time, tokens, cost), crawl log.

A completed KM snapshot is reusable: steps 6–10 can re-run without re-crawling. A snapshot older than a configured age is flagged stale.

## 6. Knowledge model

Three synchronized views over PostgreSQL (details in `dev_docs/backend-schema.md`):

- **Relational + JSONB (source of truth):** states, elements, forms, fields, transitions, role access, observed endpoints.
- **Graph (Apache AGE):** `(Role)-[:CAN_ACCESS]->(State)`, `(State)-[:NAVIGATES_TO {via}]->(State)`, `(State)-[:HAS_FORM]->(Form)-[:HAS_FIELD]->(Field)`, `(Requirement)-[:IMPLEMENTED_BY]->(State)`. Used for navigation paths and workflow chains.
- **Vectors (pgvector):** embeddings of requirements and state summaries for mapping and dedup.

A **state** (not a URL) is the unit of the model, because SPAs reuse URLs for different screens and have URL-less modals, tabs and wizard steps. A state's identity is a *fingerprint*: route + normalized accessibility-tree structure + open overlay.

### How discovery works (not "just a sitemap")

A sitemap is optional and rarely useful for authenticated SPAs. Discovery merges: navigation chrome after login (primary seed), interactive exploration (tabs, accordions, modals, wizards), DOM links, BRD-guided search (screen names mentioned in the BRD become explicit targets), observed network calls, optional router tables, and optional `sitemap.xml`/`robots.txt`. Every discovered state records which source found it.

### Safe exploration

- **Network-level guard:** in safe mode, Playwright routing aborts non-read requests triggered during exploration (`POST/PUT/PATCH/DELETE`, GraphQL `mutation`), except configured safe patterns. This is a hard guarantee even if a button is misclassified.
- **Action classifier:** keyword/role denylist (delete, remove, pay, submit order, logout, …) plus per-project configuration to allow specific actions.
- **Validation probing without submit:** required/format/length validation messages are captured via blur and input events; submit-based probing is opt-in.

## 7. Test generation and the anti-hallucination design

- **Steps are structured**, not prose: `{action, element_ref, value_spec}`. The LLM references elements by KM handle. A deterministic **renderer** produces the human-readable step using the real label and navigation path (`Admin → Users → "Add User"`). The model never types a label, so it cannot invent one.
- **Rules where logic is mechanical:** boundary values, equivalence classes, required/format validation, and role-based access are generated from crawled metadata and the permission matrix, with no LLM.
- **LLM where judgment is needed:** business rules, end-to-end workflows, priority, and wording of expected results - constrained to the requirement text and mapped states.
- **Validation gate:** schema → grounding (element/state exists, role can reach it) → dedup (normalized step hash + embedding cosine) → trivial filter → one feedback regeneration → drop and log.
- **Test types covered:** positive, negative, boundary value, field validation, role-based access, end-to-end, business-rule.
- **Output fields:** Test Case ID, Requirement ID, Module, Title, Preconditions, Test Steps, Test Data, Expected Result, Priority, Test Type, Role (template-configurable).

## 8. Prompt strategy

| Agent / call | Strategy |
|---|---|
| **Requirement extraction** | One structured-output call per section (prompt `extract-v2`, temperature 0). Each requirement must include a verbatim quote, which code **verifies against the section text**: a near-miss is replaced by the document's own sentence and anything else is discarded, so an invented requirement cannot be stored. Scope/background statements are excluded by instruction. Vague or incomplete wording is *flagged, not guessed*: deterministic wording checks (vague terms, TBD, unmeasurable targets) are merged with the model's own judgement, and a flagged requirement needs a human decision before it can be approved or used for test generation. |
| **Supervisor** | Fixed base prompt (mission, safety, tool semantics) + project context summary. Reasons in plan → spawn → review loops with coverage stats as feedback. |
| **Explorer** | Fixed base prompt + supervisor's goal text + scope. Observation = compact accessibility snapshot with opaque element handles, never raw HTML. Told to stop on no-new-state streaks. |
| **Mapping judge** | Prompt `map-v3`. Candidates come from hybrid retrieval (keyword coverage + embedding cosine, rank-fused) over *screen cards* built only from what the crawler recorded; route variants of one page are a single candidate. The judge receives one requirement and those cards and may answer only with candidate aliases, each with a confidence, a one-sentence rationale and evidence labels. Code **verifies every cited label exists on the card**; a claim with no checkable evidence cannot exceed 40%. A requirement keeps at most four screens within 0.2 of its best, and an empty answer for a behavioural requirement gets one second look before it becomes a gap. **Gaps are derived**, not stored by the model: a requirement is unmatched if it has no effective mapping (confirmed, or proposed above the threshold; rejected never counts) and a crawled screen is undocumented if no requirement maps to it. They are recomputed after every human confirm/reject/link, and are flagged when the crawl was partial. |
| **Test generation** | Two sources, one validator. **Rules** (no model) derive mechanical cases from recorded constraints: required/empty, email format, length and numeric boundaries, option lists, a positive form flow, and who can open a screen; expected messages are the app's own when the crawl saw one, otherwise the case says the expectation is *inferred*. The **author agent** (prompt `author-v3`) writes business-rule and end-to-end scenarios over a requirement's mapped screens; it may only name screens and labels printed on the cards, so code drops any case that references an invented or destructive control. A form's path is the chain of controls that opens it (page → row menu → dialog), per-row controls add a "a row must exist" precondition, and a role is said to lack access only if its crawl finished. Cases are de-duplicated (exact signature across requirements, with `also_covers` keeping traceability; cosine for near-duplicates) and trivial ones are filtered. Steps are structured (`action`, `target label`, `value`) and rendered to text; test data never contains credentials. |
| **Critic** | Scores business relevance and flags trivial/duplicate cases; grounding is checked in code, not by the LLM. |
| **Common** | Untrusted text is delimited as data; structured output everywhere; low temperature for extraction/judging; prompt templates are versioned files; every prompt and response is stored in the trace (with secrets redacted). |

## 9. Login challenges

| Challenge | Approach |
|---|---|
| Standard form | Per-role login recipe (selectors/URL, success indicator); optional first-run heuristic to locate the form. |
| TOTP MFA | TOTP seed stored encrypted; code generated in the worker at login. |
| SMS / e-mail OTP | Prefer a test-environment bypass. Otherwise the run pauses and asks the user for the code in the Live Run view (`needs_human`). |
| CAPTCHA | Not solved. Require the test environment to disable it, or import a pre-authenticated browser `storage_state`. |
| Session timeout | Detect redirect-to-login or 401, re-authenticate in code, and resume at the last frontier. |
| Role differences | One isolated browser context and one explorer set per role; permission matrix derived from observation. |

## 10. Security and privacy

- **Credentials:** encrypted at rest (Fernet; key from environment/secret manager), decrypted only inside the worker's auth module. Never logged, never in prompts, never in screenshots' captions or the KM. Generated tests use placeholders such as `<ADMIN_PASSWORD>`. A test asserts known secrets never appear in outgoing LLM payloads.
- **Confidentiality of BRD and app data:** BRD text and page structure *do* go to the LLM and the embedding endpoint. Mitigations: provider routing restricted to no-retention/no-training providers for confidential projects; PII masking option; minimal context per call (element catalogs, not full pages).
- **LLM choice (justification):** the chat models are Groq-hosted `gpt-oss-120b` (planning, authoring, extraction) and `gpt-oss-20b` (high-volume exploration steps), chosen after measurement: 0.5–1.3 s per call, correct tool calling, strict JSON-schema output, and generous rate limits - fast enough for multi-agent runs, which free-tier models (~10 s/call) were not. Chat and embeddings are separate OpenAI-compatible endpoints, configured by API key.
- **Free models and confidentiality:** free-tier models cannot honor no-retention/no-training routing (verified), so they are suitable only for demo applications and non-confidential BRDs. A project marked `confidential` forces strict provider routing and refuses free models; for genuinely sensitive material use a paid zero-retention route.
- **Environment safety:** the app URL must be a test/staging environment; a project-level acknowledgement is required, and safe mode is on by default.

## 11. Configuration

Per project: crawl depth, state/step budgets, include/exclude modules and URL globs, allowed destructive actions, safe request patterns, roles, concurrency, screenshot policy, model per agent kind, thresholds (mapping confidence, dedup similarity), enabled test types, and the test-case template (column set and order). Validated by Pydantic; stored as JSON with the run snapshot so every run is reproducible.

## 12. Observability, cost and performance

- **Run metrics:** wall time per stage, tokens in/out and cost per agent and per run (cost taken from provider usage data where available).
- **Crawl log:** every URL/action recorded as crawled, skipped or blocked with a reason and discovery source.
- **Agent trace:** all agent events persisted for replay and audit; same events power the live view.

## 13. Technology choices and reasons

| Concern | Choice | Reason |
|---|---|---|
| Backend | FastAPI, SQLModel, Alembic | Already in the repo template. |
| Database | PostgreSQL 18 + Apache AGE + pgvector | Already provisioned; one store covers relational, graph and vector needs. |
| Agent framework | LangChain + LangGraph | Fits supervisor/sub-agent graphs, parallel fan-out (`Send`), human interrupts, Postgres checkpointing. |
| LLM access | `langchain-openai` against Groq (chat) and OpenRouter (embeddings) | OpenAI-compatible; swap either to self-hosted by changing its base URL. |
| Browser automation | Playwright | Reliable SPA handling, accessibility snapshots, request interception, multi-context. |
| Queue / events | Redis + arq | Async-native, fits Playwright/FastAPI; Streams give ordered live events with replay. |
| Live transport | Server-Sent Events + REST commands | Simple, proxy-friendly; controls don't need a socket. |
| Embeddings | OpenRouter embedding models, 1024-d (default a free 350M model; paid option available) | No heavy local ML stack; 1024-d fits pgvector's HNSW limit. A self-hosted OpenAI-compatible embedding server can replace it for confidential deployments. |
| BRD parsing | Docling (fallback PyMuPDF/python-docx) | Structure and table preservation. |
| Frontend | React, TanStack Router/Query, Tailwind, shadcn/ui | Already in the repo template. |
| Exports | `openpyxl`, `csv`, JSON | No framework required. |

## 14. Known limitations

- CAPTCHA is not solved; it requires environment cooperation.
- Safe mode cannot observe server-side behavior that only appears after a state-changing request; those flows are described from the BRD and structure, and flagged as "not observed".
- Business rules that exist only in the back end and never surface in the UI can be mapped only from BRD text.
- Quality on small self-hosted models is lower for extraction and mapping.
- Visual/layout and performance testing are out of scope.
