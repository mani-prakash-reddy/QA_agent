# App Flow

Brief document: how a user moves through the product. Screens are built on the existing template layout (sidebar + content). Detailed visuals are in `design-brief.md`.

## 1. Navigation map

```
Login (existing)
 └─ Projects (list)                         /projects
     └─ Project workspace                   /projects/:id
         ├─ Overview        status, latest run, coverage %, cost
         ├─ Setup           target URL, roles & credentials, crawl/generation config, template
         ├─ BRD & Requirements              /projects/:id/requirements
         ├─ Runs            list + start     /projects/:id/runs
         │    └─ Live Run   agent tree · live feed · browser view · coverage · cost   /runs/:id
         ├─ Knowledge Model states, graph, role matrix, crawl log      /projects/:id/model
         ├─ Mapping & Gaps                  /projects/:id/gaps
         ├─ Test Cases      review table + detail drawer              /projects/:id/tests
         ├─ RTM                                                       /projects/:id/rtm
         └─ Exports & Metrics
 Settings (existing: profile, password) · Admin (existing: users)
```

## 2. Primary flow (happy path)

1. **Create project** → name, target URL, "this is a test environment" acknowledgement.
2. **Add roles** → name, login recipe (URL/selectors/MFA type), credentials (write-only; shown as "saved").
3. **Upload BRD** → parsing progress → requirements table appears with ambiguity flags.
4. **Review requirements** → fix, approve, resolve or accept flags. (Optional but recommended before generation.)
5. **Start run** → choose scope (full / crawl only / generate only), review resolved config + budget cap → *Start*.
6. **Watch live** → agent tree grows; per-agent feed streams; screenshots update; coverage counters rise; cost meter ticks. User may pause/stop, or answer a prompt (e.g., OTP).
7. **Inspect model** → states list, graph view, role matrix, crawl log (crawled/skipped/blocked with reasons).
8. **Check mapping & gaps** → confirm/reject proposed mappings; review the two gap lists.
9. **Review tests** → filter (requirement, type, role, status) → open case → edit / approve / reject / regenerate with feedback; bulk approve.
10. **RTM & export** → coverage matrix; export Excel/CSV/JSON; download metrics.

## 3. Secondary flows

| Flow | Steps |
|---|---|
| **Regenerate only** | Tests page → select cases or a requirement → *Regenerate* (+ feedback) → run `regenerate` → new versions, history kept. |
| **Re-run without crawl** | Start run → kind `generate` → uses latest ready snapshot; banner if stale. |
| **MFA / OTP prompt** | Run enters `waiting_human` → Live Run shows prompt card → user enters code → run resumes. |
| **Run failed / stopped** | Run page shows failure reason + partial results; *Resume* continues from checkpoint where supported. |
| **Replay a finished run** | Open run → events load from storage → same Live Run UI in replay mode (scrubbable). |
| **Edit requirement after generation** | Requirement edited → dependent test cases marked `needs_regen`. |
| **Add a role later** | Add role → *Crawl* run scoped to that role → matrix and states update in a new snapshot. |

## 4. Run state machine

```
queued → running ⇄ paused
            │  ↘ waiting_human → running
            ├→ succeeded
            ├→ failed
            └→ stopped
Agent: running → succeeded | failed | stopped | budget_exceeded
Test case: draft → approved | rejected | needs_regen → (regenerate) → draft
Requirement: extracted → approved | rejected | edited
```

## 5. Key screen contents (functional)

| Screen | Must show |
|---|---|
| **Requirements** | ID, title, type, module, actors, ambiguity badge + reasons, source quote (click → BRD location), review status, mapped screens count |
| **Live Run** | Agent tree with status/budget; selected agent's feed (reasoning summaries, tool calls, results); screenshot panel; counters (states, forms, fields, skipped, blocked); token/cost; controls |
| **Knowledge Model** | State list with filters (role, module, kind); state detail (elements, forms/fields with validations, transitions, screenshot); graph view; role matrix; crawl log |
| **Gaps** | Tab 1 requirements without a matching feature; tab 2 app features not in the BRD; mapping confidence and rationale; actions accept/resolve |
| **Test Cases** | Table (ID, requirement, module, title, type, priority, role, status); detail with rendered steps, test data, expected result, version history, grounded-in state links |
| **RTM** | Requirement rows × test type columns with counts; totals; uncovered/flagged highlighted; click → filtered test list |
| **Metrics** | Duration by stage, tokens/cost by agent and stage, coverage, duplicate rate, skipped counts |

## 6. Error and edge states

- Parsing failed → show reason, allow re-upload.
- Login failed for a role → run pauses at auth with a clear message and per-role retry.
- No states found → show crawl log summary and likely causes (login failure, blocked by config).
- Budget exceeded → partial results kept; banner names which cap was hit.
- Stale snapshot → warning with one-click re-crawl.
- LLM provider error/rate limit → automatic retry; visible in the feed; run fails only after retries are exhausted.
