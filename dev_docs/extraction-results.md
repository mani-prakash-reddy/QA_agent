# BRD Extraction: Measured Results (M2)

_Measured 2026-10-15 with `backend/scripts/eval_brd_extraction.py` against the real model (Groq `openai/gpt-oss-120b`, prompt `extract-v2`, temperature 0). These numbers feed the evaluation report (M9)._

## Method
Each sample BRD has a hand-written gold list (`demo/brds/*.gold.json`): every requirement that should be found (with its BRD id where one exists), which of them are vague and **must be flagged rather than guessed**, and extra items that are acceptable (rows of the roles/validation tables). The script runs the real pipeline (parse → chunk → extract → ground → merge → heuristics) and compares.

## Results

| Document | Format | Expected found | IDs correct | Vague flagged | Unexpected extras | Invented (discarded) | Time | Tokens |
|---|---|---|---|---|---|---|---|---|
| Juice Shop BRD | Markdown | 27/27 | all | 4/4 | 0 | 0 | 8–16 s | ~19.7k |
| Juice Shop BRD | DOCX | 27/27 | all | 4/4 | 0 | 0 | 9 s | ~19.5k |
| Juice Shop BRD | PDF (3 pages, ruled tables) | 27/27 | all | 4/4 | 0 | 0 | ~9 s | ~20k |
| Template app BRD | Markdown | 14/14 | all | 1/1 | 0 | 0 | 4 s | ~10.5k |

The 7 "acceptable extras" in the Juice Shop BRD are the validation rules and role rows that a reader would also treat as requirements.

## What the numbers do and do not say
- **Anti-hallucination is enforced by code, not hoped for.** Every requirement must carry a quote; code checks the quote exists in the section text and replaces near-misses with the document's own sentence. Unit and API tests inject an invented requirement and show it is discarded. With the real model, 0 were invented on these documents.
- **Caveat: the BRDs are clean and I wrote them.** They have headings, numbered ids, ruled tables and English prose. Messy real BRDs (scans, tracked changes, nested or borderless tables, multi-column layouts, other languages) are **not** measured and scanned PDFs are rejected (no OCR). Treat these figures as an upper bound until a real BRD is available.
- **Model flags vary slightly between runs** (4 or 5 flagged on the same file); the deterministic wording checks do not. The heuristic flags alone reproduced the 4 expected ones.
- A parser bug found by testing: a ruled one-row box in a PDF was read as a table and its paragraph (FR-14) silently vanished. Fixed, plus a **word-coverage check** that warns when parsing drops text.

## Reproduce
```
cd backend
uv run python ../demo/build_sample_brds.py          # md -> docx + pdf
uv run python scripts/eval_brd_extraction.py juice-shop.pdf
```
