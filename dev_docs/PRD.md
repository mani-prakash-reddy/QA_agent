# Product Requirements Document (PRD)

| | |
|---|---|
| Product | QA Agent — AI-powered contextual test case generation from a BRD |
| Status | Draft v0.1 |
| Source | `problem_statement.md` (assignment brief) |
| Related | `docs/architecture.md`, `dev_docs/TRD.md`, `dev_docs/backend-schema.md`, `dev_docs/implementation-plan.md` |

## 1. Problem

Writing test cases from a BRD is slow, and generic AI output ("click the button") is unusable: it names screens and fields that do not exist and cannot be traced to requirements. QA teams need test cases that match **how the application really behaves** and that **prove requirement coverage**.

## 2. Goal

Given a BRD, a staging URL and per-role credentials, the system explores the application autonomously and produces reviewable, traceable, executable-quality test cases with a requirement traceability matrix (RTM) and a two-way gap report.

### Success metrics (target values to be confirmed against the demo app)

| Metric | Definition | Target |
|---|---|---|
| Requirement coverage | % of non-flagged requirements with ≥1 meaningful approved/draft test | ≥ 90% |
| Grounding / hallucination rate | % of steps referencing a screen/field/label absent from the KM | 0% by construction; verified by replay sample |
| Executable rate | % of sampled tests whose steps replay on the real app without a missing-element error | ≥ 90% |
| Duplicate rate | % of case pairs above similarity threshold after dedup | ≤ 5% |
| Business relevance | QA/BA rating, 1–5, on a ≥30-case sample | ≥ 4.0 average |
| Mapping accuracy | Precision/recall of requirement→screen mapping vs hand-labelled set | ≥ 0.8 / 0.8 |
| Run cost & time | Reported per run (no hard target; trend tracked) | Reported |

## 3. Users

| Persona | Needs |
|---|---|
| **QA Lead** (primary) | Coverage visibility, RTM, review/approve workflow, confidence the output is real. |
| **QA Engineer** | Edit/regenerate cases, export to the team's tool/format, trust steps are executable. |
| **Business Analyst** | See which BRD items are unmapped or ambiguous; see features the BRD never mentioned. |
| **Project Admin** | Configure environments, roles, credentials, models, budgets; control cost and safety. |

## 4. Scope

**In scope:** everything in the assignment's Functional Requirements A–F and Non-Functional Requirements, plus the live agent view.
**Out of scope (v1):** executing test cases as automated scripts, performance/visual/accessibility testing, mobile native apps, production environments, multi-tenant billing.
**Stretch:** Jira/TestRail/Zephyr export adapters; executable replay verification as a built-in feature; Confluence live pull (v1 accepts an export).

## 5. Functional requirements

IDs map to assignment sections (A–F).

### A. BRD understanding
- **FR-A1** Upload PDF and DOCX BRDs (Confluence page export as stretch); parse text, tables, lists, user stories; diagrams captioned where possible.
- **FR-A2** Extract functional requirements, business rules, actors/roles, validations and acceptance criteria as structured records.
- **FR-A3** Preserve BRD-native requirement IDs; otherwise assign unique IDs (`REQ-001`…). IDs are stable across re-parses of the same document.
- **FR-A4** Each requirement stores a source anchor (document, page/section, verbatim quote).
- **FR-A5** Flag vague/incomplete requirements with reasons; flagged items are not auto-generated unless a human approves them.
- **FR-A6** Users can edit, merge, split and approve extracted requirements.

### B. Application exploration
- **FR-B1** Log in automatically per role; explore each role independently.
- **FR-B2** Handle SPAs, dynamic menus, modals, tabs, multi-step forms.
- **FR-B3** Capture pages/states, navigation paths, form fields (type, mandatory, length, format, min/max), buttons, dropdown values, error messages, screenshots.
- **FR-B4** Safe by default: no destructive actions (delete, payment submit, logout) unless explicitly allowed; enforced at the network level.
- **FR-B5** Documented and implemented handling for OTP/MFA, CAPTCHA and session timeout.
- **FR-B6** Exploration is configurable: depth, budgets, module include/exclude.
- **FR-B7** Multiple crawler agents may run in parallel (per role × module) under a supervisor.

### C. Application knowledge model
- **FR-C1** Build page/state inventory, element map, workflow graph and role-permission matrix.
- **FR-C2** Persist as a versioned snapshot; reuse without re-crawling; flag staleness.
- **FR-C3** Export the model as JSON.

### D. Requirement-to-application mapping
- **FR-D1** Link each requirement to implementing screens/workflows with confidence and rationale.
- **FR-D2** Report requirements with no matching app feature.
- **FR-D3** Report app features that no requirement mentions.

### E. Test case generation
- **FR-E1** Steps use real UI labels and navigation paths.
- **FR-E2** Cover positive, negative, boundary value, field validation, role-based access, end-to-end and business-rule scenarios.
- **FR-E3** Fields: Test Case ID, Requirement ID, Module, Title, Preconditions, Test Steps, Test Data, Expected Result, Priority, Test Type, Role.
- **FR-E4** Remove duplicate and trivial cases.
- **FR-E5** The test-case template (columns/order/naming) is configurable.
- **FR-E6** Generated test data never contains real credentials; placeholders are used.

