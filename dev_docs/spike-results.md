# M0 Spike Results

_Run: 2026-10-15. Host: Linux, Python 3.14.7 (uv-managed) in a scratch venv; repo untouched except `compose.yml`. Spike scripts were scratch files, not committed (see "Reproducing")._

## Verdict
**Go.** The architecture is viable on the chosen stack. Eight assumptions were checked; six held, two **changed the design** (embedding dimension, privacy with free models). Four new constraints were found (token growth, page-settle detection, free-model speed/limits, devtools noise).

## 1. Library compatibility on Python 3.14

| Library | Result |
|---|---|
| langchain 1.4.6, langchain-core 1.6.10, langgraph 1.2.15, langchain-openai 1.7.2, langgraph-checkpoint-postgres 3.2.0 | Install and run |
| playwright 1.64.0 (+ Chromium headless shell) | Install and run |
| psycopg 3.3.6 (+pool), redis 5.3.1, arq | Install |
| pymupdf 1.28.2, python-docx 1.2.0, openpyxl 3.1.5 | Resolve |
| docling | Resolves, but pulls **torch/triton** (heavy image) |

**Decision:** no need to lower `requires-python`. Use **PyMuPDF + python-docx as the default BRD parsers**; make Docling an optional extra (better tables, large install).
API note: `langchain.agents.create_agent` is the current prebuilt agent entrypoint (worked with a LangGraph checkpointer and `system_prompt=`).

## 2. OpenRouter

Key facts (this account): free tier (`is_free_tier: true`), key spend limit $2.

| Check | Result |
|---|---|
| `nvidia/nemotron-3-super-120b-a12b:free` tool calling | Works (correct `browser_click({"handle":"e2"})`), via raw API and LangChain |
| Structured output (`with_structured_output`) | Works; but quality is modest (marked the vague requirement "respond quickly" as `vague=False`) → keep the heuristic ambiguity pre-pass |
| Usage/cost reporting | Provided (`usage.cost`, token counts, `provider` name) |
| 10 parallel calls | All succeeded, 3–9 s latency each |
| Single-call latency | ~10 s average in the agent loop (reasoning model) |
| Privacy routing (`provider: {data_collection:"deny"}` / `{zdr:true}`) on a free model | **Fails with 404 "No endpoints found matching your data policy"** |
| Free-tier daily request cap | Exists for free models (exact number not retrievable from docs page). **Must be measured**; budget agent runs accordingly |

### Embeddings (OpenRouter `/embeddings`)
| Model | Dim | Notes |
|---|---|---|
| `nvidia/nemotron-3-embed-1b:free` | 2048 | context 32k |
| `nvidia/llama-nemotron-embed-vl-1b-v2:free` | 2048 | context 131k, multimodal |
| `liquid/lfm-2.5-embedding-350m:free` | **1024** | context 512 tokens |
| `voyageai/voyage-4` | **1024** | works; not in the embeddings catalog list; presumably paid → counts against the $2 key limit |

## 3. Database
- **AGE 1.8.0** works on the running DB (default `search_path` includes `ag_catalog`; `LOAD 'age'` succeeded). Variable-length path query returned node titles and edge `via` labels in one result - exactly what `kb_path` needs.
- **pgvector 0.8.7**: `vector(2048)` columns are allowed but **HNSW refuses >2000 dimensions** ("column cannot have more than 2000 dimensions for hnsw index").
- **LangGraph checkpointer** `setup()` created `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` in the **`public`** schema (not a separate schema); 17 checkpoints written for one run.

## 4. End-to-end vertical (template app as target)

Pipeline exercised: code-level login → safe mode armed → LangGraph agent with 2 tools → Redis Stream events → Postgres checkpoints.

| Check | Result |
|---|---|
| Agent explored Dashboard, Items, Admin, User Settings | Yes (9 LLM calls, ~82 s wall) |
| Safe mode: agent told to try "Log out" | **Blocked by denylist**, agent informed, logout did not happen |
| Network guard: UI "Add Item → Save" after arming | **`POST /api/v1/items/` aborted; 0 rows created** |
| Credentials in LLM context | Password and API key **not present** in any message |
| Events | 28 events (`run_status`, `tool_call`, `tool_result`, `llm_usage`, `blocked`, `llm_message`, `agent_finished`) in Redis Stream |
| SSE endpoint over the stream | Works; full replay from start and resume via `Last-Event-ID` verified |
| Redis (compose) | Added, healthy |
| Juice Shop (`demo-juice` profile) | Starts, HTTP 200 on `localhost:3000` |
| Demo images | `bkimminich/juice-shop`, `orangehrm/orangehrm`, `redmine:6` exist on Docker Hub (OrangeHRM setup not yet verified) |

## 5. Findings that change the design

1. **Embedding dimension → 1024, remote via OpenRouter (not local).** 2048-d models can't use an HNSW `vector` index. Use 1024-d (`liquid/lfm-2.5-embedding-350m:free` default; `voyageai/voyage-4` as quality option; same dimension, but vectors are model-specific → store `embedding_model` and re-embed on switch). The 512-token limit is acceptable for requirement titles/summaries and state summaries; chunk/truncate longer text. This **replaces the local-embeddings decision (D9)** and weakens the "BRD text never leaves the network" claim for retrieval.
2. **Free models cannot be used for confidential data.** They do not support no-training / zero-retention routing. Add a project flag `confidential`: when true, strict provider routing is sent and free models are refused. Free models are acceptable for demo apps and authored BRDs only. Document this in the architecture doc's privacy section.
3. **Token growth per step (403 → 10,402 input tokens over 8 calls).** Each tool result re-sent the full element list (~1.7k tokens) and history accumulated. Required mitigations: compact snapshots (deduplicate repeated chrome such as the sidebar; return only *changes* after an action), cap elements per snapshot, trim older tool results from agent history, and let the deterministic explorer do bulk traversal while the agent decides *what* to explore.
4. **Page-settle detection.** After navigation the reported heading lagged the URL (`/admin` with heading "Items"). `networkidle` is insufficient; settle must wait for URL stable + heading/DOM mutation quiescence. This directly affects fingerprinting (C3 checkpoint).
5. **Dev-overlay noise.** TanStack devtools buttons appear in the accessibility tree; the crawler needs a configurable ignore list for such overlays.
6. **Speed and quotas of free models.** ~10 s/call and an unknown daily cap make long agentic crawls impractical on free models alone. Strategy: deterministic explorer for bulk traversal; agent calls reserved for planning, naming, form-purpose summaries, mapping and authoring; per-run LLM-call budget; model choice per agent kind; retries/backoff for 429.
7. **Checkpoint tables live in `public`.** Alembic autogenerate must exclude `checkpoint*` tables (or set the checkpointer's schema/search_path).

## 6. Open items after M0
- Measure the actual free-model daily request cap (run a counting loop or check the account page).
- Verify OrangeHRM setup (DB container, installer) or fall back to Redmine for Tier 3.
- Choose per-agent models after a small tool-calling comparison among free options with tools support (`google/gemma-4-31b-it:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`, `poolside/laguna-s-2.1:free`, etc.).
- Test arq job execution (library installs; not run in the spike).
- Test AGE under a pooled psycopg connection from Python (CLI test only so far).

## Reproducing
Scratch scripts (not in repo): OpenRouter embeddings/tool-calling probes, `spike_agent.py` (agent + gate + Redis + checkpointer), `guard_test.py` (network mutation guard), `sse_app.py` (SSE over Redis Stream). They can be rebuilt from this document; the production versions arrive in M1/M3.
