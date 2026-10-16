# Mapping results (M6)

How well requirements are linked to crawled screens, measured on the Juice Shop demo. Read the
caveats first: the numbers are a regression check, not an independent accuracy claim.

## Setup

- App: OWASP Juice Shop, one bounded crawl (Customer + Admin, both roles ended at the step budget:
  36 recorded states, 17 candidate screens after grouping route variants and dropping blank shells).
- BRD: `demo/brds/juice-shop.md`, 34 extracted requirements (see `extraction-results.md`).
- Judge: Groq `openai/gpt-oss-120b`, prompt `map-v3`. Retrieval: keyword + embedding (OpenRouter).
- Gold: `demo/brds/juice-shop.mapping-gold.json`, **written by the developer of this tool**, per
  requirement: acceptable screens, or "expected gap", or "ambiguous" (not scored). 24 of the 34
  requirements are scored; 10 are ambiguous by design (vague wording, role descriptions, rules that
  need the role matrix).
- Script: `backend/scripts/eval_mapping.py "<project>" <gold.json>`.

## Result (4 identical runs of the final configuration)

| Metric | Run 1 | Run 2 | Run 3 | Run 4 |
|---|---|---|---|---|
| Requirement precision (a requirement reported as matched really is) | 0.92 | 0.93 | 0.92 | 0.92 |
| Requirement recall (an expected match was found) | 0.92 | 1.00 | 0.92 | 0.92 |
| Expected gaps reported as gaps | 9/10 | 9/10 | 9/10 | 9/10 |
| Mapping-level precision (each proposed link is acceptable) | 0.88 | 0.89 | 0.88 | 0.88 |
| Undocumented screens found (of 7 labelled) | 4/7 | 4/7 | 4/7 | 4/7 |

Target in the PRD: precision and recall ≥ 0.8. Met on this gold set; **n = 24, one app, one
labeller**, so a few requirements move the numbers by 4 points each.
A run takes 15–30 s and about 47k input + 15k output tokens (34 requirements).

## How it got here (what the measurements changed)

| Change | Why (measured) |
|---|---|
| Dropped the model's "not visible in the UI" verdict for functional/validation requirements (D36) | It dismissed "view product catalogue" as invisible on some runs |
| Shell controls (toolbar, side menu) listed separately; judge told to return one screen when only a shell control satisfies a requirement; at most 4 screens within 0.2 of the best (D35) | Search was mapped to every screen that had the toolbar |
| "Partial coverage is a match at lower confidence" instruction | Recall was 0.62–0.77 because the judge returned nothing when a price or column wasn't visible |
| One "second look" for an empty answer (D37) | Same prompt, three runs: recall 0.69 / 0.92 / 0.85 (judge variance). After: 0.92–1.00 |
| Blank/error shells are not screens | They were reported as undocumented features |

Before the second look, precision was 1.0 and recall unstable. The second look trades one stable
false positive for stability: **FR-2** (registration form validation) is linked at 55–68% to *Change
Password* and *Data Erasure*, which have password/email fields but are not registration. Such weak
links are marked as below-threshold or "weak" in the UI and need a person to confirm them.

## What it gets wrong, and why

1. **Data displayed on a screen is invisible to it.** A screen card holds controls, headings, form
   fields and validation messages. FR-10 ("the basket page shows each item, quantity, line price and
   total") is missed in every run: the recorded basket variant shows only *Checkout*. FR-5 ("catalogue
   cards with image, name and price") flips between found and missed. The fix is to record masked
   static text and table headers in the crawler; it needs a decision about personal data on staging
   screens and is not done.
2. **Gaps on a partial crawl are not features that are missing.** Registration, login and the
   administration page were never reached by the bounded crawl, so FR-1…FR-3 and FR-19 are reported as
   gaps (and counted as expected in the gold). The UI says so when a crawl ended at its budget.
3. **Undocumented screens are under-reported (4/7).** *Payment options* and *Digital wallet* are
   linked to the checkout requirement at ~60%, *Recycle* to the quantity rule at 68%; arguably the
   gold's labels are too strict, arguably the links are too lenient. A screen counts as documented if
   any effective mapping points to it.
4. **Labelling bias.** I labelled the gold after seeing the first run's output, and corrected one
   label (REQ-004: *Data Erasure* has a required email field, so a link there is defensible; it was
   marked ambiguous). Treat all numbers as optimistic.
5. **Confident false links exist and are not scored.** *REQ-002* ("Administrator: shop staff …", a role description the extractor turned into a requirement) is linked at 90% to *Data Erasure* because that screen has destructive "admin-looking" controls. It is in the gold as ambiguous, so it does not appear in the precision figure; a reviewer sees the evidence and rejects it.
6. **Page-title artefacts from the crawl** (the home page is named "Back", a Juice Shop page titled
   with the Express version) flow into the mapping UI. Canonical screen naming is M4 work.

## Not measured

- A second application, or a BRD written by someone else.
- Mapping against a complete crawl (the demo crawl is partial).
- Self-hosted / smaller judge models.
- Threshold sensitivity (0.5 default; per project via `generation_config.mapping_threshold`, no UI).
