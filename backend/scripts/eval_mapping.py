"""Score a finished mapping run against a hand-labelled gold file (requirement -> screens, gaps).

    uv run python scripts/eval_mapping.py "<project name>" ../demo/brds/juice-shop.mapping-gold.json

Reports requirement-level precision/recall, gap accuracy, and the undocumented-screen list. The gold
file is written by the tool's developer, so treat the numbers as a regression check, not as an
independent accuracy claim."""

import json
import sys
import uuid
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.mapping.gaps import DEFAULT_THRESHOLD, effective
from app.qa.models import Gap, Project, Requirement, RequirementMapping, UiState


def main(project_name: str, gold_path: str) -> dict[str, object]:
    gold = json.loads(Path(gold_path).read_text())
    with Session(engine) as s:
        project = s.exec(select(Project).where(Project.name == project_name)).one()
        reqs = {
            r.req_key: r
            for r in s.exec(
                select(Requirement).where(Requirement.project_id == project.id)
            )
        }
        rows = s.exec(
            select(RequirementMapping, UiState)
            .join(UiState, col(UiState.id) == col(RequirementMapping.state_id))
            .where(RequirementMapping.project_id == project.id)
        ).all()
        gaps = list(s.exec(select(Gap).where(Gap.project_id == project.id)))
        th = float(
            project.generation_config.get("mapping_threshold") or DEFAULT_THRESHOLD
        )
        by_req: dict[uuid.UUID, list[tuple[RequirementMapping, UiState]]] = {}
        for m, st in rows:
            if effective(m, th):
                by_req.setdefault(m.requirement_id, []).append((m, st))
        gap_reqs = {
            g.requirement_id for g in gaps if g.direction == "requirement_unmatched"
        }
        feat_routes = {
            st.route_template
            for g in gaps
            if g.direction == "feature_unmentioned"
            and (st := s.get(UiState, g.state_id))
        }

    def ok(st: UiState, accepted: list[str]) -> bool:
        title = (st.heading or st.title or "").lower()
        for a in accepted:
            route, _, frag = a.partition("|")
            if (st.route_template or "") == route and frag.lower() in title:
                return True
        return False

    tp = fp_reqs = fn = tn = fp_gap = 0
    mapping_ok = mapping_total = 0
    detail = []
    for key, g in gold["requirements"].items():
        r = reqs.get(key)
        if r is None or g.get("ambiguous"):
            continue
        got = by_req.get(r.id, [])
        if g["expect"] == "match":
            hit = [m for m, st in got if ok(st, g["screens"])]
            mapping_total += len(got)
            mapping_ok += sum(1 for m, st in got if ok(st, g["screens"]))
            if hit:
                tp += 1
            else:
                fn += 1
                detail.append(
                    f"MISSED  {key}: got {[st.route_template for _, st in got]}"
                )
        else:
            if got:
                fp_reqs += 1
                mapping_total += len(got)
                detail.append(
                    f"SPURIOUS {key}: {[(st.route_template, round(m.confidence, 2)) for m, st in got]}"
                )
            else:
                tn += 1
    precision = tp / (tp + fp_reqs) if tp + fp_reqs else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    expected_routes = set(gold["undocumented_screens"]["routes"])
    found = expected_routes & feat_routes
    out = {
        "requirements_scored": tp + fn + fp_reqs + tn,
        "requirement_precision": round(precision, 3),
        "requirement_recall": round(recall, 3),
        "expected_gaps_reported": f"{tn}/{tn + fp_reqs}",
        "mapping_precision": round(mapping_ok / mapping_total, 3)
        if mapping_total
        else None,
        "undocumented_found": f"{len(found)}/{len(expected_routes)}",
        "undocumented_missed": sorted(expected_routes - feat_routes),
        "undocumented_extra": sorted(r for r in feat_routes - expected_routes if r),
        "problems": detail,
    }
    sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
