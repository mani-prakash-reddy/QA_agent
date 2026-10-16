# Backend Schema - where data lives

| | |
|---|---|
| Status | Draft v0.1 |
| Conventions | UUID primary keys (matches existing `user`/`item`), `created_at timestamptz`, JSONB for flexible payloads, SQLModel tables in `app/models/`, Alembic migrations. Existing template tables `user` and `item` stay; `item` can be dropped later. |

## 1. Storage map

| Data | Store | Why |
|---|---|---|
| Users, projects, roles, requirements, KM, tests, runs, metrics | PostgreSQL relational tables (+JSONB) | Source of truth, transactional |
| Navigation/workflow/permission graph | Apache AGE graph `qa_kg` | Path and traversal queries; **derived** from relational tables, rebuildable |
| Requirement & state embeddings, test dedup vectors | pgvector columns (1024-d) | Mapping retrieval and dedup |
| LangGraph run state | Tables `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` created by `langgraph-checkpoint-postgres` in schema `public` (verified in M0) | Resumable runs; excluded from Alembic autogenerate |
| Live agent events | Redis Stream `run:{id}:events` (+ persisted copy in `agent_event`) | Fan-out to UI; replay |
| Job queue, state claims, control flags | Redis (arq keys, `claim:*`, pub/sub `run:{id}:control`) | Ephemeral coordination |
| BRD files, screenshots, exports | Artifact store (`/data/artifacts/...`, S3-ready interface); paths in DB | Large binaries stay out of Postgres |
| Credentials | `role_credential.ciphertext` (Fernet) | Encrypted at rest; key in env |

## 2. Entity overview

```
user ─< project ─< app_role ─1 role_credential
          │
          ├─< brd_document ─< brd_chunk
          ├─< requirement ─< requirement_mapping >─ ui_state
          ├─< run ─< agent_run ─< agent_event
          │      └─< run_stage
          │      └─< crawl_event
          ├─< app_snapshot ─< ui_state ─< ui_element
          │        │            └─< form ─< form_field
          │        ├─< transition
          │        ├─< role_state_access / role_element_visibility
          │        └─< observed_endpoint
          ├─< gap
          ├─< test_case ─< test_case_version
          │        └─< test_case_requirement
          └─< export
```

## 3. Tables

Types: `uuid`, `text`, `int`, `bool`, `ts` = `timestamptz`, `jsonb`, `vec` = `vector(1024)`. Every table with a `vec` column also stores `embedding_model text` (vectors from different models are not comparable).

### 3.1 Tenancy and configuration

**project**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| owner_id | uuid FK user.id | cascade delete |
| name | text | |
| description | text null | |
| target_base_url | text | staging only |
| environment_ack | bool | user confirmed non-production |
| confidential | bool | true → strict provider routing, free models refused (TRD §5, D12) |
| crawl_config | jsonb | `CrawlConfig` |
| generation_config | jsonb | `GenerationConfig` incl. template |
| budget_caps | jsonb | `BudgetCaps` |
| created_at / updated_at | ts | |

**app_role** - a user role *of the application under test*
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK | unique (project_id, name) |
| name | text | e.g. Admin, Manager |
| description | text null | |
| login_recipe | jsonb | login URL, selector hints, success indicator, MFA type (`none|totp|otp_human|bypass|storage_state`). **No secrets.** |

**role_credential** (write-only via API)
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| role_id | uuid FK unique | |
| ciphertext | text | Fernet token of JSON {username, password, totp_seed?} |
| key_version | int | rotation support |
| updated_at | ts | |

### 3.2 BRD and requirements

**brd_document**: id, project_id, filename, mime, source_type (`upload|confluence_export`), artifact_path, sha256, status (`uploaded|parsing|parsed|failed`), page_count, parsed_md_path, error, created_at.

**brd_chunk**: id, document_id, ordinal, section_path (text, e.g. "3.2 User Management"), page_from, page_to, text, kind (`text|table|list|diagram_caption`), embedding vec null.

**requirement**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK | |
| document_id | uuid FK null | |
| req_key | text | `REQ-001` or BRD-native id; unique (project_id, req_key) |
| key_origin | text | `brd|generated` |
| type | text | `functional|business_rule|validation|acceptance_criterion|nfr` |
| title | text | |
| description | text | |
| actors | jsonb | list of role names |
| module | text null | |
| acceptance_criteria | jsonb | list |
| source_anchor | jsonb | `{chunk_id, page, section, quote}` |
| ambiguity_flag | bool | |
| ambiguity_reasons | jsonb | list |
| review_status | text | `extracted|approved|rejected|edited` |
| embedding | vec | |
| version | int | increments on edit |
| created_at / updated_at | ts | |

