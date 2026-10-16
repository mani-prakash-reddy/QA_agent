"""Rows for the exports: test cases (through the project's template), the RTM, gaps and the knowledge model."""

import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.qa.config import GenerationConfig, TestCaseTemplate
from app.qa.mapping.gaps import DEFAULT_THRESHOLD, effective
from app.qa.models import (
    FormField,
    Project,
    Requirement,
    RequirementMapping,
    TestCase,
    UiForm,
    UiState,
)

TYPES = [
    "positive", "negative", "boundary", "field_validation", "role_access", "e2e", "business_rule",
]  # fmt: skip


def template_of(project: Project) -> TestCaseTemplate:
    return GenerationConfig.model_validate(project.generation_config or {}).template


def basis_text(t: TestCase) -> str:
    return {
        "observed": "observed in the app",
        "inferred": "inferred from a recorded constraint",
        "requirement": "from the requirement text",
        "not_observed": "not probed in the app",
    }.get(str(t.evidence.get("basis", "")), "")


def cell(t: TestCase, req: Requirement, key: str, prefix: str) -> Any:
    if key == "tc_key":
        return (
            prefix + t.tc_key[2:]
            if t.tc_key.startswith("TC") and prefix != "TC"
            else t.tc_key
        )
    if key == "req_key":
        return ", ".join([req.req_key, *t.evidence.get("also_covers", [])])
    if key == "preconditions":
        return [f"- {p}" for p in t.preconditions]
    if key == "steps":
        return t.rendered_steps
    if key == "test_data":
        return [f"{d.get('field')}: {d.get('value')}" for d in t.test_data]
    if key == "expectation_basis":
        return basis_text(t)
    return getattr(t, key, "")


def test_sheet(
    session: Session,
    project: Project,
    *,
    status: str | None = None,
    test_type: str | None = None,
    requirement_id: uuid.UUID | None = None,
    include_rejected: bool = False,
) -> tuple[list[str], list[list[Any]]]:
    tpl = template_of(project)
    stmt = (
        select(TestCase, Requirement)
        .join(Requirement, col(Requirement.id) == col(TestCase.requirement_id))
        .where(TestCase.project_id == project.id)
        .order_by(col(TestCase.tc_key))
    )
    if status:
        stmt = stmt.where(TestCase.status == status)
    elif not include_rejected:
        stmt = stmt.where(TestCase.status != "rejected")
    if test_type:
        stmt = stmt.where(TestCase.test_type == test_type)
    if requirement_id:
        stmt = stmt.where(TestCase.requirement_id == requirement_id)
    rows = [
        [cell(t, r, c.key, tpl.id_prefix) for c in tpl.columns]
        for t, r in session.exec(stmt).all()
    ]
    return [c.header for c in tpl.columns], rows


def uncovered_reason(r: Requirement, mapped: int) -> str:
    if r.review_status == "rejected":
        return "requirement rejected"
    if r.ambiguity_flag and not r.ambiguity_resolved:
        return "wording needs a decision"
    if r.type == "nfr":
        return "non-functional: not testable through the interface"
    if mapped == 0:
        return "no matching screen (map it or link a screen)"
    return "no test case generated yet"


