"""Evaluation harness: the PRD metrics for one project, computed from what is stored.

    uv run python scripts/evaluate.py "<project>" [--replay replay.json] [--ratings ratings.csv]
                                      [--mapping-gold gold.json] [--sheet out.csv] [--json]

Automatic metrics (no model, no browser): requirement coverage, grounding (every step's control or
field label exists in the recorded application), duplicate rate, run time and tokens. Optional inputs:
a replay result (scripts/replay_cases.py output), a mapping gold file, and a filled QA rating sheet.
`--sheet` writes a stratified sample for a human to rate 1-5 for business relevance; the score is
only reported when a rated sheet is supplied."""

import argparse
import asyncio
import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.mapping.cards import norm_label
from app.qa.models import (
    AppSnapshot,
    FormField,
    Project,
    Requirement,
    Run,
    TestCase,
    Transition,
    UiElement,
    UiForm,
    UiState,
)

NEAR_DUPLICATE = (
    0.9  # token Jaccard over steps + expected result, same requirement and type
)


def tokens(t: TestCase) -> set[str]:
    return set(
        re.findall(
            r"[a-z0-9]+",
            " ".join([*map(str, t.rendered_steps), t.expected_result]).lower(),
        )
    )


def known_labels(session: Session, snapshot_id: Any) -> set[str]:
    states = [
        s.id
        for s in session.exec(select(UiState).where(UiState.snapshot_id == snapshot_id))
    ]
    labels: set[str] = set()
    for e in session.exec(select(UiElement).where(col(UiElement.state_id).in_(states))):
        labels.add(norm_label(e.label))
    forms = list(session.exec(select(UiForm).where(col(UiForm.state_id).in_(states))))
    for f in forms:
        labels.add(norm_label(f.submit_label or ""))
    for f in session.exec(
        select(FormField).where(col(FormField.form_id).in_([f.id for f in forms]))
    ):
        labels.add(norm_label(f.label))
    for t in session.exec(
        select(Transition).where(Transition.snapshot_id == snapshot_id)
    ):
        labels.add(norm_label(t.via_label))
    labels.discard("")
    return labels


RUBRIC = """You are a senior QA lead reviewing generated test cases for a business analyst. For each case, given the
requirement it covers, rate 1-5 how USEFUL it is to the team:
5 = clearly tests the requirement with specific steps and a precise, checkable expected result;
4 = useful, small gaps; 3 = acceptable but vague or partly redundant; 2 = weak or only loosely related;
1 = trivial, wrong or meaningless. Judge only what is written. Answer with the score and one short reason."""


