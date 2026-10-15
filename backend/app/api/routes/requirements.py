"""Requirements: list, review (edit / approve / reject / accept wording), manual add, bulk actions."""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, not_, or_
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.models import User
from app.qa.brd.heuristics import ambiguity_reasons, is_blocking
from app.qa.models import BrdChunk, Requirement
from app.qa.schemas import (
    BulkAction,
    BulkResult,
    RequirementCreate,
    RequirementDetail,
    RequirementPublic,
    RequirementsPage,
    RequirementsSummary,
    RequirementUpdate,
)

router = APIRouter(tags=["requirements"])


def to_public(r: Requirement) -> RequirementPublic:
    return RequirementPublic(
        id=r.id,
        project_id=r.project_id,
        document_id=r.document_id,
        req_key=r.req_key,
        key_origin=r.key_origin,
        type=r.type,
        title=r.title,
        description=r.description,
        actors=r.actors,
        module=r.module,
        acceptance_criteria=r.acceptance_criteria,
        source_anchor=r.source_anchor,
        ambiguity_flag=r.ambiguity_flag,
        ambiguity_reasons=r.ambiguity_reasons,
        ambiguity_resolved=r.ambiguity_resolved,
        needs_decision=r.ambiguity_flag and not r.ambiguity_resolved,
        review_status=r.review_status,
        version=r.version,
        has_embedding=r.embedding is not None,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def get_owned_requirement(
    session: Session, user: User, requirement_id: uuid.UUID
) -> Requirement:
    r = session.get(Requirement, requirement_id)
    if not r:
        raise HTTPException(status_code=404, detail="Requirement not found")
    get_owned_project(session, user, r.project_id)
    return r


def _touch(r: Requirement) -> None:
    r.updated_at = datetime.now(UTC)


def _recheck_ambiguity(r: Requirement) -> None:
    """After a human edit, the old model findings may be stale: recompute from the new wording."""
    reasons = ambiguity_reasons(
        f"{r.title}. {r.description}", r.type, bool(r.acceptance_criteria)
    )
    r.ambiguity_reasons = [x.as_dict() for x in reasons]
    r.ambiguity_flag = is_blocking(reasons)
    r.ambiguity_resolved = False


def _approval_blocker(r: Requirement) -> str | None:
    if r.ambiguity_flag and not r.ambiguity_resolved:
        return (
            "flagged as ambiguous: rewrite it, or accept its wording, before approving"
        )
    return None


@router.get("/projects/{project_id}/requirements", response_model=RequirementsPage)
def list_requirements(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    status: str | None = None,
    type: str | None = None,
    needs_decision: bool | None = None,
    document_id: uuid.UUID | None = None,
    q: str | None = None,
    skip: int = 0,
    limit: int = Query(default=100, le=500),
) -> Any:
    get_owned_project(session, current_user, project_id)
    where: list[Any] = [Requirement.project_id == project_id]
    if status:
        where.append(Requirement.review_status == status)
    if type:
        where.append(Requirement.type == type)
    if document_id:
        where.append(Requirement.document_id == document_id)
    flagged_open = and_(
        col(Requirement.ambiguity_flag).is_(True),
        col(Requirement.ambiguity_resolved).is_(False),
    )
    if needs_decision is True:
        where.append(flagged_open)
    elif needs_decision is False:
        where.append(not_(flagged_open))
    if q:
        like = f"%{q}%"
        where.append(
            or_(
                col(Requirement.title).ilike(like),
                col(Requirement.description).ilike(like),
                col(Requirement.req_key).ilike(like),
            )
        )
    total = session.exec(
        select(func.count()).select_from(Requirement).where(*where)
    ).one()
    rows = session.exec(
        select(Requirement)
        .where(*where)
        .order_by(col(Requirement.created_at), col(Requirement.req_key))
        .offset(skip)
        .limit(limit)
    ).all()
    return RequirementsPage(data=[to_public(r) for r in rows], count=total)


@router.get(
    "/projects/{project_id}/requirements/summary", response_model=RequirementsSummary
)
def requirements_summary(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    rows = session.exec(
        select(Requirement).where(Requirement.project_id == project_id)
    ).all()
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_status[r.review_status] = by_status.get(r.review_status, 0) + 1
        by_type[r.type] = by_type.get(r.type, 0) + 1
    return RequirementsSummary(
        total=len(rows),
        by_status=by_status,
        by_type=by_type,
        needs_decision=sum(
            1 for r in rows if r.ambiguity_flag and not r.ambiguity_resolved
        ),
        approved=by_status.get("approved", 0),
    )


@router.get("/requirements/{requirement_id}", response_model=RequirementDetail)
def read_requirement(
    session: SessionDep, current_user: CurrentUser, requirement_id: uuid.UUID
) -> Any:
    r = get_owned_requirement(session, current_user, requirement_id)
    chunk = None
    try:
        chunk = session.get(BrdChunk, uuid.UUID(str(r.source_anchor.get("chunk_id"))))
    except ValueError, TypeError:
        chunk = None
    return RequirementDetail(
        **to_public(r).model_dump(),
        source_text=chunk.text if chunk else None,
        source_page_to=chunk.page_to if chunk else None,
    )


@router.post("/projects/{project_id}/requirements", response_model=RequirementPublic)
def create_requirement(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: RequirementCreate,
) -> Any:
    """A requirement the BRD does not state in words (a verbal agreement, a ticket). Marked as manual."""
    get_owned_project(session, current_user, project_id)
    existing = set(
        session.exec(
            select(Requirement.req_key).where(Requirement.project_id == project_id)
        ).all()
    )
    if body.req_key:
        key = re.sub(r"\s+", "-", body.req_key.strip()).upper()
        if key in existing:
            raise HTTPException(
                status_code=409, detail=f"Requirement '{key}' already exists"
            )
    else:
        n = 1 + max(
            (int(m.group(1)) for k in existing if (m := re.fullmatch(r"REQ-(\d+)", k))),
            default=0,
        )
        key = f"REQ-{n:03d}"
    r = Requirement(
        project_id=project_id,
        req_key=key,
        key_origin="manual",
        type=body.type,
        title=body.title.strip(),
        description=body.description.strip(),
        module=body.module,
        actors=body.actors,
        acceptance_criteria=body.acceptance_criteria,
        source_anchor={"quote": "", "section": "manual entry"},
        review_status="edited",
    )
    _recheck_ambiguity(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.patch("/requirements/{requirement_id}", response_model=RequirementPublic)
def update_requirement(
    session: SessionDep,
    current_user: CurrentUser,
    requirement_id: uuid.UUID,
    body: RequirementUpdate,
) -> Any:
    r = get_owned_requirement(session, current_user, requirement_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return to_public(r)
    for key, value in changes.items():
        setattr(r, key, value)
    r.review_status = "edited"  # an edited requirement must be approved again
    r.version += 1
    # The old vector describes the old wording: drop it (re-embedded when mapping needs it).
    # flag_modified is required: a loaded pgvector value is a NumPy array, and SQLAlchemy's change
    # detection does not notice `array -> None`, so no UPDATE would be sent.
    r.embedding = None
    r.embedding_model = None
    flag_modified(r, "embedding")
    _recheck_ambiguity(r)
    _touch(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.post("/requirements/{requirement_id}/approve", response_model=RequirementPublic)
def approve_requirement(
    session: SessionDep, current_user: CurrentUser, requirement_id: uuid.UUID
) -> Any:
    r = get_owned_requirement(session, current_user, requirement_id)
    if (why := _approval_blocker(r)) is not None:
        raise HTTPException(status_code=409, detail=why)
    r.review_status = "approved"
    _touch(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.post("/requirements/{requirement_id}/reject", response_model=RequirementPublic)
def reject_requirement(
    session: SessionDep, current_user: CurrentUser, requirement_id: uuid.UUID
) -> Any:
    r = get_owned_requirement(session, current_user, requirement_id)
    r.review_status = "rejected"
    _touch(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.post(
    "/requirements/{requirement_id}/accept-wording", response_model=RequirementPublic
)
def accept_wording(
    session: SessionDep, current_user: CurrentUser, requirement_id: uuid.UUID
) -> Any:
    """A person decides the flagged wording is acceptable as it stands (it can then be approved)."""
    r = get_owned_requirement(session, current_user, requirement_id)
    if not r.ambiguity_flag:
        raise HTTPException(status_code=409, detail="This requirement is not flagged")
    r.ambiguity_resolved = True
    _touch(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.post("/requirements/{requirement_id}/reset", response_model=RequirementPublic)
def reset_requirement(
    session: SessionDep, current_user: CurrentUser, requirement_id: uuid.UUID
) -> Any:
    r = get_owned_requirement(session, current_user, requirement_id)
    r.review_status = "extracted"
    _touch(r)
    session.add(r)
    session.commit()
    session.refresh(r)
    return to_public(r)


@router.post("/projects/{project_id}/requirements/bulk", response_model=BulkResult)
def bulk_review(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: BulkAction,
) -> Any:
    get_owned_project(session, current_user, project_id)
    rows = session.exec(
        select(Requirement).where(
            Requirement.project_id == project_id, col(Requirement.id).in_(body.ids)
        )
    ).all()
    updated, skipped = 0, []
    for r in rows:
        if body.action == "approve" and (why := _approval_blocker(r)) is not None:
            skipped.append({"req_key": r.req_key, "reason": why})
            continue
        r.review_status = "approved" if body.action == "approve" else "rejected"
        _touch(r)
        session.add(r)
        updated += 1
    session.commit()
    return BulkResult(updated=updated, skipped=skipped)