def rtm(session: Session, project: Project) -> list[dict[str, Any]]:
    threshold = float(
        project.generation_config.get("mapping_threshold") or DEFAULT_THRESHOLD
    )
    reqs = list(
        session.exec(
            select(Requirement)
            .where(Requirement.project_id == project.id)
            .order_by(col(Requirement.created_at), col(Requirement.req_key))
        )
    )
    maps = session.exec(
        select(RequirementMapping).where(RequirementMapping.project_id == project.id)
    ).all()
    mapped: dict[uuid.UUID, int] = {}
    snap_ids = {m.snapshot_id for m in maps}
    latest = (
        max(
            snap_ids,
            key=lambda s: max(m.updated_at for m in maps if m.snapshot_id == s),
        )
        if snap_ids
        else None
    )
    for m in maps:
        if m.snapshot_id == latest and effective(m, threshold):
            mapped[m.requirement_id] = mapped.get(m.requirement_id, 0) + 1
    tests = session.exec(
        select(TestCase).where(TestCase.project_id == project.id)
    ).all()
    by_key = {r.req_key: r for r in reqs}
    out: list[dict[str, Any]] = []
    stats: dict[uuid.UUID, dict[str, Any]] = {
        r.id: {
            "own": 0,
            "linked": 0,
            "types": dict.fromkeys(TYPES, 0),
            "approved": 0,
            "draft": 0,
            "needs_regen": 0,
            "rejected": 0,
        }
        for r in reqs
    }
    for t in tests:
        s = stats.get(t.requirement_id)
        if s is None:
            continue
        if t.status == "rejected":
            s["rejected"] += 1
            continue
        s["own"] += 1
        s["types"][t.test_type] = s["types"].get(t.test_type, 0) + 1
        if t.status in {"approved", "edited"}:
            s["approved"] += 1
        elif t.status == "needs_regen":
            s["needs_regen"] += 1
        else:
            s["draft"] += 1
        for other in t.evidence.get("also_covers", []):
            o = by_key.get(other)
            if o is not None:
                stats[o.id]["linked"] += 1
    for r in reqs:
        s = stats[r.id]
        total = s["own"] + s["linked"]
        flagged = r.ambiguity_flag and not r.ambiguity_resolved
        out.append(
            {
                "requirement_id": r.id,
                "req_key": r.req_key,
                "title": r.title,
                "type": r.type,
                "review_status": r.review_status,
                "needs_decision": flagged,
                "mapped_screens": mapped.get(r.id, 0),
                "tests_own": s["own"],
                "tests_linked": s["linked"],
                "tests_total": total,
                "by_type": s["types"],
                "approved": s["approved"],
                "draft": s["draft"],
                "needs_regen": s["needs_regen"],
                "rejected_tests": s["rejected"],
                "coverage": "covered" if total else "uncovered",
                "reason": "" if total else uncovered_reason(r, mapped.get(r.id, 0)),
            }
        )
    return out


def rtm_sheet(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Requirement ID", "Title", "Type", "Requirement status", "Needs wording decision",
        "Mapped screens", "Test cases", "Of which also covers", *[t.replace("_", " ").title() for t in TYPES],
        "Approved", "Draft", "Needs regeneration", "Coverage", "Why not covered",
    ]  # fmt: skip
    body = [
        [
            r["req_key"], r["title"], r["type"], r["review_status"],
            "yes" if r["needs_decision"] else "", r["mapped_screens"], r["tests_total"], r["tests_linked"],
            *[r["by_type"][t] for t in TYPES],
            r["approved"], r["draft"], r["needs_regen"], r["coverage"], r["reason"],
        ]
        for r in rows
    ]  # fmt: skip
    return headers, body


def gap_sheet(rows: list[Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Direction",
        "Requirement ID",
        "Requirement",
        "Screen",
        "Route",
        "Severity",
        "Status",
        "Reason code",
        "Reason",
    ]
    body = [
        [
            "Requirement without matching screen" if g.direction == "requirement_unmatched" else "Screen without requirement",
            g.req_key or "", g.requirement_title or "", g.screen_title or "", g.screen_route or "",
            g.severity, g.status, g.code, g.reason,
        ]
        for g in rows
    ]  # fmt: skip
    return headers, body


def km_sheets(
    session: Session, snapshot_id: uuid.UUID
) -> dict[str, tuple[list[str], list[list[Any]]]]:
    states = list(
        session.exec(select(UiState).where(UiState.snapshot_id == snapshot_id))
    )
    by_id = {s.id: s for s in states}
    sheets: dict[str, tuple[list[str], list[list[Any]]]] = {
        "Screens": (
            ["Kind", "Title", "Heading", "Module", "Route", "Navigation path", "Depth"],
            [[s.kind, s.title, s.heading or "", s.module or "", s.route_template or "", " > ".join(str(p) for p in s.nav_path), s.depth] for s in states],
        )
    }  # fmt: skip
    forms = list(
        session.exec(select(UiForm).where(col(UiForm.state_id).in_(list(by_id))))
    )
    fields = list(
        session.exec(
            select(FormField).where(col(FormField.form_id).in_([f.id for f in forms]))
        )
    )
    fm = {f.id: f for f in forms}
    sheets["Fields"] = (
        ["Screen", "Form", "Field", "Type", "Required", "Min length", "Max length", "Min value", "Max value", "Validation messages"],
        [
            [
                by_id[fm[f.form_id].state_id].title, fm[f.form_id].name or fm[f.form_id].submit_label or "",
                f.label, f.input_type, "yes" if f.required else "", f.min_length or "", f.max_length or "",
                f.min_value or "", f.max_value or "", "; ".join(str(m.get("message")) for m in f.validation_messages or [] if isinstance(m, dict)),
            ]
            for f in fields
        ],
    )  # fmt: skip
    return sheets
