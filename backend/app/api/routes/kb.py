"""Read API for the application knowledge model (snapshots, states, matrix, crawl log, screenshots)."""

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.core.config import settings
from app.models import User
from app.qa.models import (
    AppRole,
    AppSnapshot,
    CrawlEvent,
    FormField,
    RoleStateAccess,
    Run,
    Transition,
    UiElement,
    UiForm,
    UiState,
)
from app.qa.schemas import (
    CrawlEventPublic,
    CrawlLogPublic,
    ElementPublic,
    FieldPublic,
    FormPublic,
    MatrixPublic,
    MatrixRow,
    SnapshotPublic,
    StateDetail,
    StatePublic,
    TransitionPublic,
)

router = APIRouter(tags=["knowledge"])


def _snapshot(session: Session, user: User, snapshot_id: uuid.UUID) -> AppSnapshot:
    snap = session.get(AppSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    get_owned_project(session, user, snap.project_id)
    return snap


def _state(
    session: Session, user: User, state_id: uuid.UUID
) -> tuple[UiState, AppSnapshot]:
    st = session.get(UiState, state_id)
    if not st:
        raise HTTPException(status_code=404, detail="State not found")
    return st, _snapshot(session, user, st.snapshot_id)


def _role_names(session: Session, snapshot_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = session.exec(
        select(AppRole.id, AppRole.name)
        .join(RoleStateAccess, col(RoleStateAccess.role_id) == col(AppRole.id))
        .where(RoleStateAccess.snapshot_id == snapshot_id)
        .distinct()
    ).all()
    return dict(rows)


@router.get("/projects/{project_id}/snapshots", response_model=list[SnapshotPublic])
def list_snapshots(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    return session.exec(
        select(AppSnapshot)
        .where(AppSnapshot.project_id == project_id)
        .order_by(col(AppSnapshot.created_at).desc())
    ).all()


@router.get("/snapshots/{snapshot_id}/states", response_model=list[StatePublic])
def list_states(
    session: SessionDep,
    current_user: CurrentUser,
    snapshot_id: uuid.UUID,
    kind: str | None = None,
    module: str | None = None,
    role: str | None = None,
    q: str | None = None,
) -> Any:
    _snapshot(session, current_user, snapshot_id)
    stmt = select(UiState).where(UiState.snapshot_id == snapshot_id)
    if kind:
        stmt = stmt.where(UiState.kind == kind)
    if module:
        stmt = stmt.where(UiState.module == module)
    if q:
        stmt = stmt.where(col(UiState.title).ilike(f"%{q}%"))
    states = session.exec(
        stmt.order_by(col(UiState.depth), col(UiState.module), col(UiState.title))
    ).all()

    names = _role_names(session, snapshot_id)
    access: dict[uuid.UUID, list[str]] = {}
    for a in session.exec(
        select(RoleStateAccess).where(RoleStateAccess.snapshot_id == snapshot_id)
    ).all():
        access.setdefault(a.state_id, []).append(names.get(a.role_id, "?"))
    ids = [s.id for s in states]
    el_counts = (
        dict(
            session.exec(
                select(UiElement.state_id, func.count())
                .where(col(UiElement.state_id).in_(ids))
                .group_by(col(UiElement.state_id))
            ).all()
        )
        if ids
        else {}
    )
    form_counts = (
        dict(
            session.exec(
                select(UiForm.state_id, func.count())
                .where(col(UiForm.state_id).in_(ids))
                .group_by(col(UiForm.state_id))
            ).all()
        )
        if ids
        else {}
    )
    out = [
        StatePublic(
            id=s.id,
            kind=s.kind,
            title=s.title,
            module=s.module,
            route_template=s.route_template,
            sample_url=s.sample_url,
            depth=s.depth,
            parent_state_id=s.parent_state_id,
            nav_path=s.nav_path,
            has_screenshot=bool(s.screenshot_path),
            roles=sorted(access.get(s.id, [])),
            n_elements=el_counts.get(s.id, 0),
            n_forms=form_counts.get(s.id, 0),
            discovery_source=s.discovery_source,
        )
        for s in states
    ]
    if role:
        out = [s for s in out if role in s.roles]
    return out


@router.get("/states/{state_id}", response_model=StateDetail)
def read_state(
    session: SessionDep, current_user: CurrentUser, state_id: uuid.UUID
) -> Any:
    st, snap = _state(session, current_user, state_id)
    names = _role_names(session, snap.id)
    roles = sorted(
        names.get(a.role_id, "?")
        for a in session.exec(
            select(RoleStateAccess).where(
                RoleStateAccess.snapshot_id == snap.id,
                RoleStateAccess.state_id == st.id,
            )
        ).all()
    )
    elements = session.exec(
        select(UiElement)
        .where(UiElement.state_id == st.id)
        .order_by(col(UiElement.region), col(UiElement.kind))
    ).all()
    forms: list[FormPublic] = []
    for f in session.exec(select(UiForm).where(UiForm.state_id == st.id)).all():
        fields = session.exec(
            select(FormField)
            .where(FormField.form_id == f.id)
            .order_by(col(FormField.ordinal))
        ).all()
        forms.append(
            FormPublic(
                id=f.id,
                name=f.name,
                submit_label=f.submit_label,
                fields=[
                    FieldPublic.model_validate(x, from_attributes=True) for x in fields
                ],
            )
        )
    titles = {
        s.id: s.title
        for s in session.exec(
            select(UiState).where(UiState.snapshot_id == snap.id)
        ).all()
    }

    def _t(t: Transition) -> TransitionPublic:
        return TransitionPublic(
            id=t.id,
            from_state_id=t.from_state_id,
            from_title=titles.get(t.from_state_id, ""),
            to_state_id=t.to_state_id,
            to_title=titles.get(t.to_state_id, ""),
            via_label=t.via_label,
            kind=t.kind,
        )

    outgoing = session.exec(
        select(Transition).where(Transition.from_state_id == st.id)
    ).all()
    incoming = session.exec(
        select(Transition).where(Transition.to_state_id == st.id)
    ).all()
    return StateDetail(
        id=st.id,
        snapshot_id=snap.id,
        kind=st.kind,
        title=st.title,
        heading=st.heading,
        module=st.module,
        route_template=st.route_template,
        sample_url=st.sample_url,
        depth=st.depth,
        nav_path=st.nav_path,
        parent_state_id=st.parent_state_id,
        has_screenshot=bool(st.screenshot_path),
        roles=roles,
        elements=[
            ElementPublic.model_validate(e, from_attributes=True) for e in elements
        ],
        forms=forms,
        outgoing=[_t(t) for t in outgoing],
        incoming=[_t(t) for t in incoming],
    )


@router.get("/states/{state_id}/screenshot")
def state_screenshot(
    session: SessionDep, current_user: CurrentUser, state_id: uuid.UUID
) -> FileResponse:
    st, _ = _state(session, current_user, state_id)
    if not st.screenshot_path:
        raise HTTPException(status_code=404, detail="No screenshot for this state")
    base = Path(settings.ARTIFACT_DIR).resolve()
    path = (base / st.screenshot_path).resolve()
    if (
        base not in path.parents or not path.is_file()
    ):  # no path traversal, no missing file
        raise HTTPException(status_code=404, detail="Screenshot file not found")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/snapshots/{snapshot_id}/matrix", response_model=MatrixPublic)
def role_matrix(
    session: SessionDep, current_user: CurrentUser, snapshot_id: uuid.UUID
) -> Any:
    """Which role can reach which screen (from what each role's crawl actually observed)."""
    snap = _snapshot(session, current_user, snapshot_id)
    names = _role_names(session, snapshot_id)
    roles = sorted(names.values())
    access: dict[uuid.UUID, set[str]] = {}
    denied: dict[uuid.UUID, dict[str, str]] = {}
    for a in session.exec(
        select(RoleStateAccess).where(RoleStateAccess.snapshot_id == snapshot_id)
    ).all():
        if a.access == "visible":
            access.setdefault(a.state_id, set()).add(names.get(a.role_id, "?"))
        else:
            denied.setdefault(a.state_id, {})[names.get(a.role_id, "?")] = a.access
    states = session.exec(
        select(UiState)
        .where(UiState.snapshot_id == snap.id)
        .order_by(col(UiState.module), col(UiState.depth), col(UiState.title))
    ).all()
    return MatrixPublic(
        roles=roles,
        rows=[
            MatrixRow(
                state_id=s.id,
                title=s.title,
                kind=s.kind,
                module=s.module,
                route=s.route_template,
                access={r: r in access.get(s.id, set()) for r in roles},
                denied=denied.get(s.id, {}),
            )
            for s in states
        ],
    )


@router.get("/snapshots/{snapshot_id}/export.json")
def export_snapshot(
    session: SessionDep, current_user: CurrentUser, snapshot_id: uuid.UUID
) -> dict[str, Any]:
    """The whole knowledge model as reusable JSON (no re-crawl needed to use it elsewhere)."""
    snap = _snapshot(session, current_user, snapshot_id)
    names = _role_names(session, snapshot_id)
    states = session.exec(select(UiState).where(UiState.snapshot_id == snap.id)).all()
    ids = [s.id for s in states]
    elements = (
        session.exec(select(UiElement).where(col(UiElement.state_id).in_(ids))).all()
        if ids
        else []
    )
    forms = (
        session.exec(select(UiForm).where(col(UiForm.state_id).in_(ids))).all()
        if ids
        else []
    )
    fids = [f.id for f in forms]
    fields = (
        session.exec(select(FormField).where(col(FormField.form_id).in_(fids))).all()
        if fids
        else []
    )
    trans = session.exec(
        select(Transition).where(Transition.snapshot_id == snap.id)
    ).all()
    access = session.exec(
        select(RoleStateAccess).where(RoleStateAccess.snapshot_id == snap.id)
    ).all()

    def d(row: Any, drop: set[str] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = row.model_dump(mode="json")
        for k in drop or set():
            out.pop(k, None)
        return out

    result: dict[str, Any] = {
        "snapshot": d(snap),
        "roles": sorted(names.values()),
        "states": [d(s, {"screenshot_path"}) for s in states],
        "elements": [d(e) for e in elements],
        "forms": [d(f) for f in forms],
        "fields": [d(f) for f in fields],
        "transitions": [d(t) for t in trans],
        "role_access": [
            {
                "role": names.get(a.role_id, "?"),
                "state_id": str(a.state_id),
                "access": a.access,
            }
            for a in access
        ],
    }
    return result


@router.get("/runs/{run_id}/crawl-log", response_model=CrawlLogPublic)
def crawl_log(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
    status: str | None = None,
    reason: str | None = None,
    skip: int = 0,
    limit: int = Query(default=200, le=2000),
) -> Any:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    get_owned_project(session, current_user, run.project_id)
    base = select(CrawlEvent).where(CrawlEvent.run_id == run_id)
    summary_rows = session.exec(
        select(CrawlEvent.status, CrawlEvent.reason, func.count())
        .where(CrawlEvent.run_id == run_id)
        .group_by(col(CrawlEvent.status), col(CrawlEvent.reason))
    ).all()
    stmt = base
    if status:
        stmt = stmt.where(CrawlEvent.status == status)
    if reason:
        stmt = stmt.where(CrawlEvent.reason == reason)
    rows = session.exec(
        stmt.order_by(col(CrawlEvent.id)).offset(skip).limit(limit)
    ).all()
    role_names = {
        r.id: r.name
        for r in session.exec(
            select(AppRole).where(AppRole.project_id == run.project_id)
        ).all()
    }
    return CrawlLogPublic(
        summary=[{"status": s, "reason": r, "count": n} for s, r, n in summary_rows],
        events=[
            CrawlEventPublic(
                id=e.id or 0,
                ts=e.ts,
                role=role_names.get(e.role_id) if e.role_id else None,
                url=e.url,
                action=e.action,
                element_label=e.element_label,
                status=e.status,
                reason=e.reason,
                source=e.source,
            )
            for e in rows
        ],
    )


@router.post("/snapshots/{snapshot_id}/graph", response_model=dict[str, int])
def rebuild_graph(
    session: SessionDep, current_user: CurrentUser, snapshot_id: uuid.UUID
) -> Any:
    """(Re)project the snapshot into the Apache AGE graph."""
    _snapshot(session, current_user, snapshot_id)
    from app.qa.knowledge.graph import project_snapshot

    return project_snapshot(snapshot_id)


class PathStep(BaseModel):
    screen: str
    via: str


@router.get("/snapshots/{snapshot_id}/path", response_model=list[PathStep])
def screen_path(
    session: SessionDep,
    current_user: CurrentUser,
    snapshot_id: uuid.UUID,
    from_state: uuid.UUID,
    to_state: uuid.UUID,
) -> Any:
    """Fewest clicks from one screen to another, answered by the graph."""
    _snapshot(session, current_user, snapshot_id)
    from app.qa.knowledge.graph import shortest_path

    path = shortest_path(snapshot_id, from_state, to_state)
    if path is None:
        raise HTTPException(
            status_code=404, detail="No recorded path between those screens"
        )
    return path
