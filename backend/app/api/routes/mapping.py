"""Requirement <-> screen mappings, gap report, and their review."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Query, Response
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.api.routes.runs import enqueue_run
from app.models import User
from app.qa.mapping.cards import load_cards
from app.qa.mapping.gaps import DEFAULT_THRESHOLD, effective, recompute_gaps
from app.qa.models import (
    AppSnapshot,
    Gap,
    Project,
    Requirement,
    RequirementMapping,
    Run,
    UiState,
)
from app.qa.runs.kinds.map import latest_snapshot_id
from app.qa.schemas import (
    CoverageSummary,
    GapPublic,
    GapUpdate,
    MappingCreate,
    MappingPublic,
    MappingRunRequest,
    MappingUpdate,
    RunPublic,
)

router = APIRouter(tags=["mapping"])


def _threshold(project: Project, override: float | None = None) -> float:
    return float(
        override
        or project.generation_config.get("mapping_threshold")
        or DEFAULT_THRESHOLD
    )


def _current_snapshot(
    session: Session, project_id: uuid.UUID, snapshot_id: uuid.UUID | None
) -> uuid.UUID | None:
    if snapshot_id:
        snap = session.get(AppSnapshot, snapshot_id)
        if not snap or snap.project_id != project_id:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return snapshot_id
    # The snapshot the project was last mapped against, else the latest finished crawl.
    last = session.exec(
        select(RequirementMapping.snapshot_id)
        .where(RequirementMapping.project_id == project_id)
        .order_by(col(RequirementMapping.updated_at).desc())
    ).first()
    return last or latest_snapshot_id(project_id)


def _owned_mapping(
    session: Session, user: User, mapping_id: uuid.UUID
) -> tuple[RequirementMapping, Project]:
    m = session.get(RequirementMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return m, get_owned_project(session, user, m.project_id)


def _refresh_gaps(session: Session, project: Project, snapshot_id: uuid.UUID) -> None:
    recompute_gaps(session, project.id, snapshot_id, threshold=_threshold(project))


@router.post("/projects/{project_id}/mapping/run", response_model=RunPublic)
def start_mapping(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: MappingRunRequest | None = None,
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    body = body or MappingRunRequest()
    snapshot_id = body.snapshot_id or latest_snapshot_id(project_id)
    if snapshot_id is None:
        raise HTTPException(
            status_code=409, detail="There is no finished crawl yet. Run a crawl first."
        )
    _current_snapshot(session, project_id, snapshot_id)  # ownership check
    live = session.exec(
        select(func.count())
        .select_from(Requirement)
        .where(
            Requirement.project_id == project_id,
            Requirement.review_status != "rejected",
        )
    ).one()
    if not live:
        raise HTTPException(
            status_code=409, detail="There are no requirements yet. Upload a BRD first."
        )
    run = Run(
        project_id=project_id,
        kind="map",
        snapshot_id=snapshot_id,
        created_by=current_user.id,
        config_snapshot={
            "snapshot_id": str(snapshot_id),
            "threshold": body.threshold,
            "confidential": project.confidential,
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    run.arq_job_id = from_thread.run(enqueue_run, run.id)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _mapping_public(
    m: RequirementMapping, r: Requirement, st: UiState, threshold: float
) -> MappingPublic:
    return MappingPublic(
        id=m.id,
        requirement_id=m.requirement_id,
        req_key=r.req_key,
        requirement_title=r.title,
        requirement_type=r.type,
        snapshot_id=m.snapshot_id,
        state_id=m.state_id,
        screen_title=st.heading or st.title or st.route_template or "(untitled)",
        screen_route=st.route_template,
        screen_kind=st.kind,
        confidence=m.confidence,
        rationale=m.rationale,
        citations=m.citations,
        retrieval_score=m.retrieval_score,
        method=m.method,
        review_status=m.review_status,
        effective=effective(m, threshold),
        updated_at=m.updated_at,
    )


@router.get("/projects/{project_id}/mappings", response_model=list[MappingPublic])
def list_mappings(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
    requirement_id: uuid.UUID | None = None,
    state_id: uuid.UUID | None = None,
    review_status: str | None = None,
    limit: int = Query(default=500, le=2000),
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    snap = _current_snapshot(session, project_id, snapshot_id)
    if snap is None:
        return []
    stmt = (
        select(RequirementMapping, Requirement, UiState)
        .join(
            Requirement, col(Requirement.id) == col(RequirementMapping.requirement_id)
        )
        .join(UiState, col(UiState.id) == col(RequirementMapping.state_id))
        .where(
            RequirementMapping.project_id == project_id,
            RequirementMapping.snapshot_id == snap,
        )
        .order_by(col(Requirement.req_key), col(RequirementMapping.confidence).desc())
        .limit(limit)
    )
    if requirement_id:
        stmt = stmt.where(RequirementMapping.requirement_id == requirement_id)
    if state_id:
        stmt = stmt.where(RequirementMapping.state_id == state_id)
    if review_status:
        stmt = stmt.where(RequirementMapping.review_status == review_status)
    th = _threshold(project)
    return [_mapping_public(m, r, st, th) for m, r, st in session.exec(stmt).all()]


@router.patch("/mappings/{mapping_id}", response_model=MappingPublic)
def review_mapping(
    session: SessionDep,
    current_user: CurrentUser,
    mapping_id: uuid.UUID,
    body: MappingUpdate,
) -> Any:
    m, project = _owned_mapping(session, current_user, mapping_id)
    m.review_status = body.review_status
    m.updated_at = datetime.now(UTC)
    session.add(m)
    session.commit()
    _refresh_gaps(session, project, m.snapshot_id)
    session.refresh(m)
    r = session.get(Requirement, m.requirement_id)
    st = session.get(UiState, m.state_id)
    assert r is not None and st is not None
    return _mapping_public(m, r, st, _threshold(project))


@router.post("/projects/{project_id}/mappings", response_model=MappingPublic)
def add_mapping(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: MappingCreate,
) -> Any:
    """A person links a requirement to a screen the judge missed. Always confirmed."""
    project = get_owned_project(session, current_user, project_id)
    r = session.get(Requirement, body.requirement_id)
    st = session.get(UiState, body.state_id)
    if not r or r.project_id != project_id:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if not st:
        raise HTTPException(status_code=404, detail="Screen not found")
    snap = session.get(AppSnapshot, st.snapshot_id)
    if not snap or snap.project_id != project_id:
        raise HTTPException(status_code=404, detail="Screen not found")
    m = session.exec(
        select(RequirementMapping).where(
            RequirementMapping.requirement_id == r.id,
            RequirementMapping.snapshot_id == snap.id,
            RequirementMapping.state_id == st.id,
        )
    ).first()
    if m is None:
        m = RequirementMapping(
            project_id=project_id,
            requirement_id=r.id,
            snapshot_id=snap.id,
            state_id=st.id,
        )
    m.method, m.review_status, m.confidence = "manual", "confirmed", 1.0
    m.rationale = body.note.strip() or "Linked by a reviewer."
    m.updated_at = datetime.now(UTC)
    session.add(m)
    session.commit()
    _refresh_gaps(session, project, snap.id)
    session.refresh(m)
    return _mapping_public(m, r, st, _threshold(project))


def _gap_public(g: Gap, r: Requirement | None, st: UiState | None) -> GapPublic:
    return GapPublic(
        id=g.id,
        snapshot_id=g.snapshot_id,
        direction=g.direction,
        code=g.code,
        reason=g.reason,
        evidence=g.evidence,
        severity=g.severity,
        status=g.status,
        requirement_id=g.requirement_id,
        req_key=r.req_key if r else None,
        requirement_title=r.title if r else None,
        requirement_type=r.type if r else None,
        requirement_status=r.review_status if r else None,
        state_id=g.state_id,
        screen_title=(st.heading or st.title or st.route_template) if st else None,
        screen_route=st.route_template if st else None,
    )


def _gaps(
    session: Session,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID | None,
    direction: str | None,
    status: str | None,
) -> list[GapPublic]:
    if snapshot_id is None:
        return []
    stmt = (
        select(Gap, Requirement, UiState)
        .outerjoin(Requirement, col(Requirement.id) == col(Gap.requirement_id))
        .outerjoin(UiState, col(UiState.id) == col(Gap.state_id))
        .where(Gap.project_id == project_id, Gap.snapshot_id == snapshot_id)
    )
    if direction:
        stmt = stmt.where(Gap.direction == direction)
    if status:
        stmt = stmt.where(Gap.status == status)
    rank = {"high": 0, "medium": 1, "low": 2}
    rows = sorted(
        session.exec(stmt).all(),
        key=lambda t: (rank.get(t[0].severity, 3), t[1].req_key if t[1] else ""),
    )
    return [_gap_public(g, r, st) for g, r, st in rows]


@router.get("/projects/{project_id}/gaps", response_model=list[GapPublic])
def list_gaps(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
    direction: str | None = None,
    status: str | None = None,
) -> Any:
    get_owned_project(session, current_user, project_id)
    snap = _current_snapshot(session, project_id, snapshot_id)
    return _gaps(session, project_id, snap, direction, status)


@router.patch("/gaps/{gap_id}", response_model=GapPublic)
def update_gap(
    session: SessionDep, current_user: CurrentUser, gap_id: uuid.UUID, body: GapUpdate
) -> Any:
    g = session.get(Gap, gap_id)
    if not g:
        raise HTTPException(status_code=404, detail="Gap not found")
    get_owned_project(session, current_user, g.project_id)
    g.status = body.status
    session.add(g)
    session.commit()
    session.refresh(g)
    r = session.get(Requirement, g.requirement_id) if g.requirement_id else None
    st = session.get(UiState, g.state_id) if g.state_id else None
    return _gap_public(g, r, st)


@router.get("/projects/{project_id}/gaps/export")
def export_gaps(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    snapshot_id: uuid.UUID | None = None,
) -> Response:
    get_owned_project(session, current_user, project_id)
    snap = _current_snapshot(session, project_id, snapshot_id)
    rows = _gaps(session, project_id, snap, None, None)
    if format == "json":
        return Response(
            json.dumps([g.model_dump(mode="json") for g in rows], indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="gap-report.json"'},
        )
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
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
    )
    for g in rows:
        w.writerow(
            [
                "Requirement without matching screen"
                if g.direction == "requirement_unmatched"
                else "Screen without requirement",
                g.req_key or "",
                g.requirement_title or "",
                g.screen_title or "",
                g.screen_route or "",
                g.severity,
                g.status,
                g.code,
                g.reason,
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="gap-report.csv"'},
    )


@router.get("/projects/{project_id}/mapping/summary", response_model=CoverageSummary)
def mapping_summary(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    snap = _current_snapshot(session, project_id, None)
    th = _threshold(project)
    reqs = session.exec(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.review_status != "rejected",
        )
    ).all()
    maps = (
        session.exec(
            select(RequirementMapping).where(RequirementMapping.snapshot_id == snap)
        ).all()
        if snap
        else []
    )
    matched = {m.requirement_id for m in maps if effective(m, th)}
    screens = len(load_cards(session, snap)) if snap else 0
    gaps = (
        session.exec(select(Gap).where(Gap.snapshot_id == snap)).all() if snap else []
    )
    last = session.exec(
        select(Run)
        .where(Run.project_id == project_id, Run.kind == "map")
        .order_by(col(Run.created_at).desc())
    ).first()
    snapshot = session.get(AppSnapshot, snap) if snap else None
    partial = bool(
        snapshot
        and any(
            v == "budget_exceeded" for v in (snapshot.stats.get("roles") or {}).values()
        )
    )
    return CoverageSummary(
        snapshot_id=snap,
        crawl_partial=partial,
        threshold=th,
        requirements_total=len(reqs),
        requirements_matched=sum(1 for r in reqs if r.id in matched),
        requirement_gaps=sum(1 for g in gaps if g.direction == "requirement_unmatched"),
        screens_total=screens,
        feature_gaps=sum(1 for g in gaps if g.direction == "feature_unmentioned"),
        mappings=len(maps),
        confirmed=sum(1 for m in maps if m.review_status == "confirmed"),
        rejected=sum(1 for m in maps if m.review_status == "rejected"),
        last_run_id=last.id if last else None,
    )
