# Evaluation

What was measured, how, what worked, what did not, and what is **not** known. Read §1 first.

## 1. How far these numbers can be trusted

- **Four applications, all run locally**: this repository's own template app (small, 2 roles), OWASP Juice
  Shop (a single-page app), Redmine (a classic server-rendered project tracker, roles Administrator and
  Developer) and Gitea (a git forge, roles Administrator and User). No real customer BRD or application was
  available.
- **The BRDs and the gold labels were written by the developer of the tool.** The BRDs are clean (headings,
  ids, ruled tables). Each includes requirements the product does not have (to see whether gaps are
  reported) and vague ones (to see whether they are flagged). Treat every figure as an upper bound.
- **The crawls are bounded and partial** (120-220 steps per role; Redmine and Gitea stopped at the budget on
  both roles). Several "gaps" below are crawl coverage, not missing features. The UI says so.
- **Model output varies between identical runs.** Re-running mapping on the same crawl moved requirement
  coverage by up to ±10 points (Gitea 72% to 80%, Juice Shop 54% to 68%). Every coverage number is one run.
- **Demo data was created by the developer in the targets** (a repository, an issue and an organisation in
  Gitea; accounts everywhere), never by this tool and never seeded in code.
- **Not measured at all:** business relevance as rated by a QA person, any real-world BRD (tracked changes,
  borderless tables), Confluence import against a real Confluence (tested against a mocked API only), CAPTCHA
  and a real authenticator-app login (tested against a fixture app only), self-hosted models, a second
  reviewer.

## 2. Targets from the PRD, and where we are

| Metric | Target | Template app | Juice Shop | Redmine | Gitea |
|---|---|---|---|---|---|
| Requirement coverage (testable, non-flagged, with ≥1 case) | ≥ 90% | **60%** (9/15) | **61%** (17/28) | **70%** (19/27) | **80%** (20/25) |
| Hallucinated steps (named control absent from the recorded app) | 0 | 0 / 139 | 0 / 89 | 0 / 215 | 0 / 298 |
| Executable on the live app (replay, ≤80 cases sampled) | ≥ 90% | **65%** (22/34) | **95%** (56/59) | **99%** (79/80) | **86%** (69/80) |
| Duplicate cases (structural) | ≤ 5% | 0% | 0% | 2.6% | 1.3% |
| Business relevance (QA rating ≥ 4.0, ≥ 30 cases) | ≥ 4.0 | not measured | not measured | not measured | not measured |
| Mapping precision / recall vs hand labels | ≥ 0.8 | n/a | 0.86 / 0.92 | n/a | n/a |

Verdicts:

- **Grounding: met on all four apps** (0 of 741 steps), checked independently from the database.
- **Duplicates: met** on all four.
- **Executable rate: met on two apps, missed on two.** Template app: the replay failures are cases whose
  click path goes through a control named after the signed-in user's own data (an account menu labelled with
  an e-mail, a project card named after its project). A person can run these; an automated replay cannot
  find the control. Gitea: 11 of 80 failures are mostly JavaScript editor toggles and fields that are only
  present after another widget is opened.
- **Coverage: not met on any app (60-80% vs 90%).** The causes are specific (§3), not a single defect.
- **Business relevance: not measured.** Rating sheets for all four apps are in `docs/samples/`
  (`qa-rating-sheet-*.csv`); the harness scores one when filled in.
- **Mapping: met on Juice Shop** against labels written by the developer, so optimistic.

## 3. Coverage: why 60-80%

"No mapped screen" is the sum of:

1. **Features the crawl never reached.** Budgets are bounded. Gitea's crawl, for example, spends part of its
   budget on administration pages.
2. **BRD items the application really lacks** (Kanban drag-and-drop, chat notifications, single sign-on in
   Redmine; a visual pipeline editor and a weekly digest in Gitea). The gap report is *correct* here and
   these are meant to stay uncovered: they account for 3 of the 8 uncovered Redmine requirements and 2 of the 5
   uncovered Gitea ones.
3. **Features that need an action safe mode will not take**: sign-out (blocked by design), registration (a
   page only a signed-out visitor sees).
