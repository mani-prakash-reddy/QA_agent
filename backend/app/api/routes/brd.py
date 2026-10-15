"""BRD documents: upload, list, source viewer, delete, re-extract."""

import hashlib
import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from anyio import from_thread
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlmodel import Session, col, delete, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.api.routes.runs import enqueue_run
from app.core.config import settings
from app.models import Message
from app.qa.brd.parse import MAX_UPLOAD_BYTES, BrdParseError, detect_format
from app.qa.models import BrdChunk, BrdDocument, Requirement, Run
from app.qa.schemas import BrdPublic, BrdUploadResult, ChunkPublic

router = APIRouter(tags=["brd"])

MIME = {
    "pdf": "application/pdf",
    "text": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
EXT = {"pdf": ".pdf", "docx": ".docx", "text": ".txt"}


def safe_filename(name: str) -> str:
    """Display name only: it never takes part in a filesystem path."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "document")[:200]


def get_owned_document(
    session: Session, user: Any, document_id: uuid.UUID
) -> BrdDocument:
    doc = session.get(BrdDocument, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    get_owned_project(session, user, doc.project_id)
    return doc


def _delete_unreviewed(session: Session, document_id: uuid.UUID) -> None:
    """Requirements nobody has reviewed yet belong to the document; reviewed ones are kept."""
    session.execute(
        delete(Requirement).where(
            col(Requirement.document_id) == document_id,
            col(Requirement.review_status) == "extracted",
        )
    )


def _start_ingest(
    session: Session, doc: BrdDocument, user_id: uuid.UUID, confidential: bool
) -> Run:
    run = Run(
        project_id=doc.project_id,
        kind="ingest",
        created_by=user_id,
        config_snapshot={"document_id": str(doc.id), "confidential": confidential},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    run.arq_job_id = from_thread.run(enqueue_run, run.id)
    doc.run_id, doc.status, doc.error = run.id, "uploaded", None
    session.add_all([run, doc])
    session.commit()
    session.refresh(doc)
    return run


@router.post("/projects/{project_id}/brd", response_model=BrdUploadResult)
def upload_brd(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown or text BRD")],
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 20 MB")
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    name = safe_filename(file.filename or "document")
    try:
        fmt = detect_format(data, name)
    except BrdParseError as exc:
        raise HTTPException(status_code=415, detail=str(exc))

    digest = hashlib.sha256(data).hexdigest()
    twin = session.exec(
        select(BrdDocument).where(
            BrdDocument.project_id == project_id, BrdDocument.sha256 == digest
        )
    ).first()
    if twin:
        raise HTTPException(
            status_code=409,
            detail=f"This file was already uploaded as '{twin.filename}'",
        )

    doc_id = uuid.uuid4()
    rel = f"{project_id}/brd/{doc_id}/original{EXT[fmt]}"
    path = Path(settings.ARTIFACT_DIR).resolve() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    doc = BrdDocument(
        id=doc_id,
        project_id=project_id,
        filename=name,
        mime=MIME[fmt],
        artifact_path=rel,
        sha256=digest,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    run = _start_ingest(session, doc, current_user.id, project.confidential)
    return BrdUploadResult(
        document=BrdPublic.model_validate(doc, from_attributes=True), run_id=run.id
    )


@router.get("/projects/{project_id}/brd", response_model=list[BrdPublic])
def list_brd(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    return session.exec(
        select(BrdDocument)
        .where(BrdDocument.project_id == project_id)
        .order_by(col(BrdDocument.created_at).desc())
    ).all()


@router.get("/brd/{document_id}", response_model=BrdPublic)
def read_brd(
    session: SessionDep, current_user: CurrentUser, document_id: uuid.UUID
) -> Any:
    return get_owned_document(session, current_user, document_id)


@router.get("/brd/{document_id}/chunks", response_model=list[ChunkPublic])
def list_chunks(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    skip: int = 0,
    limit: int = Query(default=200, le=1000),
) -> Any:
    get_owned_document(session, current_user, document_id)
    return session.exec(
        select(BrdChunk)
        .where(BrdChunk.document_id == document_id)
        .order_by(col(BrdChunk.ordinal))
        .offset(skip)
        .limit(limit)
    ).all()


@router.post("/brd/{document_id}/reextract", response_model=BrdUploadResult)
def reextract(
    session: SessionDep, current_user: CurrentUser, document_id: uuid.UUID
) -> Any:
    """Run extraction again. Requirements a person already reviewed are kept; the rest are replaced."""
    doc = get_owned_document(session, current_user, document_id)
    if doc.status in {"uploaded", "parsing", "extracting"}:
        raise HTTPException(
            status_code=409, detail="This document is still being processed"
        )
    project = get_owned_project(session, current_user, doc.project_id)
    _delete_unreviewed(session, doc.id)
    session.commit()
    run = _start_ingest(session, doc, current_user.id, project.confidential)
    return BrdUploadResult(
        document=BrdPublic.model_validate(doc, from_attributes=True), run_id=run.id
    )


@router.delete("/brd/{document_id}", response_model=Message)
def delete_brd(
    session: SessionDep, current_user: CurrentUser, document_id: uuid.UUID
) -> Any:
    """Removes the file, its chunks and its unreviewed requirements. Reviewed requirements are kept."""
    doc = get_owned_document(session, current_user, document_id)
    _delete_unreviewed(session, doc.id)
    folder = (Path(settings.ARTIFACT_DIR).resolve() / doc.artifact_path).parent
    session.delete(doc)
    session.commit()
    for f in folder.glob("*") if folder.is_dir() else []:
        f.unlink(missing_ok=True)
    return Message(message="Document deleted")
