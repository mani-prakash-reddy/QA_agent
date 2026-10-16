"""Reference documents besides the BRD: a business glossary and an API specification (Swagger / OpenAPI)."""

import hashlib
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.models import Message
from app.qa.models import ProjectReference, Requirement
from app.qa.reference.api_spec import parse_api, related
from app.qa.reference.glossary import ReferenceError, parse_glossary

router = APIRouter(tags=["references"])
MAX_BYTES = 5 * 1024 * 1024


class ReferencePublic(BaseModel):
    id: uuid.UUID
    kind: str
    filename: str
    created_at: datetime
    summary: str


class ApiOperation(BaseModel):
    method: str
    path: str
    summary: str
    fields: list[str]
    related_requirements: list[str]


class ReferenceDetail(ReferencePublic):
    terms: list[dict[str, Any]] = []
    operations: list[ApiOperation] = []
    unreferenced_operations: int = 0


def summarise(r: ProjectReference) -> str:
    if r.kind == "glossary":
        return f"{len(r.data.get('terms', []))} terms"
    return f"{r.data.get('title', '')} {r.data.get('version', '')}: {len(r.data.get('operations', []))} operations".strip()


def public(r: ProjectReference) -> ReferencePublic:
    return ReferencePublic(
        id=r.id,
        kind=r.kind,
        filename=r.filename,
        created_at=r.created_at,
        summary=summarise(r),
    )


@router.post("/projects/{project_id}/references", response_model=ReferencePublic)
def add_reference(
    session: SessionDep,
    user: CurrentUser,
    project_id: uuid.UUID,
    kind: Annotated[Literal["glossary", "api"], Form()],
    file: Annotated[
        UploadFile,
        File(
            description="Glossary (CSV, JSON, Markdown, text) or OpenAPI/Swagger (JSON, YAML)"
        ),
    ],
) -> Any:
    get_owned_project(session, user, project_id)
    data = file.file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(422, "The file is empty or larger than 5 MB")
    name = (file.filename or kind)[:300]
    try:
        parsed = (
            {"terms": parse_glossary(data, name)}
            if kind == "glossary"
            else parse_api(data, name)
        )
    except ReferenceError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()
    if session.exec(
        select(ProjectReference).where(
            ProjectReference.project_id == project_id, ProjectReference.sha256 == digest
        )
    ).first():
        raise HTTPException(409, "This file was already added")
    # one glossary and one API specification per project: a new one replaces the old
    for old in session.exec(
        select(ProjectReference).where(
            ProjectReference.project_id == project_id, ProjectReference.kind == kind
        )
    ):
        session.delete(old)
    row = ProjectReference(
        project_id=project_id, kind=kind, filename=name, sha256=digest, data=parsed
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return public(row)


@router.get("/projects/{project_id}/references", response_model=list[ReferencePublic])
def list_references(
    session: SessionDep, user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, user, project_id)
    rows = session.exec(
        select(ProjectReference)
        .where(ProjectReference.project_id == project_id)
        .order_by(col(ProjectReference.created_at))
    ).all()
    return [public(r) for r in rows]


@router.get("/references/{reference_id}", response_model=ReferenceDetail)
def read_reference(
    session: SessionDep, user: CurrentUser, reference_id: uuid.UUID
) -> Any:
    r = session.get(ProjectReference, reference_id)
    if not r:
        raise HTTPException(404, "Reference not found")
    get_owned_project(session, user, r.project_id)
    out = ReferenceDetail(**public(r).model_dump())
    if r.kind == "glossary":
        out.terms = r.data.get("terms", [])
        return out
    reqs = session.exec(
        select(Requirement).where(
            Requirement.project_id == r.project_id,
            Requirement.review_status != "rejected",
        )
    ).all()
    ops = r.data.get("operations", [])
    by_op: dict[str, list[str]] = {}
    for q in reqs:
        for op in related(ops, f"{q.title} {q.description}", 6):
            by_op.setdefault(f"{op['method']} {op['path']}", []).append(q.req_key)
    for op in ops:
        keys = by_op.get(f"{op['method']} {op['path']}", [])
        out.operations.append(
            ApiOperation(
                method=op["method"],
                path=op["path"],
                summary=op["summary"],
                fields=[f["name"] for f in op["body"]],
                related_requirements=keys[:6],
            )
        )
    out.unreferenced_operations = sum(
        1 for o in out.operations if not o.related_requirements
    )
    return out


@router.delete("/references/{reference_id}", response_model=Message)
def delete_reference(
    session: SessionDep, user: CurrentUser, reference_id: uuid.UUID
) -> Any:
    r = session.get(ProjectReference, reference_id)
    if not r:
        raise HTTPException(404, "Reference not found")
    get_owned_project(session, user, r.project_id)
    session.delete(r)
    session.commit()
    return Message(message="removed")