4. **Requirements about displayed data.** Masked table headers and captions are now captured, which lets
   "the basket shows items, quantities and totals" be matched when the table exists. It does not help when
   the page is empty during the crawl (an empty basket has no header row).
5. **Judge strictness and run-to-run variance** (±10 points).

What moved the number since the first evaluation (template 60%, Juice 63%): forbidden-route probing and
masked static text (Redmine and Gitea benefit most), per-role click paths, and opening pages by address
when the clicks to them are long.

A person can raise coverage immediately by linking screens by hand (Mapping tab) and by seeding the target
with demo data before the crawl.

## 4. Per-component results

### BRD extraction (M2; `dev_docs/extraction-results.md`)
Real model, three formats of the same BRD: 27/27 expected requirements found with correct ids, 4/4 vague
requirements flagged, 0 invented (an invented requirement is discarded in code because its quote is not in the
document). The Redmine BRD yielded 30 requirements, the Gitea BRD 26. **Scanned PDFs** are now read with OCR:
a rendered scan of the Juice Shop BRD (3 pages, 300 dpi) was recovered with **95% word recall** against the
text version, headings and lists intact, lines the engine is unsure of (< 0.7) are left out and counted, and
the parse carries a warning. Real scans (skew, stamps, handwriting) are not measured. A rendered scan is an
easy case.

### Mapping (M6; `dev_docs/mapping-results.md`)
Juice Shop, 23 scored requirements: precision **0.86**, recall **0.92**, expected gaps reported 8 of 10,
mapping-level precision 0.91, undocumented screens found 4 of 7. Known failures: displayed data on an empty
page (FR-10, the basket) and weak links to a related screen (FR-1, FR-2: "change password") that a human has
to reject.

### Test generation (M7; `dev_docs/generation-results.md`)
- **Grounding:** 0 ungrounded steps in 741 steps across four apps. Author cases that name an invented,
  destructive or non-control label (including a page heading, an e-mail or a URL-named control) are dropped by
  code.
- **Role access is observed, not guessed.** After a multi-role crawl each role opens, by address and
  read-only, the screens only other roles reached. Redmine: the Developer role was *observed* to be sent away
  from 6 administration pages (+1 not found); the template app's User role was redirected from `/admin`. A
  "role cannot open X" case reproduces exactly that (open the address, verify denial) and is marked
  `observed`; without a probe it stays `not_observed` with a note.
- **Replay** is the instrument that found most generator bugs (see §6): paths that ended in headings, menus
  not opened, `navigate /x` transitions rendered as clicks, role-specific menus used by another role, a
  required-field test that "cleared" a dropdown.
- **Expected results** carry their provenance (Redmine: about half observed from the app, a third taken from
  the requirement, the rest inferred).

## 5. Explorer comparison (agentic vs deterministic)

Same project, same roles, same limits; the agentic run used the strong model for planner and explorers.

| | Template app, deterministic | Template app, agentic | Gitea, deterministic | Gitea, agentic |
|---|---|---|---|---|
| Screens found | **37** | 28 | 115 | **123** |
| Forms / fields | 9 / 35 | 6 / 28 | **102 / 570** | 84 / 371 |
| Transitions | **53** | 36 | **137** | 123 |
| Time | **116 s** | 207 s | **311 s** | 588 s |
| Agents / tool steps | 3 / 141 | 10 / 207 | 3 / (budget) | 21 / 348 |
| LLM calls / tokens | **0 / 0** | 205 / 866k | **0 / 0** | 346 / 1.80M |
| Roles finished | both | both | both stopped at budget | both finished |

Reading: the agentic explorer found **fewer screens at ~1,000x the tokens** on the small app and **a few more
screens but fewer forms and fields** on the larger one, at 2x the time and 1.8M tokens. It did not beat the
deterministic explorer on any measure except raw screen count on Gitea, where it also was not stopped by a step
budget. Its value is in what it can do that code cannot (follow a requirement-driven goal, recover from an
unexpected state); on these four apps that was not needed. **The deterministic explorer is therefore the
default**; the agentic mode is a setting. Both go through the same tool gate: neither can submit a form or
change data.