### F. Traceability and output
- **FR-F1** RTM: test count per requirement (and by type); uncovered and flagged requirements highlighted.
- **FR-F2** Export Excel, CSV and JSON for test cases, RTM, gap report and knowledge model.
- **FR-F3** Review workflow: edit, approve, reject, regenerate (case- or requirement-level, with optional feedback); version history.
- **FR-F4** (stretch) Push to Jira / TestRail / Zephyr.

### G. Live run experience (added)
- **FR-G1** Every agent's activity (reasoning summary, tool calls, results, screenshots, discoveries, skips, cost) is visible live in the UI.
- **FR-G2** Users can pause, resume and stop a run or an individual agent.
- **FR-G3** The run asks the user for input when needed (e.g. OTP) and resumes.
- **FR-G4** Finished runs can be replayed from stored events.

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 Security | Credentials encrypted at rest, never hard-coded, never logged, never sent to an LLM; enforced by architecture and tested. |
| NFR-2 Privacy | BRD and app data treated as confidential; provider routing restricted to non-retaining providers; documented justification; fully self-hosted mode supported by configuration. |
| NFR-3 Configurability | Depth, modules, template, models, budgets, thresholds configurable per project. |
| NFR-4 Cost/performance | Time and token cost reported per run, per stage and per agent. |
| NFR-5 Logging | Crawl log of what was crawled/skipped and why; agent trace persisted. |
| NFR-6 Reproducibility | Each run stores its config and prompt versions; regeneration from a stored snapshot needs no re-crawl. |
| NFR-7 Reliability | Runs are resumable after worker crash; agents have hard step/token/time caps. |
| NFR-8 Code quality | Typed Python (mypy strict, ruff per repo config), tests per component, documented. |
| NFR-9 Usability | Review table usable with ≥500 test cases (filter, sort, bulk approve). |

## 7. User stories (acceptance headlines)

| ID | Story | Acceptance |
|---|---|---|
| US-1 | As a QA lead I create a project with a staging URL and per-role credentials. | Credentials are stored encrypted and never shown again after saving. |
| US-2 | I upload a BRD and see extracted requirements with flags. | Each requirement shows ID, type, source quote, ambiguity reasons. |
| US-3 | I start a run and watch agents work live. | Agent tree, tool calls and screenshots update without refresh; I can stop the run. |
| US-4 | I see what was crawled and skipped and why. | Crawl log filterable by status/reason/role. |
| US-5 | I inspect the role-permission matrix. | Matrix shows pages/actions by role. |
| US-6 | I see the gap report. | Two lists: unmatched requirements; unmentioned features. |
| US-7 | I review generated tests. | Edit, approve, reject, regenerate with feedback; version history retained. |
| US-8 | I get an RTM and exports. | RTM counts match test-case table; Excel/CSV/JSON download. |
| US-9 | I see time and token cost for a run. | Totals and per-stage/agent breakdown shown. |
| US-10 | I rerun generation without re-crawling. | Uses the latest snapshot; staleness warning past the configured age. |

## 8. Assumptions

- A staging/test environment is provided; production is never targeted.
- Role credentials are valid and are entered by the user in the UI (never seeded in code or config files).
- BRDs are text-based or OCR-able PDFs/DOCX in English.
- The application is reachable from the worker's network.
- No demo app is supplied. Development and evaluation use open-source applications run locally via docker compose profiles (see TRD §2.1), with accounts created by the developer. Because no real BRD exists for them, sample BRDs are authored for the demo apps (including deliberate mismatches with the app, to exercise the gap report).
- Jira/TestRail/Zephyr integration is deferred to the end of the project (stretch).

## 9. Risks

| Risk | Mitigation |
|---|---|
| Agent wanders / cost explodes | Hard budgets; no-new-state stop rule; per-agent metering; deterministic fallback. |
| Weak model tool use | Select models per agent kind; evaluate in the spike; BFS fallback. |
| MFA/CAPTCHA blocks exploration | Documented bypass/pause strategies; require env cooperation. |
| Destructive action slips through | Network-level mutation blocking independent of classification. |
| Data leakage to LLM provider | No-retention routing, minimal context, redaction, self-hosted option. |
| Python 3.14 library compatibility | Verify in spike; lower the Python floor if required. |
| SPA fingerprint instability (state explosion) | Normalized structure hashing, state caps, tuning against the fixture. |

## 10. Deliverables (from the assignment)

1. Architecture & design document → `docs/architecture.md`
2. Source code, README, setup instructions
3. Working demo on the provided application
4. Sample outputs: knowledge model, test cases, RTM, gap report → `docs/samples/`
5. Evaluation report (what worked, limitations, metrics) → `docs/evaluation.md`
