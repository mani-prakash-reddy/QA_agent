"""Dashboard: one call that summarises every project's progress and the latest runs."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.qa.models import (
    AppRole,
    AppSnapshot,
    Project,
    Requirement,
    RequirementMapping,
    Run,
    TestCase,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class ProjectProgress(BaseModel):
    id: uuid.UUID
    name: str
    target_base_url: str
    roles: int
    requirements: int
    screens: int
    mapped_requirements: int
    test_cases: int
    approved_cases: int
    covered_requirements: int
    next_step: str  # roles | requirements | crawl | mapping | tests | review | done
    last_run_kind: str | None
    last_run_status: str | None
    last_run_at: datetime | None


class RecentRun(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    kind: str
    status: str
    created_at: datetime
    seconds: float | None
    partial: bool


class DashboardPublic(BaseModel):
    projects: list[ProjectProgress]
    recent_runs: list[RecentRun]
    running: int
    totals: dict[str, int]


def _counts(session: SessionDep, stmt: Any) -> dict[uuid.UUID, int]:
    return dict(session.exec(stmt).all())


@router.get("/", response_model=DashboardPublic)
def dashboard(session: SessionDep, current_user: CurrentUser) -> Any:
    pstmt = select(Project).order_by(col(Project.created_at).desc())
    if not current_user.is_superuser:
        pstmt = pstmt.where(Project.owner_id == current_user.id)
    projects = list(session.exec(pstmt).all())
    ids = [p.id for p in projects]
    if not ids:
        return DashboardPublic(projects=[], recent_runs=[], running=0, totals={})

    roles = _counts(
        session,
        select(col(AppRole.project_id), func.count())
        .where(col(AppRole.project_id).in_(ids))
        .group_by(col(AppRole.project_id)),
    )
    reqs = _counts(
        session,
        select(col(Requirement.project_id), func.count())
        .where(
            col(Requirement.project_id).in_(ids),
            Requirement.review_status != "rejected",
        )
        .group_by(col(Requirement.project_id)),
    )
    mapped = _counts(
        session,
        select(
            col(RequirementMapping.project_id),
            func.count(func.distinct(RequirementMapping.requirement_id)),
        )
        .where(
            col(RequirementMapping.project_id).in_(ids),
            RequirementMapping.review_status != "rejected",
        )
        .group_by(col(RequirementMapping.project_id)),
    )
    cases = _counts(
        session,
        select(col(TestCase.project_id), func.count())
        .where(col(TestCase.project_id).in_(ids), TestCase.status != "rejected")
        .group_by(col(TestCase.project_id)),
    )
    approved = _counts(
        session,
        select(col(TestCase.project_id), func.count())
        .where(col(TestCase.project_id).in_(ids), TestCase.status == "approved")
        .group_by(col(TestCase.project_id)),
    )
    covered = _counts(
        session,
        select(TestCase.project_id, func.count(func.distinct(TestCase.requirement_id)))
        .where(col(TestCase.project_id).in_(ids), TestCase.status != "rejected")
        .group_by(col(TestCase.project_id)),
    )
    screens: dict[uuid.UUID, int] = {}
    for snap in session.exec(
        select(AppSnapshot)
        .where(col(AppSnapshot.project_id).in_(ids), AppSnapshot.status == "ready")
        .order_by(col(AppSnapshot.created_at))
    ):
        screens[snap.project_id] = int(snap.stats.get("states") or 0)  # newest wins

    runs = list(
        session.exec(
            select(Run)
            .where(col(Run.project_id).in_(ids))
            .order_by(col(Run.created_at).desc())
            .limit(60)
        ).all()
    )
    last: dict[uuid.UUID, Run] = {}
    for r in runs:
        last.setdefault(r.project_id, r)
    names = {p.id: p.name for p in projects}

    out: list[ProjectProgress] = []
    for p in projects:
        n_roles, n_req = roles.get(p.id, 0), reqs.get(p.id, 0)
        n_scr, n_map = screens.get(p.id, 0), mapped.get(p.id, 0)
        n_case, n_ok = cases.get(p.id, 0), approved.get(p.id, 0)
        step = (
            "roles"
            if not n_roles
            else "requirements"
            if not n_req
            else "crawl"
            if not n_scr
            else "mapping"
            if not n_map
            else "tests"
            if not n_case
            else "review"
            if n_ok < n_case
            else "done"
        )
        lr = last.get(p.id)
        out.append(
            ProjectProgress(
                id=p.id,
                name=p.name,
                target_base_url=p.target_base_url,
                roles=n_roles,
                requirements=n_req,
                screens=n_scr,
                mapped_requirements=n_map,
                test_cases=n_case,
                approved_cases=n_ok,
                covered_requirements=covered.get(p.id, 0),
                next_step=step,
                last_run_kind=lr.kind if lr else None,
                last_run_status=lr.status if lr else None,
                last_run_at=lr.created_at if lr else None,
            )
        )
    recent = [
        RecentRun(
            id=r.id,
            project_id=r.project_id,
            project_name=names[r.project_id],
            kind=r.kind,
            status=r.status,
            created_at=r.created_at,
            seconds=(r.finished_at - r.started_at).total_seconds()
            if r.started_at and r.finished_at
            else None,
            partial=bool(r.summary.get("partial")),
        )
        for r in runs[:8]
    ]
    return DashboardPublic(
        projects=out,
        recent_runs=recent,
        running=sum(
            1
            for r in runs
            if r.status in {"queued", "running", "paused", "waiting_human"}
        ),
        totals={
            "projects": len(projects),
            "requirements": sum(reqs.values()),
            "test_cases": sum(cases.values()),
            "approved": sum(approved.values()),
        },
    )
