# QA Agent

Upload a business requirements document (BRD), point the tool at a **staging** web application, and it
produces reviewable, traceable test cases: it extracts the requirements, explores the live app per
role, maps requirements to screens (and reports what is missing in both directions), generates
grounded test cases, and exports them with a requirement traceability matrix (RTM).

Everything an agent does is visible live in the UI (a run page with the agent tree, event feed and
screenshots). The tool only reads the target application: credentials are encrypted and never sent to
a model, form submissions and destructive actions are blocked at the network layer, and every claim in
a generated test case is checked against what the crawler actually recorded.

Built on the FastAPI full-stack template (FastAPI, SQLModel, Postgres 18 + pgvector + Apache AGE,
React/Vite/shadcn/TanStack) with LangChain/LangGraph, Playwright and arq.

## Documents

| Read | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How it works, safety, models, data |
| [docs/evaluation.md](docs/evaluation.md) | What was measured, what worked, the limitations |
| [docs/samples/](docs/samples/) | Real outputs: tests, RTM, gap report, knowledge model |
| [demo/DEMO.md](demo/DEMO.md) | A 10-minute walkthrough on the demo apps |
| [dev_docs/](dev_docs/README.md) | PRD, TRD (decision log), schema, plan, status, per-milestone results |

## Setup

Requirements: Docker with Compose, and for development `uv` (Python 3.14) and a Chromium for the
crawler tests (`uv run playwright install chromium`).

```bash
cp .env.example .env
# edit .env: set SECRET_KEY, POSTGRES_PASSWORD, FIRST_SUPERUSER_PASSWORD,
# CREDENTIAL_ENCRYPTION_KEY (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
# and the model settings below
docker compose up -d --build        # db, redis, backend, worker (Chromium), frontend
```

UI <http://localhost:5173> (log in as `FIRST_SUPERUSER`) · API docs <http://localhost:8000/docs>.

**Models** (any OpenAI-compatible endpoints; chat and embeddings are configured separately):