### 3.3 Runs and agents

**run**: id, project_id, kind (`full|crawl|map|generate|regenerate`), status (`queued|running|paused|waiting_human|succeeded|failed|stopped`), snapshot_id null, config_snapshot jsonb, arq_job_id, started_at, finished_at, error, summary jsonb (totals: tokens, cost, duration, counts), created_by.

**run_stage**: id, run_id, stage (`ingest|auth|explore|map|generate|validate|finalize`), status, started_at, finished_at, duration_ms, tokens_in, tokens_out, cost_usd.

**agent_run**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_id | uuid FK | |
| parent_agent_id | uuid FK null | null = supervisor |
| kind | text | `supervisor|explorer|test_author|mapper|critic` |
| spec | jsonb | validated `AgentSpec` |
| status | text | `running|succeeded|failed|stopped|budget_exceeded` |
| model | text | |
| result | jsonb null | typed result summary |
| steps | int | |
| tokens_in / tokens_out | int | |
| cost_usd | numeric | provider-reported where available |
| started_at / finished_at | ts | |

**agent_event** (persisted trace; index `(run_id, seq)`)
id bigserial, run_id, agent_id null, seq bigint, ts, type text, payload jsonb. `llm_token` events are not persisted. Retention policy configurable.

**crawl_event** (crawl log; also emitted as `skipped`/`blocked` events)
id bigserial, run_id, agent_id, role_id, ts, url text null, action text null, element_label text null, status (`crawled|skipped|blocked|error`), reason text (`denylist|depth|excluded_module|duplicate_state|blocked_request|budget|auth|timeout|other`), source (`nav|link|click|network|brd|sitemap|seed`), state_id null.

### 3.4 Knowledge model (versioned per snapshot)

**app_snapshot**: id, project_id, run_id (producing run), status (`building|ready|failed`), roles_covered jsonb, stats jsonb (states, forms, fields, transitions), graph_projected bool, created_at, stale_after ts.

**ui_state** - a screen/overlay/tab/wizard-step
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| snapshot_id | uuid FK | unique (snapshot_id, fingerprint) |
| fingerprint | text | |
| kind | text | `page|modal|tab|wizard_step|drawer` |
| parent_state_id | uuid null | for overlays/steps |
| route_template | text null | e.g. `/items/:id` |
| sample_url | text null | |
| title | text | page heading/title |
| module | text null | navigation section |
| nav_path | jsonb | breadcrumb of labels from entry, e.g. ["Admin","Users"] |
| summary | text | LLM-written purpose summary |
| summary_embedding | vec | |
| screenshot_path | text null | |
| a11y_snapshot | jsonb | trimmed, value-scrubbed |
| discovered_by_agent | uuid null | |
| discovery_source | text | |

**ui_element**
id, state_id, handle (stable per state, e.g. `e12`), kind (`button|link|input|select|checkbox|radio|tab|menuitem|toggle|other`), role_aria text, label text, name_attr text null, locators jsonb (ordered candidates), attrs jsonb (disabled, readonly, href, test-id…), is_destructive bool, destructive_reason text null, visible bool, unique (state_id, handle).

**form**: id, state_id, name text, submit_element_id uuid null, is_destructive bool, purpose text null, step_index int null.

**form_field**
id, form_id, element_id, ordinal, label, input_type, required bool, min_length, max_length, min_value, max_value (nullable), pattern text null, step text null, options jsonb null (`{values:[…], truncated:bool}`), placeholder text null, help_text text null, validation_messages jsonb (`[{trigger:"empty"|"too_long"|"format", message:"…"}]`), default_present bool. **Never stores typed credential values.**

**transition**: id, snapshot_id, from_state_id, to_state_id, via_element_id null, action (`click|navigate|submit|open_overlay|close_overlay|tab_switch`), kind (`navigation|overlay|submit`), observed_by_role_id, observed bool (false = inferred), request_summary jsonb null.

**role_state_access**: snapshot_id, role_id, state_id, access (`visible|forbidden|not_found|redirected`), evidence text; PK (snapshot_id, role_id, state_id).
**role_element_visibility**: snapshot_id, role_id, element_id, visible bool, enabled bool; PK (snapshot_id, role_id, element_id).
> The **role-permission matrix** is a query/view over these two tables, plus `forbidden` probes (direct navigation attempts to states discovered by other roles).