class Rating(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str = ""


async def judge_cases(items: list[tuple[str, str]]) -> list[Rating]:
    """Rate (requirement text, case text) pairs with the strong model. An automated PROXY for a QA rating."""
    from app.qa.llm.gateway import get_chat_model

    model = get_chat_model("mapper").with_structured_output(Rating)
    sem = asyncio.Semaphore(4)

    async def one(req: str, case: str) -> Rating:
        async with sem:
            try:
                out = await model.ainvoke(
                    [
                        ("system", RUBRIC),
                        ("user", f"Requirement:\n{req}\n\nTest case:\n{case}"),
                    ]
                )
                return out if isinstance(out, Rating) else Rating.model_validate(out)
            except Exception:
                return Rating(score=3, reason="the judge could not answer")

    return list(await asyncio.gather(*(one(r, c) for r, c in items)))


def evaluate(
    name: str,
    replay: str | None,
    ratings: str | None,
    sheet: str | None,
    judge: bool = False,
) -> dict[str, Any]:
    with Session(engine) as s:
        p = s.exec(select(Project).where(Project.name == name)).one()
        reqs = list(s.exec(select(Requirement).where(Requirement.project_id == p.id)))
        cases = [
            t
            for t in s.exec(select(TestCase).where(TestCase.project_id == p.id))
            if t.status != "rejected"
        ]
        runs = list(s.exec(select(Run).where(Run.project_id == p.id)))
        snaps = {t.snapshot_id for t in cases}
        labels: set[str] = set()
        for sid in snaps:
            labels |= known_labels(s, sid)
        snap = s.get(AppSnapshot, next(iter(snaps))) if snaps else None

        live = [r for r in reqs if r.review_status != "rejected"]
        decided = [
            r for r in live if not (r.ambiguity_flag and not r.ambiguity_resolved)
        ]
        testable = [r for r in decided if r.type != "nfr"]
        covered_ids = {t.requirement_id for t in cases}
        for t in cases:
            covered_ids |= {
                r.id for r in live if r.req_key in t.evidence.get("also_covers", [])
            }
        cov = lambda rs: (
            round(sum(1 for r in rs if r.id in covered_ids) / len(rs), 3)
            if rs
            else None
        )  # noqa: E731
        from app.qa.exports.build import rtm

        reasons = Counter(
            r["reason"]
            for r in rtm(s, p)
            if r["coverage"] == "uncovered" and r["review_status"] != "rejected"
        )

        # grounding: every named control / field must exist somewhere in the recorded application
        steps = bad_steps = bad_cases = 0
        violations: list[str] = []
        for t in cases:
            hit = False
            for st in t.steps:
                if st.get("action") in {"open_screen", "verify"} or not st.get(
                    "target"
                ):
                    continue
                steps += 1
                if norm_label(st["target"]["label"]) not in labels:
                    bad_steps += 1
                    hit = True
                    violations.append(f"{t.tc_key}: {st['target']['label']}")
            bad_cases += hit

        # duplicates: same requirement, type and role, acting on the same controls in the same order,
        # expecting the same thing (numbers aside: "40 characters" vs "41" is a boundary pair, not a
        # repeat). Wording-only differences are caught by this too, unlike the generator's exact hash.
        def shape(t: TestCase) -> tuple[Any, ...]:
            acts = tuple(
                (st["action"], norm_label(st["target"]["label"]))
                for st in t.steps
                if st.get("target")
            )
            expected = re.sub(
                r"\d+", "#", re.sub(r"\W+", " ", t.expected_result.lower())
            ).strip()
            return (t.requirement_id, t.test_type, t.role_name, acts, expected)

        shapes = Counter(shape(t) for t in cases)
        dup = {t.tc_key for t in cases if shapes[shape(t)] > 1}
        sigs = Counter(t.signature for t in cases)

        by_type = Counter(t.test_type for t in cases)
        basis = Counter(str(t.evidence.get("basis", "?")) for t in cases)
        kinds: dict[str, dict[str, float]] = {}
        for r in runs:
            k = kinds.setdefault(r.kind, {"runs": 0, "seconds": 0.0, "in": 0, "out": 0})
            k["runs"] += 1
            if r.started_at and r.finished_at:
                k["seconds"] += (r.finished_at - r.started_at).total_seconds()
            k["in"] += int(r.summary.get("input_tokens") or 0)
            k["out"] += int(r.summary.get("output_tokens") or 0)

        out: dict[str, Any] = {
            "project": name,
            "snapshot_partial": bool(
                snap
                and any(
                    v == "budget_exceeded"
                    for v in (snap.stats.get("roles") or {}).values()
                )
            ),
            "requirements": {
                "total": len(live),
                "wording_decision_pending": len(live) - len(decided),
                "non_functional": sum(1 for r in decided if r.type == "nfr"),
                "testable": len(testable),
            },
            "coverage": {
                "of_decided_requirements": cov(decided),
                "of_testable_requirements": cov(testable),
                "uncovered_testable": len(
                    [r for r in testable if r.id not in covered_ids]
                ),
                "why_uncovered": dict(reasons),
            },
            "test_cases": {
                "total": len(cases),
                "by_type": dict(by_type),
                "expectation_basis": dict(basis),
                "origin": dict(Counter(t.origin for t in cases)),
            },
            "grounding": {
                "steps_checked": steps,
                "ungrounded_steps": bad_steps,
                "cases_with_ungrounded_step": bad_cases,
                "examples": violations[:5],
            },
            "duplicates": {
                "identical_signature_cases": sum(n - 1 for n in sigs.values() if n > 1),
                "near_duplicate_cases": len(dup),
                "near_duplicate_rate": round(len(dup) / len(cases), 3)
                if cases
                else None,
            },
            "runs": {
                k: {**v, "seconds": round(v["seconds"], 1)} for k, v in kinds.items()
            },
        }

        if replay:
            r = json.loads(Path(replay).read_text())
            out["executable"] = {
                k: r[k]
                for k in ("replayed", "executable", "executable_rate", "by_type")
            }
        if sheet and cases:
            rnd = random.Random(11)
            groups: dict[str, list[TestCase]] = {}
            for t in cases:
                groups.setdefault(t.test_type, []).append(t)
            pick: list[TestCase] = []
            while len(pick) < min(30, len(cases)):
                for g in groups.values():
                    if g and len(pick) < 30:
                        pick.append(g.pop(rnd.randrange(len(g))))
                if not any(groups.values()):
                    break
            req_by_id = {r.id: r for r in reqs}
            with open(sheet, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "Test Case ID",
                        "Requirement",
                        "Type",
                        "Title",
                        "Steps",
                        "Expected result",
                        "Relevance (1-5)",
                        "Comment",
                    ]
                )
                for t in pick:
                    w.writerow(
                        [
                            t.tc_key,
                            f"{req_by_id[t.requirement_id].req_key}: {req_by_id[t.requirement_id].title}",
                            t.test_type,
                            t.title,
                            "\n".join(map(str, t.rendered_steps)),
                            t.expected_result,
                            "",
                            "",
                        ]
                    )
            out["rating_sheet"] = {"path": sheet, "cases": len(pick)}
        if judge and cases:
            if not sheet:  # the same stratified sample the sheet would hold
                rnd = random.Random(11)
                groups2: dict[str, list[TestCase]] = {}
                for t in cases:
                    groups2.setdefault(t.test_type, []).append(t)
                pick = []
                while len(pick) < min(30, len(cases)):
                    for g in groups2.values():
                        if g and len(pick) < 30:
                            pick.append(g.pop(rnd.randrange(len(g))))
                    if not any(groups2.values()):
                        break
                req_by_id = {r.id: r for r in reqs}
            items = [
                (
                    f"{req_by_id[t.requirement_id].req_key}: {req_by_id[t.requirement_id].title}. {req_by_id[t.requirement_id].description}",
                    f"{t.title}\nPreconditions: {'; '.join(map(str, t.preconditions))}\nSteps: {' / '.join(map(str, t.rendered_steps))}\nExpected: {t.expected_result}",
                )
                for t in pick
            ]
            rated = asyncio.run(judge_cases(items))
            scores = [r.score for r in rated]
            out["business_relevance_proxy"] = {
                "note": "Scored by an AI model with a QA-lead rubric. A proxy: it is NOT a human QA rating.",
                "rated": len(scores),
                "mean": round(sum(scores) / len(scores), 2) if scores else None,
                "distribution": {str(k): scores.count(k) for k in range(1, 6)},
                "lowest": [
                    {
                        "case": t.tc_key,
                        "title": t.title,
                        "score": r.score,
                        "reason": r.reason,
                    }
                    for t, r in sorted(
                        zip(pick, rated, strict=False), key=lambda x: x[1].score
                    )[:5]
                ],
            }
        if ratings:
            rows = list(csv.DictReader(open(ratings, encoding="utf-8-sig")))
            vals = [
                int(r["Relevance (1-5)"])
                for r in rows
                if (r.get("Relevance (1-5)") or "").strip().isdigit()
            ]
            out["business_relevance"] = {
                "rated": len(vals),
                "mean": round(sum(vals) / len(vals), 2) if vals else None,
            }
        return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--replay")
    ap.add_argument("--ratings")
    ap.add_argument("--sheet")
    ap.add_argument(
        "--judge",
        action="store_true",
        help="rate the sample with an AI model (a proxy, not a human rating)",
    )
    a = ap.parse_args()
    sys.stdout.write(
        json.dumps(
            evaluate(a.project, a.replay, a.ratings, a.sheet, a.judge),
            indent=2,
            default=str,
        )
        + "\n"
    )
