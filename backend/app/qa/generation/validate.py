"""Quality gates between generation and storage: trivial filter, exact and semantic de-duplication."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.qa.generation.schema import CaseDraft


def is_trivial(c: CaseDraft) -> str | None:
    acts = [s for s in c.steps if s.action not in {"open_screen", "verify"}]
    checks = [s for s in c.steps if s.action == "verify"]
    if c.test_type != "role_access" and not acts and not checks:
        return "it neither interacts with the screen nor checks anything on it"
    if len(c.expected_result.strip()) < 12:
        return "no meaningful expected result"
    if not c.steps:
        return "no steps"
    return None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class DedupResult:
    kept: list[CaseDraft] = field(default_factory=list)
    vectors: list[Sequence[float] | None] = field(default_factory=list)
    exact: int = 0
    semantic: int = 0
    trivial: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def dedup(
    drafts: list[CaseDraft],
    vectors: list[Sequence[float] | None] | None,
    *,
    existing_signatures: set[str] | None = None,
    threshold: float = 0.92,
) -> DedupResult:
    """Drop trivial cases, exact repeats (same steps and expectation) and near-duplicates.

    A case dropped because another requirement already has the identical case is recorded on the
    survivor as `also_covers`, so traceability does not lose the second requirement."""
    out = DedupResult()
    seen: dict[str, CaseDraft] = {}
    taken = set(existing_signatures or ())
    for i, d in enumerate(drafts):
        why = is_trivial(d)
        if why:
            out.trivial += 1
            out.reasons[why] = out.reasons.get(why, 0) + 1
            continue
        sig = d.signature()
        if sig in seen:
            first = seen[sig]
            if first.requirement_key != d.requirement_key:
                also = first.evidence.setdefault("also_covers", [])
                if d.requirement_key not in also:
                    also.append(d.requirement_key)
            out.exact += 1
            continue
        if sig in taken:
            out.exact += 1
            continue
        vec = vectors[i] if vectors else None
        if vec is not None:
            clash = next(
                (
                    k
                    for k, v in zip(out.kept, out.vectors, strict=True)
                    if v is not None
                    and k.requirement_key == d.requirement_key
                    and k.test_type == d.test_type
                    and cosine(v, vec) >= threshold
                ),
                None,
            )
            if clash is not None:
                out.semantic += 1
                continue
        seen[sig] = d
        d.evidence["signature"] = sig
        out.kept.append(d)
        out.vectors.append(vec)
    return out
