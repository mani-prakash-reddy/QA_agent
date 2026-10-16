"""Generated test cases: start generation, browse, and basic status changes (full review is M8)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session, col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.api.routes.runs import enqueue_run
from app.qa.generation.versions import version_content
from app.qa.models import Requirement, Run, TestCase, TestCaseVersion
from app.qa.runs.kinds.generate import latest_mapped_snapshot
from app.qa.schemas import (
    GenerateRequest,
    RegenerateRequest,
    RestoreRequest,
    RunPublic,
    TestBulk,
    TestCaseEdit,
    TestCasePublic,
    TestCasesPage,
    TestCaseStatus,
    TestsSummary,
    TestVersionPublic,
)

router = APIRouter(tags=["tests"])


def to_public(t: TestCase, r: Requirement) -> TestCasePublic:
    return TestCasePublic(
        id=t.id,
        tc_key=t.tc_key,
        requirement_id=t.requirement_id,
        req_key=r.req_key,
        requirement_title=r.title,
        also_covers=list(t.evidence.get("also_covers", [])),
        state_id=t.state_id,
        snapshot_id=t.snapshot_id,
        module=t.module,
        title=t.title,
        preconditions=t.preconditions,
        steps=t.steps,
        rendered_steps=t.rendered_steps,
        test_data=t.test_data,
        expected_result=t.expected_result,
        priority=t.priority,
        test_type=t.test_type,
        role_name=t.role_name,
        origin=t.origin,
        status=t.status,
        evidence=t.evidence,
        version=t.version,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _owned(
    session: Session, user: Any, test_id: uuid.UUID
) -> tuple[TestCase, Requirement]:
    t = session.get(TestCase, test_id)
    if not t:
        raise HTTPException(status_code=404, detail="Test case not found")
    get_owned_project(session, user, t.project_id)
    r = session.get(Requirement, t.requirement_id)
    assert r is not None
    return t, r


@router.post("/projects/{project_id}/tests/generate", response_model=RunPublic)
def start_generation(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: GenerateRequest | None = None,
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    snapshot_id = latest_mapped_snapshot(project_id)
    if snapshot_id is None:
        raise HTTPException(
            status_code=409,
            detail="Requirements are not mapped to screens yet. Run mapping first.",
        )
    body = body or GenerateRequest()
    run = Run(
        project_id=project_id,
        kind="generate",
        snapshot_id=snapshot_id,
        created_by=current_user.id,
        config_snapshot={
            "snapshot_id": str(snapshot_id),
            "requirement_ids": [str(x) for x in body.requirement_ids or []],
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


@router.get("/projects/{project_id}/tests", response_model=TestCasesPage)
def list_tests(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    status: str | None = None,
    test_type: str | None = None,
    requirement_id: uuid.UUID | None = None,
    role: str | None = None,
    origin: str | None = None,
    q: str | None = None,
    skip: int = 0,
    limit: int = Query(default=100, le=1000),
) -> Any:
    get_owned_project(session, current_user, project_id)
    where: list[Any] = [TestCase.project_id == project_id]
    if status:
        where.append(TestCase.status == status)
    if test_type:
        where.append(TestCase.test_type == test_type)
    if requirement_id:
        where.append(TestCase.requirement_id == requirement_id)
    if role:
        where.append(TestCase.role_name == role)
    if origin:
        where.append(TestCase.origin == origin)
    if q:
        like = f"%{q}%"
        where.append(col(TestCase.title).ilike(like) | col(TestCase.tc_key).ilike(like))
    total = session.exec(select(func.count()).select_from(TestCase).where(*where)).one()
    rows = session.exec(
        select(TestCase, Requirement)
        .join(Requirement, col(Requirement.id) == col(TestCase.requirement_id))
        .where(*where)
        .order_by(col(TestCase.tc_key))
        .offset(skip)
        .limit(limit)
    ).all()
    return TestCasesPage(data=[to_public(t, r) for t, r in rows], count=total)


@router.get("/projects/{project_id}/tests/summary", response_model=TestsSummary)
def tests_summary(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    rows = session.exec(
        select(
            TestCase.status,
            TestCase.test_type,
            TestCase.origin,
            TestCase.requirement_id,
        ).where(TestCase.project_id == project_id)
    ).all()
    by: dict[str, dict[str, int]] = {"s": {}, "t": {}, "o": {}}
    for st, ty, og, _ in rows:
        by["s"][st] = by["s"].get(st, 0) + 1
        by["t"][ty] = by["t"].get(ty, 0) + 1
        by["o"][og] = by["o"].get(og, 0) + 1
    total_reqs = session.exec(
        select(func.count())
        .select_from(Requirement)
        .where(
            Requirement.project_id == project_id,
            Requirement.review_status != "rejected",
        )
    ).one()
    last = session.exec(
        select(Run)
        .where(Run.project_id == project_id, Run.kind == "generate")
        .order_by(col(Run.created_at).desc())
    ).first()
    return TestsSummary(
        total=len(rows),
        by_status=by["s"],
        by_type=by["t"],
        by_origin=by["o"],
        requirements_total=total_reqs,
        requirements_covered=len({r[3] for r in rows if r[0] != "rejected"}),
        last_run_id=last.id if last else None,
    )


@router.get("/tests/{test_id}", response_model=TestCasePublic)
def read_test(
    session: SessionDep, current_user: CurrentUser, test_id: uuid.UUID
) -> Any:
    t, r = _owned(session, current_user, test_id)
    return to_public(t, r)


@router.patch("/tests/{test_id}/status", response_model=TestCasePublic)
def set_status(
    session: SessionDep,
    current_user: CurrentUser,
    test_id: uuid.UUID,
    body: TestCaseStatus,
) -> Any:
    t, r = _owned(session, current_user, test_id)
    t.status = body.status
    t.updated_at = datetime.now(UTC)
    session.add(t)
    session.commit()
    session.refresh(t)
    return to_public(t, r)


def _add_version(
    session: Session,
    t: TestCase,
    change_type: str,
    note: str,
    user_id: uuid.UUID | None,
) -> None:
    session.add(
        TestCaseVersion(
            test_case_id=t.id,
            version=t.version,
            content=version_content(t),
            change_type=change_type,
            change_note=note,
            changed_by=user_id,
        )
    )


def _ensure_baseline(session: Session, t: TestCase) -> None:
    """Cases generated before versioning existed have no version row: save the current text first,
    so the edit can always be undone."""
    has = session.exec(
        select(TestCaseVersion.id).where(TestCaseVersion.test_case_id == t.id)
    ).first()
    if has is None:
        _add_version(session, t, "generated", "Baseline before the first edit", None)
        session.flush()


@router.patch("/tests/{test_id}", response_model=TestCasePublic)
def edit_test(
    session: SessionDep,
    current_user: CurrentUser,
    test_id: uuid.UUID,
    body: TestCaseEdit,
) -> Any:
    """A person rewrites a case. The structured steps are kept as generated (they no longer match the
    text), so the case is marked hand-edited and must be approved again."""
    t, r = _owned(session, current_user, test_id)
    changes = body.model_dump(exclude_unset=True, exclude={"note"})
    changes = {
        k: v for k, v in changes.items() if getattr(t, k) != v
    }  # only what differs
    if not changes:
        return to_public(t, r)
    _ensure_baseline(session, t)
    for key, value in changes.items():
        setattr(t, key, value)
    if "rendered_steps" in changes:
        t.evidence = {**t.evidence, "hand_edited": True}
    t.status = "edited"
    t.version += 1
    t.updated_at = datetime.now(UTC)
    session.add(t)
    _add_version(session, t, "edited", body.note, current_user.id)
    session.commit()
    session.refresh(t)
    return to_public(t, r)


@router.get("/tests/{test_id}/versions", response_model=list[TestVersionPublic])
def test_versions(
    session: SessionDep, current_user: CurrentUser, test_id: uuid.UUID
) -> Any:
    _owned(session, current_user, test_id)
    return session.exec(
        select(TestCaseVersion)
        .where(TestCaseVersion.test_case_id == test_id)
        .order_by(col(TestCaseVersion.version).desc())
    ).all()


@router.post("/tests/{test_id}/restore", response_model=TestCasePublic)
def restore_test(
    session: SessionDep,
    current_user: CurrentUser,
    test_id: uuid.UUID,
    body: RestoreRequest,
) -> Any:
    t, r = _owned(session, current_user, test_id)
    v = session.exec(
        select(TestCaseVersion).where(
            TestCaseVersion.test_case_id == test_id,
            TestCaseVersion.version == body.version,
        )
    ).first()
    if v is None:
        raise HTTPException(status_code=404, detail="Version not found")
    _ensure_baseline(session, t)
    for key, value in v.content.items():
        setattr(t, key, value)
    t.status = "edited"
    t.version += 1
    t.updated_at = datetime.now(UTC)
    session.add(t)
    _add_version(
        session, t, "edited", f"Restored version {body.version}", current_user.id
    )
    session.commit()
    session.refresh(t)
    return to_public(t, r)


@router.post("/projects/{project_id}/tests/bulk")
def bulk_status(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: TestBulk,
) -> Any:
    get_owned_project(session, current_user, project_id)
    new = {"approve": "approved", "reject": "rejected", "draft": "draft"}[body.action]
    rows = session.exec(
        select(TestCase).where(
            TestCase.project_id == project_id, col(TestCase.id).in_(body.ids)
        )
    ).all()
    for t in rows:
        t.status = new
        t.updated_at = datetime.now(UTC)
        session.add(t)
    session.commit()
    return {"updated": len(rows)}


@router.post("/tests/{test_id}/regenerate", response_model=RunPublic)
def regenerate(
    session: SessionDep,
    current_user: CurrentUser,
    test_id: uuid.UUID,
    body: RegenerateRequest,
) -> Any:
    """Write this requirement's unreviewed cases again, with the reviewer's comment. Approved and
    hand-edited cases are kept."""
    t, r = _owned(session, current_user, test_id)
    project = get_owned_project(session, current_user, t.project_id)
    run = Run(
        project_id=t.project_id,
        kind="regenerate",
        snapshot_id=t.snapshot_id,
        created_by=current_user.id,
        config_snapshot={
            "snapshot_id": str(t.snapshot_id),
            "requirement_ids": [str(r.id)],
            "feedback": body.feedback.strip(),
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
