"""Two-way gap analysis, derived from the effective mappings (so a human confirm/reject re-derives it
without any LLM call).

- requirement_unmatched: a live requirement with no effective mapping.
- feature_unmentioned: a crawled screen (grouped by route) that no requirement maps to.

A mapping is effective when it is confirmed, or proposed with confidence >= threshold. Rejected
mappings never count. Gaps a person has accepted keep that status while they remain true."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, select

from app.qa.mapping.cards import ScreenCard, load_cards
from app.qa.models import Gap, Requirement, RequirementMapping

DEFAULT_THRESHOLD = 0.5
FUNCTIONAL_TYPES = {"functional", "validation", "acceptance_criterion"}


@dataclass
class GapHint:
    code: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def effective(m: RequirementMapping, threshold: float) -> bool:
    if m.review_status == "rejected":
        return False
    return m.review_status == "confirmed" or m.confidence >= threshold


def _req_severity(r: Requirement, code: str) -> str:
    if code == "not_ui_observable":
        return "low"
    if r.type in FUNCTIONAL_TYPES:
        return "high"
    return "medium"


def _feature_severity(card: ScreenCard) -> str:
    return "medium" if card.element_count >= 8 else "low"


def recompute_gaps(
    session: Session,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    run_id: uuid.UUID | None = None,
    hints: dict[uuid.UUID, GapHint] | None = None,
) -> dict[str, int]:
    hints = hints or {}
    reqs = session.exec(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.review_status != "rejected",
        )
    ).all()
    maps = session.exec(
        select(RequirementMapping).where(RequirementMapping.snapshot_id == snapshot_id)
    ).all()
    live = [m for m in maps if effective(m, threshold)]
    matched_reqs = {m.requirement_id for m in live}
    matched_states = {m.state_id for m in live}
    cards = load_cards(session, snapshot_id)

    existing = {
        (g.direction, g.requirement_id or g.state_id): g
        for g in session.exec(
            select(Gap).where(
                Gap.project_id == project_id, Gap.snapshot_id == snapshot_id
            )
        ).all()
    }
    desired: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}
    for r in reqs:
        if r.id in matched_reqs:
            continue
        prior = existing.get(("requirement_unmatched", r.id))
        hint = hints.get(r.id)
        if hint is None and prior is not None:
            hint = GapHint(prior.code, prior.reason, prior.evidence)
        if hint is None:
            hint = GapHint(
                "not_assessed",
                "Mapping has not been run for this requirement yet.",
            )
        desired[("requirement_unmatched", r.id)] = {
            "requirement_id": r.id,
            "code": hint.code,
            "reason": hint.reason,
            "evidence": hint.evidence,
            "severity": _req_severity(r, hint.code),
        }
    for c in cards:
        if any(sid in matched_states for sid in c.state_ids):
            continue
        desired[("feature_unmentioned", c.state_id)] = {
            "state_id": c.state_id,
            "code": "undocumented_screen",
            "reason": (
                f"The crawler found '{c.display}'"
                + (f" ({c.route})" if c.route else "")
                + " but no requirement maps to it."
            ),
            "evidence": {
                "route": c.route,
                "variants": len(c.state_ids),
                "controls": c.element_count,
            },
            "severity": _feature_severity(c),
        }

    created = removed = 0
    for key, old in list(existing.items()):
        if key not in desired:
            session.delete(old)
            removed += 1
    for key, data in desired.items():
        direction = key[0]
        g = existing.get(key)
        if g is None:
            session.add(
                Gap(
                    project_id=project_id,
                    snapshot_id=snapshot_id,
                    run_id=run_id,
                    direction=direction,
                    **data,
                )
            )
            created += 1
        elif g.status == "open":
            for k, v in data.items():
                setattr(g, k, v)
            session.add(g)
    session.commit()
    open_reqs = sum(1 for d, _ in desired if d == "requirement_unmatched")
    open_feats = len(desired) - open_reqs
    return {
        "requirements_total": len(reqs),
        "requirements_matched": len(reqs) - open_reqs,
        "requirement_gaps": open_reqs,
        "screens_total": len(cards),
        "feature_gaps": open_feats,
        "created": created,
        "removed": removed,
    }


def mapping_pairs(
    session: Session, snapshot_id: uuid.UUID
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    return {
        (m.requirement_id, m.state_id)
        for m in session.exec(
            select(RequirementMapping).where(
                RequirementMapping.snapshot_id == snapshot_id,
                col(RequirementMapping.review_status) == "rejected",
            )
        ).all()
    }