Addendum: the table above measured the first agentic design (one planner, one agent per module, per role). It
was replaced by the **supervised** design (scouts measure each role, the supervisor splits the work so shared
pages are explored once and role-only pages by their role, the team is sized by measured complexity). One
real-model run on the template app (2 roles): 22 screens, 6 agents (2 scouts, 3 explorers, 1 supervisor), about
400k tokens and 89 s, against 866k tokens for the earlier design and 39 screens for the deterministic explorer.
It spends far fewer tokens than before and is still not better than the deterministic crawler at finding
screens; one run, not a benchmark.

## 6. Bugs found by real targets (and fixed)

- Redmine: a **sign-out link** was opened by address (denylist only covered clicks), after which every
  navigation failed; a **"leave site?" prompt** (the probe types into fields and never saves) cancelled every
  later navigation; **download/feed addresses** (`.csv`, `.atom`) aborted navigation; a long error message
  overflowed a column and lost the log row.
- Gitea: login lives at `/user/login` (the login page is now discovered from usual paths); **in-flow
  dropdown triggers carry `role=menu`** and made the whole page look like an open menu (one screen found, 0
  forms); the crawler's seed addresses were turned into "click" steps in test cases.
- Redmine: every page had the same title (the site name is an `<h1>` in the header; page titles are `<h2>`).
- Generation: required-field tests "cleared" dropdowns; identical access cases for one page opened with
  different filters; role-specific menus used by another role; controls named after user data.

## 6a. Purpose-built test applications (known ground truth)

Three small applications written for this (`demo/apps/`, see its README): **LeaveTrack** (single-page app,
three roles, HR signs in with TOTP), **Stockroom** (server-rendered, a read-only role, pagination, a 3-step
wizard, delete confirmation) and **HelpDesk** (hash-routed single-page app, sessions that expire). Each has a
BRD (`demo/brds/{leavetrack,stockroom,helpdesk}.{md,docx,pdf}`) that contains exact rules, requirements the app
does not implement, vague requirements and one feature it omits, plus answer keys for extraction and mapping.
Because the developer wrote both the app and the BRD, these numbers are *upper bounds*, but the rules and gaps
are known rather than guessed.

| | LeaveTrack | Stockroom | HelpDesk |
|---|---|---|---|
| BRD extraction (PDF): expected found / flagged / wrong id | 25/25, 2/2, 0 | 24/24, 1/1, 0 | 22/22, 2/2, 0 |
| Screens / forms recorded | 23 / 5 | 16 / 10 | 9 / 8 |
| Role denials observed by probing | Manager 4, Employee 5 | Clerk 2, Viewer 5 | Agent 2, Customer 3 |
| Mapping precision / recall (answer key) | 0.92 / 0.75 | 0.93 / 0.81 | 1.00 / 1.00 |
| Requirement coverage (testable) | 65% | 76% | 91% |
| Cases / steps naming an absent control | 52 / 0 of 126 | 71 / 0 of 147 | 57 / 0 of 85 |
| Duplicates | 0% | 0% | 0% |
| Executable on the live app (replay) | 77% | 90% | 91% |

What they exposed (all fixed):
- **PDF ligatures**: the BRD rendered to PDF stores "fi" as one glyph, so "notified" and "five" were not found
  by quote verification and 3 of 25 requirements were missing from the extraction. Ligatures are now expanded.
- **Hash-route links were ignored** (`href="#/tickets/new"` was treated like an in-page anchor): the HelpDesk
  crawl found 1 screen; it finds 9 now.
- **The sign-in screen was never a screen**: requirements about signing in could not be mapped. It is now
  recorded (once, untouched, with its fields and rules), and cases on it are generated for a signed-out visitor
  and replayed in a fresh, signed-out browser.
- **Session expiry works**: with a 4-second session the HelpDesk crawl signed in again 4 times and still found
  the same 8 screens. **TOTP works** from the container worker (HR role).

