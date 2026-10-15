"""BRD documents, their chunks, and the requirements extracted from them."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field

EMBEDDING_DIM = (
    1024  # fixed by the schema: pgvector HNSW cannot index more than 2000 dims
)


def vector_column() -> Any:
    return Column(Vector(EMBEDDING_DIM), nullable=True)


class BrdDocument(SQLModel, table=True):
    __tablename__ = "brd_document"
    __table_args__ = (UniqueConstraint("project_id", "sha256"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    filename: str = Field(max_length=500)
    mime: str = Field(max_length=100)
    # upload | confluence_export
    source_type: str = Field(default="upload", max_length=30)
    artifact_path: str = Field(max_length=1000)  # relative to ARTIFACT_DIR
    sha256: str = Field(max_length=64)
    # uploaded | parsing | extracting | ready | failed
    status: str = Field(default="uploaded", max_length=20)
    page_count: int | None = None
    chunk_count: int = 0
    requirement_count: int = 0
    error: str | None = None
    run_id: uuid.UUID | None = Field(
        default=None, foreign_key="run.id", ondelete="SET NULL"
    )
    created_at: datetime = ts_field()


class BrdChunk(SQLModel, table=True):
    __tablename__ = "brd_chunk"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="brd_document.id", nullable=False, ondelete="CASCADE", index=True
    )
    ordinal: int
    section_path: str = Field(default="", max_length=1000)  # "3.2 User Management"
    page_from: int | None = None
    page_to: int | None = None
    text: str
    # text | table | list
    kind: str = Field(default="text", max_length=20)


class Requirement(SQLModel, table=True):
    __tablename__ = "requirement"
    __table_args__ = (UniqueConstraint("project_id", "req_key"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    document_id: uuid.UUID | None = Field(
        default=None, foreign_key="brd_document.id", ondelete="SET NULL"
    )
    req_key: str = Field(max_length=100)  # BRD-native id, or REQ-001
    # brd | generated | manual
    key_origin: str = Field(default="generated", max_length=20)
    # functional | business_rule | validation | acceptance_criterion | nfr
    type: str = Field(default="functional", max_length=30)
    title: str = Field(max_length=500)
    description: str
    actors: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    module: str | None = Field(default=None, max_length=200)
    acceptance_criteria: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    # {"chunk_id", "section", "page", "quote"}: how a reviewer verifies the requirement
    source_anchor: dict[str, Any] = jsonb(dict)
    ambiguity_flag: bool = False
    # [{"source": "heuristic|llm", "reason": "..."}]
    ambiguity_reasons: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    ambiguity_resolved: bool = False  # a human accepted the wording as it stands
    # extracted | approved | rejected | edited
    review_status: str = Field(default="extracted", max_length=20, index=True)
    version: int = 1
    embedding: Any = Field(default=None, sa_column=vector_column())
    embedding_model: str | None = Field(default=None, max_length=200)
    created_at: datetime = ts_field()
    updated_at: datetime = ts_field()
