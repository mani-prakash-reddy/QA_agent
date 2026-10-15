"""Measure BRD extraction quality against a gold list, using the real model (costs a few LLM calls).

    cd backend && uv run python scripts/eval_brd_extraction.py [juice-shop.pdf template-app.docx ...]

Reports: recall of expected requirements, ID accuracy, flag precision/recall, ungrounded rejections,
extra (unexpected) requirements for manual review, tokens and time.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from app.qa.brd.chunk import chunk_document
from app.qa.brd.extract import (
    Candidate,
    assign_keys,
    extract_chunk,
    finalize_reasons,
    ground_candidates,
    merge_duplicates,
    normalize,
)
from app.qa.brd.parse import parse_bytes
from app.qa.llm.gateway import UsageMeter, get_chat_model

SAMPLES = Path(__file__).resolve().parents[2] / "demo" / "brds"


async def run(name: str) -> dict[str, object]:
    stem = name.rsplit(".", 1)[0]
    gold_doc = json.loads((SAMPLES / f"{stem}.gold.json").read_text())
    gold = gold_doc["requirements"]
    acceptable = [a["match"] for a in gold_doc.get("acceptable_extras", [])]
    parsed = parse_bytes((SAMPLES / name).read_bytes(), name)
    chunks = chunk_document(parsed)
    meter = UsageMeter()
    model = get_chat_model("extractor", meter=meter)
    started = time.time()
    sem = asyncio.Semaphore(4)
    rejected = 0
    per_chunk: dict[int, list[Candidate]] = {}

    async def one(c):  # type: ignore[no-untyped-def]
        nonlocal rejected
        async with sem:
            res = await extract_chunk(model, c)
            cands, rej = ground_candidates(c, res)
            rejected += rej
            per_chunk[c.ordinal] = cands

    await asyncio.gather(*(one(c) for c in chunks))
    cands = merge_duplicates([c for k in sorted(per_chunk) for c in per_chunk[k]])
    assign_keys(cands, set())
    flags = {c.key: finalize_reasons(c) for c in cands}
    elapsed = time.time() - started

    def text(c: Candidate) -> str:
        return normalize(f"{c.data.title} {c.data.description} {c.quote}")

    matched, missing, wrong_id, used = 0, [], [], set()
    for g in gold:
        hit = next(
            (
                c
                for c in cands
                if c.key not in used and normalize(g["match"]) in text(c)
            ),
            None,
        )
        if hit is None:
            missing.append(g["key"] or g["match"])
            continue
        used.add(hit.key)
        matched += 1
        if g["key"] and hit.key != g["key"]:
            wrong_id.append((g["key"], hit.key))
    leftovers = [c for c in cands if c.key not in used]
    ok = [c for c in leftovers if any(normalize(a) in text(c) for a in acceptable)]
    extras = [
        c for c in leftovers if c not in ok
    ]  # neither expected nor explained: review these
    gold_flag = {g["key"] or g["match"] for g in gold if g["flagged"]}
    got_flag = set()
    for g in gold:
        hit = next((c for c in cands if normalize(g["match"]) in text(c)), None)
        if hit and flags[hit.key]:
            got_flag.add(g["key"] or g["match"])
    false_flags = [c.key for c in extras if flags[c.key]]
    return {
        "file": name,
        "pages": parsed.page_count,
        "chunks": len(chunks),
        "seconds": round(elapsed, 1),
        "tokens": meter.totals.input_tokens + meter.totals.output_tokens,
        "calls": meter.totals.calls,
        "expected": len(gold),
        "extracted": len(cands),
        "recall": f"{matched}/{len(gold)}",
        "missing": missing,
        "wrong_id": wrong_id,
        "ungrounded_rejected": rejected,
        "flag_recall": f"{len(gold_flag & got_flag)}/{len(gold_flag)}",
        "missed_flags": sorted(gold_flag - got_flag),
        "extra_flags": false_flags,
        "acceptable_extras": len(ok),
        "unexpected_extras": [(c.key, c.data.title[:60]) for c in extras],
    }


async def main() -> None:
    names = sys.argv[1:] or [
        "juice-shop.md",
        "juice-shop.docx",
        "juice-shop.pdf",
        "template-app.md",
    ]
    for name in names:
        print(json.dumps(await run(name), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