Remaining limits seen here: requirements about displayed numbers (dashboard counts) cannot be shown by controls;
a wizard's later steps and a delete confirmation page are never reached because reaching them needs a submit or a
destructive link (safe mode, by design); LeaveTrack's replay rate is understated because some authored cases
check that a role does **not** see a control, so "control not found" is their expected result.

## 7. Cost and time

Reported by the Metrics tab (time and provider-reported tokens). Typical runs with Groq `gpt-oss-120b`:

| Run | Template app | Juice Shop | Redmine | Gitea |
|---|---|---|---|---|
| Crawl, deterministic (no model) | 2 min | 3 min (120 steps) | 4.5 min (partial) | 5 min (partial) |
| Mapping | ~25 s | ~40 s | ~50 s | ~30 s |
| Test generation | ~25 s | ~28 s | ~35 s | ~35 s |
| Agentic crawl (tokens) | 866k | n/a | n/a | 1.8M |

Everything except the agentic crawl is cheap. The crawl time is the target's response time, not the tool's:
Redmine and Gitea answer in hundreds of milliseconds per page.

## 8. What worked

- **Grounding by construction**: quotes verified against the BRD, evidence against the screen, step labels
  against the crawl, all in code. 0 hallucinated labels in 741 steps.
- **Honest provenance**: expected results say whether they were observed, inferred or taken from the
  requirement; gaps say why; partial crawls are flagged; role denials are `observed` only when probed.
- **Safe by default**: no run submitted a form or mutated data (network guard; the access probe, the agentic
  explorers and the session re-login all stay behind it); credentials and one-time codes never reached a model,
  a log or an event.
- **Replay on the real app** as a regression instrument: it found most of the generator's real bugs.
- **Four different apps** found bugs the fixture and the first two apps never did (§6).

## 9. Limitations

- **Coverage is below target** and varies by ±10 points between identical runs.
- The crawler never submits forms: many expected results are inferred; success paths are not verified.
- Data-dependent screens (an empty vs populated list) are recorded as variants; per-row controls need data.
  Controls named after a user's own data cannot be located by an automated replay.
- CAPTCHA is refused (never solved): use a stored browser session. SMS/e-mail OTP is supported only as "a
  person types the code"; single sign-on only through a stored session.
- Confluence import is verified against a mocked API only.
- OCR is verified on a rendered scan only. Messy BRDs are unmeasured; English only.
- Groq's data-retention terms for confidential BRDs were not verified; the `confidential` flag refuses
  free-tier models but does not make a hosted provider private. A self-hosted endpoint is the answer.
- Free-tier embeddings are rate-limited; retrieval falls back to keywords and near-duplicate detection to
  exact matching.
- Jira Cloud / TestRail / Zephyr Scale integration is built (bring-your-own-key) but only exercised against mocked platform APIs and a TestRail stand-in; no real account has been used, so field mappings (custom fields, required fields, workflows) may need adjusting per instance.
- Single-user deployment assumptions.

## 10. Reproduce

```bash
cd backend
set -a; . ../.env; set +a; export POSTGRES_SERVER=localhost
# whole pipeline for an app (project, roles, BRD, crawl, map, generate); roles.json holds the demo logins
uv run python scripts/run_pipeline.py --name "Redmine (demo)" --url http://localhost:3001 \
    --brd ../demo/brds/redmine.md --roles roles.json --api http://localhost:8000/api/v1
uv run python scripts/evaluate.py "Redmine (demo)" --sheet rating.csv                      # §2 metrics
uv run python scripts/replay_cases.py "Redmine (demo)" --limit 80 > replay.json             # executable rate
uv run python scripts/compare_explorers.py "Redmine (demo)" --modes deterministic,agentic   # §5
uv run python scripts/eval_mapping.py "Juice Shop (demo)" ../demo/brds/juice-shop.mapping-gold.json
```
Demo targets: `docker compose --profile demo-juice|demo-redmine|demo-gitea up -d <service>` (see
`demo/DEMO.md` for the accounts and demo data to create). The QA rating sheets are stratified samples of up
to 30 cases; fill in *Relevance (1-5)* and pass the file as `--ratings` to get the mean.
