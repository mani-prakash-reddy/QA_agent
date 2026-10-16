# Test generation results (M7)

What the generator produces, how it was checked against the live application, and what is still
wrong. As with the other results documents, the numbers are a regression check on two demo apps, not
an independent accuracy claim.

## Method

- **Inputs:** the requirements (approved or not flagged), their effective mappings (M6) and the crawl
  snapshot. A requirement that is rejected, awaiting a wording decision, non-functional, or not mapped
  to a screen is **not generated for**, and the run summary says which and why.
- **Rule cases** (no model): positive form flow; required (field cleared); email format; length
  boundary at and over `maxlength`; under `minlength`; numeric below/at/at/above min/max; option
  lists; who can open a screen. **Author cases** (model `openai/gpt-oss-120b`, prompt `author-v3`):
  business-rule, scenario and end-to-end cases over the requirement's mapped screens.
- **Replay** (`backend/scripts/replay_cases.py`): signs in as each case's role, performs the clicks and
  field fills, and checks that every control the steps name can be found. It never presses the final
  click of a form flow (the submit) and the safe-mode request guard blocks any write. `verify` steps
  need a person and are not executed. "Executable" = every actionable step resolved.

## Template app (Admin + User roles, 16 requirements, bounded crawl of 14 screens)

Final configuration, one run:

| | |
|---|---|
| Test cases kept | 25 (14 rule-based, 11 scenarios) |
| Requirements with cases | 8 of 16 (the rest: no mapped screen, or wording needs a decision) |
| Screens/forms skipped as unreachable | 7 |
| **Replayed executable** | **23 of 25 (92%)** - role access 7/7, field validation 4/4, positive 6/6, business rule 3/3, negative 3/4, e2e 0/1 |
| Run time / tokens | ~25 s, ~31k tokens |

The 92% is the end of a sequence, and the earlier numbers are the useful part:

| Version | Cases | Executable | What was wrong |
|---|---|---|---|
| first | 34 | 32% | The crawler's recorded path ended in a heading ("Settings > User Settings"), role-specific page variants and budget-stopped crawls produced false "cannot open" cases, the replay matched a hidden text node |
| path from crawl nav_path | 37–38 | 62–76% (sample varied) | A dialog opened from a row menu was named as if it were a screen; fields on a dialog were used without opening it |
| path planned over transitions | 27 | 82% | The author's click that opens a dialog was repeated; start page title was a personalised greeting |
| final | 25 | 92% | - |

Cases fell from 34 to 25 because forms whose path cannot be planned are now skipped (7) instead of
emitted with a guessed path. That is the honest trade: fewer cases, none of them unrunnable by
construction.

## Juice Shop (before path planning; not replayed)

53–60 cases from 19 requirements, 0 grounding violations stored; the run reports dropped author cases
(6–24 depending on the prompt version, mostly "opens a screen and checks nothing" before display tests
were allowed). **Not replayed:** the demo container keeps its users in memory, so restarting it deleted
the test accounts; replaying needs the developer to register them again. Path planning was added after
this run, so these numbers do not describe the final generator.

## Quality gates (each has a regression test)

- A step may only name a screen alias and a label printed on that screen's card; invented and
  destructive targets drop the whole case (`ungrounded_author_cases` in the summary).
- Every case that uses a screen opened from another (dialog, menu, tab) carries the clicks that open it;
  a click the author already wrote is not repeated; an unreachable screen drops the case.
- Exact duplicates collapse across requirements and the survivor lists `also_covers`; near-duplicates
  (cosine ≥ 0.92, same requirement and type) are dropped when embeddings are available.
- Test data never contains credentials: passwords are the placeholder `<valid password>`.
- A form whose submit is destructive gets validation cases that stop before submitting and no positive
  flow.
- Re-generating keeps approved/rejected/edited cases and never reuses a `TC-` key.

## Where it is still weak

1. **Expected results are often inferred.** The crawler never submits, so it never sees a success
   message or what a form does with a valid value. Positive and boundary cases say what the constraint
   implies and are marked `inferred`/`requirement`; only validation messages the app showed on its
   own are `observed`. Review them.
2. **Reachability depends on accessible names.** Row-action icons without a label cannot be named, so
   edit/delete dialogs behind them are skipped (7 here). A labelled crawl (or the agentic explorer,
   M4) would recover them.
3. **Per-row controls need data.** Cases that start from a row say so in their preconditions; the
   replay cannot create the row, so it counts them as not executable when the list is empty.
4. **Author cases are the weakest part.** 11 scenarios here, and both failed replays were author
   cases (a role-permission case it was told not to write; an end-to-end flow). They are grounded in
   labels, not proven in behaviour.
5. **Role denial is inferred from the crawl**, not probed (`not_observed`, P3). It is only generated for
   roles whose crawl finished, and a role that reached any variant of the page counts as having access.
6. **No LLM critic pass and no version history yet** (planned; history and edit/regenerate with
   feedback are M8).
7. **One app measured end to end.** Juice Shop needs the accounts re-created before it can be
   replayed.