| Variable | Used for | Used by the demo runs |
|---|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST` | requirement extraction, mapping judge, test author | Groq, `openai/gpt-oss-120b` / `-20b` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` | retrieval and near-duplicate detection (preferred when set; 1536-d `text-embedding-ada-002`) | Azure OpenAI |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM=1536` | the same, through an OpenAI-compatible endpoint when Azure is not set | OpenRouter (free tier is rate-limited, and its 1024-d models no longer fit the schema) |

Only the worker holds the model keys; the API never sees them. For confidential BRDs set the
project's *confidential* flag (free-tier models are then refused) or point the variables at a
self-hosted server. Data-retention terms of your provider are your responsibility to check.

**Demo targets** (the tool never creates accounts; you register them in the app yourself):

```bash
docker compose --profile demo-juice up -d juice-shop     # OWASP Juice Shop, host port 3000
docker compose --profile demo-redmine up -d redmine      # Redmine 6, host port 3001
docker compose --profile demo-gitea up -d gitea          # Gitea, host port 3002
docker compose --profile demo-leave --profile demo-stock --profile demo-help up -d --build demo-leave demo-stock demo-help   # purpose-built apps, ports 3101-3103 (demo/apps/README.md)
```

The template app of this repository (frontend :5173) is a fourth target. Juice Shop keeps its users in
memory: restarting the container deletes the accounts you registered. `demo/DEMO.md` lists what to create in
each target.

## Using it

1. **Projects → New project**: name, application URL, and tick that it is a test/staging environment.
2. **Roles**: add each user role of the app with its test credentials (write-only; stored encrypted). A
   role can sign in with a password, a password plus an authenticator secret, a password plus a code you
   type when the run asks (the run page shows a prompt), or a saved browser session (for CAPTCHA/SSO).
3. **Requirements**: optionally add *Reference documents* (a glossary and an OpenAPI/Swagger file; they sharpen mapping and field rules). Upload the BRD (PDF, scanned PDF, Word, Markdown, text) or import a Confluence page.
   Review the extracted requirements;
   vague ones are flagged and need a decision before approval.
4. **Crawl**: explores the app once per role (read-only) and records screens, forms, fields, paths, masked
   table headers and captions, and what each role gets when it opens another role's screens by address.
   Settings (the *Settings* button on the Crawl and Test cases tabs): depth, screen and step limits, parallel
   agents, deterministic or agentic explorer, include/exclude modules, seeds, forbidden-route probing, text
   capture, opt-in walking of multi-step forms, test types, cases per requirement.
5. **Mapping**: links requirements to screens with evidence; shows requirement gaps and screens no
   requirement mentions; confirm, reject or link by hand.
6. **Test cases**: generate, review (edit, history, bulk approve, regenerate with feedback), see the
   **Traceability matrix**, **Export** (Excel/CSV/JSON) and set the **Template** (columns, names, IDs).
7. **Integrations** (sidebar): save your own Jira Cloud, TestRail or Zephyr Scale key once (encrypted, write-only); then **Push to…** on a project's Test cases tab sends the approved cases there (preview first; pushed cases are updated, not duplicated).
8. **Metrics**: time, tokens and an estimated dollar cost per kind of run (`LLM_PRICE_*`; embeddings excluded).

What is sent to which provider,: [docs/privacy.md](docs/privacy.md).
Sample exports for seven apps: `docs/samples/` (regenerate with `scripts/export_samples.py`); an AI-judge
relevance proxy: `scripts/evaluate.py --judge`.

## Development

```bash
cd backend && uv sync
FASTAPI_ENV=development uv run pytest tests -q     # needs the compose db + redis; 330 tests
uv run ruff check app && uv run mypy app           # strict on app code
```

- Backend containers bake code at build time: `docker compose build backend worker && docker compose up -d backend worker`.
- The frontend container has no bind mount (see `development.md`).
- Evaluation and replay scripts: `backend/scripts/` (`run_pipeline.py`, `evaluate.py`, `compare_explorers.py`, `eval_mapping.py`, `eval_brd_extraction.py`,
  `replay_cases.py`).


## Appendix: what has been achieved

**Pipeline, end to end.** BRD in (PDF, scanned PDF, Word, Markdown, text, Confluence page, optional glossary and
OpenAPI file) -> grounded requirement extraction with verbatim quotes and ambiguity flags -> a read-only crawl of
the live application once per role -> a knowledge model (screens, forms, fields, paths, role access, graph
projection) -> requirement-to-screen mapping with two-way gaps -> grounded test cases (positive, negative,
boundary, role access, business rule, end to end) -> traceability matrix, Excel/CSV/JSON export with a
configurable template, review workflow, and push to Jira Cloud, TestRail or Zephyr Scale with your own key.

**Agentic architecture.** A supervisor plans the crawl from scouts' measurements and spawns explorers sized to
the app's complexity. Every agent's screens, actions and log are visible live in the UI, with pause, stop and
feed controls. A deterministic explorer is the default; the agentic one is optional.

**Safety.** Credentials are encrypted at rest and never reach a model or a log. Safe mode blocks destructive
actions and form submits. Login handles passwords, authenticator codes, typed one-time codes and stored sessions;
CAPTCHA is refused, never solved.

**Also built.** Dashboard and workflow step bar, settings dialog for crawl and generation options, run metrics
with time, tokens and an estimated dollar cost, OCR for scans and diagrams, wizard walking (opt-in), five demo
targets with answer keys, sample exports in `docs/samples/`.

**Measured on seven apps** (full table and caveats in [docs/evaluation.md](docs/evaluation.md)):

| Measure | Result | Target |
|---|---|---|
| Steps naming a control absent from the recorded app | 0 of about 800 | 0 |
| Duplicate cases | 0% | at most 5% |
| Executable on the live app (replay) | 84-97% (template app 46%, stale crawl) | 90% |
| Requirement coverage | 53-91%, target met on 1 of 7 | 90% |
| Business relevance | AI-judge proxy 2.8-3.5 of 5; no human rating | 4.0 |
| BRD extraction on the purpose-built apps | 25/25, 24/24, 22/22 requirements, flags correct | n/a |

**Not achieved or not verified.** Coverage and relevance are below target. Jira, TestRail and Zephyr pushes were
only run against mocks. Wizard walking and diagram OCR are tested on fixtures and rendered images only. Groq's
data-retention terms are unverified. Sources of truth: `dev_docs/STATUS.md`, `docs/evaluation.md`.