**observed_endpoint**: id, snapshot_id, method, path_template, first_state_id, status_codes jsonb, role_ids jsonb, in_openapi bool null.

### 3.5 Mapping and gaps

**requirement_mapping** (implemented, migration `e5f6a7b8c9d0`): id, project_id, requirement_id, snapshot_id, state_id (the group's representative screen), confidence float, rationale text, citations jsonb (`[{"label"}]`, each verified to exist on the screen), retrieval_score, method (`judge|manual`), review_status (`proposed|confirmed|rejected`), run_id, created_at, updated_at; unique (requirement_id, snapshot_id, state_id).

`ui_state` also gained `embedding vector(1024)` + `embedding_model` (a cache of the screen-card vector; no ANN index, search is exact per snapshot).

**gap** (implemented): id, project_id, snapshot_id, run_id, direction (`requirement_unmatched|feature_unmentioned`), requirement_id null, state_id null, code (`judged_absent|no_candidate|not_ui_observable|not_assessed|undocumented_screen`), reason text, evidence jsonb (nearest screens, route, variants), severity (`low|medium|high`), status (`open|accepted|resolved`), created_at. Rows are re-derived from the effective mappings after every run and every human decision.

### 3.6 Test cases

> **M7 implementation notes.** `test_case` (migration `f6a7b8c9d0e1`) and `test_case_version` (M8, `a7b8c9d0e1f2`: version, content snapshot of the editable fields, change_type `generated|edited|regenerated`, change_note, changed_by) exist. `test_case_requirement` and the `export` table were **not** built: extra requirements live in `evidence.also_covers` and exports are generated on request. Differences from the design: `state_id`, `role_name`, `evidence jsonb` (`basis: observed|inferred|requirement|not_observed`, `rule`, `field`, `also_covers[]` = other requirements an identical case covers, `needs_rows`), `status` also allows `edited`, `test_data` is a list of `{field, value, kind}` (never a credential: passwords are `<valid password>` placeholders), `embedding` is filled when the embedding service is reachable. Priority scale is P1–P4.

**test_case**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| project_id | uuid FK | |
| tc_key | text | `TC-0001` per project template; unique (project_id, tc_key) |
| requirement_id | uuid FK | primary requirement |
| snapshot_id | uuid FK | grounded against this snapshot |
| module | text | |
| title | text | |
| preconditions | jsonb | list |
| steps | jsonb | structured `Step[]` (refs) |
| rendered_steps | jsonb | rendered text, regenerated when KM/labels change |
| test_data | jsonb | values / placeholders |
| expected_result | text | |
| priority | text | template scale (e.g. P1–P4) |
| test_type | text | `positive|negative|boundary|field_validation|role_access|e2e|business_rule` |
| role_id | uuid FK | |
| origin | text | `rule|llm|manual` |
| status | text | `draft|approved|rejected|needs_regen` |
| signature | text | dedup hash; index |
| embedding | vec | |
| current_version | int | |
| created_by_run | uuid FK null | |
| created_at / updated_at | ts | |

**test_case_requirement**: test_case_id, requirement_id, PK both - additional requirements covered (RTM counts both primary and linked).
**test_case_version**: id, test_case_id, version, content jsonb (full snapshot of editable fields), change_type (`generated|edited|regenerated|status`), change_note text, changed_by user null, created_at.

### 3.7 Outputs

**export**: id, project_id, run_id null, kind (`test_cases|rtm|gaps|km`), format (`xlsx|csv|json`), filters jsonb, artifact_path, created_by, created_at.

RTM, coverage and cost dashboards are **queries/views**, not stored tables.

> **M3 implementation notes.** Created as designed: `app_snapshot`, `ui_state`, `ui_element`, `form`, `form_field`, `transition`, `role_state_access`. Differences: elements carry a stable `key` (`kind|label|region[/row]|nth`) instead of per-snapshot handles; `ui_state` has `heading`, `depth`, `discovery_source`; `form_field.validation_messages` entries are `{trigger, message, source(app|native)}`; embedding columns, `observed_endpoint` and `role_element_visibility` arrive in M4/M5. Event `parent_id` is persisted inside `agent_event.payload`.

> **M2 implementation notes.** `brd_document`, `brd_chunk`, `requirement` exist as designed, with these differences: `requirement.ambiguity_resolved` (a human accepted the wording), `ambiguity_reasons[]` entries are `{source: heuristic|llm, reason, severity: blocking|info}` (only `blocking` sets `ambiguity_flag`), `key_origin` also allows `manual`, `source_anchor` is `{chunk_id, section, page, quote}`, `brd_document` tracks `status/chunk_count/requirement_count/error/run_id` and is unique per `(project_id, sha256)`. `requirement.embedding` is `vector(1024)` with an HNSW cosine index; `brd_chunk` has no embedding yet. Original files live at `ARTIFACT_DIR/{project}/brd/{document}/original.<ext>`.

## 4. Graph (Apache AGE) - `qa_kg`

| Node label | Properties |
|---|---|
| `Role` | id, name, project_id |
| `State` | id, snapshot_id, title, module, kind, route_template |
| `Form` | id, name |
| `Field` | id, label, input_type, required |
| `Requirement` | id, req_key, title |

| Edge | From → To | Properties |
|---|---|---|
| `CAN_ACCESS` | Role → State | access |
| `NAVIGATES_TO` | State → State | via_label, action, role_id |
| `HAS_FORM` | State → Form | |
| `HAS_FIELD` | Form → Field | ordinal |
| `IMPLEMENTED_BY` | Requirement → State | confidence |

Projection is idempotent: delete + rebuild per snapshot from relational tables. Used by `kb_path`, workflow-chain discovery for E2E tests, and the UI graph view. Every connection that runs Cypher must `LOAD 'age'` and have `ag_catalog` on `search_path` (DB default already set in `db/init-extensions.sql`; verify under pooling).

## 5. Vector usage

| Column | Used for |
|---|---|
| `requirement.embedding` | requirement → state retrieval |
| `ui_state.summary_embedding` | retrieval target; also page similarity |
| `brd_chunk.embedding` | optional context retrieval for authoring |
| `test_case.embedding` | semantic dedup |

Index: HNSW with cosine ops on each. Dimension 1024 (default `liquid/lfm-2.5-embedding-350m:free`; alternative `voyageai/voyage-4`). pgvector's HNSW limit is 2000 dims (verified), so 2048-d models are excluded. Switching model = re-embed + update `embedding_model`.

## 6. Redis keyspace

| Key | Type | Purpose |
|---|---|---|
| `arq:*` | arq internal | job queue |
| `run:{run_id}:events` | Stream | live events (trimmed) |
| `run:{run_id}:control` | Pub/Sub | pause/resume/stop |
| `run:{run_id}:input:{prompt_id}` | String (TTL) | human input delivery (e.g., OTP) |
| `claim:{snapshot_id}:{fingerprint}` | String NX (TTL) | state claiming across explorers |
| `lock:run:{run_id}` | String | single-writer guard |

## 7. Artifact layout

```
/data/artifacts/{project_id}/
  brd/{document_id}/original.<ext>, parsed.md
  runs/{run_id}/screenshots/{state_id}.png
  exports/{export_id}.<ext>
```

## 8. Indexes (initial)

- `requirement (project_id, req_key)` unique; `requirement (project_id, review_status)`
- `ui_state (snapshot_id, fingerprint)` unique; `(snapshot_id, module)`
- `ui_element (state_id, handle)` unique
- `agent_event (run_id, seq)`; `crawl_event (run_id, status, reason)`
- `test_case (project_id, status)`, `(project_id, requirement_id)`, `(project_id, signature)`
- HNSW on the four embedding columns

## 9. Migrations plan

1. `qa_core`: project, app_role, role_credential
2. `qa_brd`: brd_document, brd_chunk, requirement
3. `qa_runs`: run, run_stage, agent_run, agent_event, crawl_event
4. `qa_km`: app_snapshot, ui_state, ui_element, form, form_field, transition, role_*, observed_endpoint
5. `qa_mapping`: requirement_mapping, gap
6. `qa_tests`: test_case, test_case_version, test_case_requirement, export
7. `qa_graph`: `create_graph('qa_kg')` and labels
8. Vector indexes (after tables)

The existing `a1b2c3d4e5f6` migration already enables `vector` and `age`. Alembic `env.py` must exclude the `checkpoint*` tables from autogenerate (they live in `public`).

## 10. Data lifecycle and privacy

- Deleting a project cascades to everything above and removes its artifact directory.
- Secrets never copied into `config_snapshot`, events, traces or exports.
- `agent_event.payload` for tool results is value-scrubbed before persistence.
- Snapshots older than `stale_after` are flagged, not deleted; retention policy for events is configurable.
