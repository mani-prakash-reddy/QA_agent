"""Requirement <-> screen mappings and the two-way gap report."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field


class RequirementMapping(SQLModel, table=True):
    __tablename__ = "requirement_mapping"
    __table_args__ = (UniqueConstraint("requirement_id", "snapshot_id", "state_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    requirement_id: uuid.UUID = Field(
        foreign_key="requirement.id", nullable=False, ondelete="CASCADE", index=True
    )
    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", nullable=False, ondelete="CASCADE", index=True
    )
    state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", nullable=False, ondelete="CASCADE", index=True
    )
    confidence: float = 0.0
    rationale: str = ""
    # Labels the judge relied on, each verified to exist on the screen: [{"kind", "label"}]
    citations: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    retrieval_score: float = 0.0
    # judge | manual
    method: str = Field(default="judge", max_length=20)
    # proposed | confirmed | rejected
    review_status: str = Field(default="proposed", max_length=20)
    run_id: uuid.UUID | None = Field(
        default=None, foreign_key="run.id", ondelete="SET NULL"
    )
    created_at: datetime = ts_field()
    updated_at: datetime = ts_field()


class Gap(SQLModel, table=True):
    __tablename__ = "gap"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", nullable=False, ondelete="CASCADE", index=True
    )
    run_id: uuid.UUID | None = Field(
        default=None, foreign_key="run.id", ondelete="SET NULL"
    )
    # requirement_unmatched | feature_unmentioned
    direction: str = Field(max_length=30)
    requirement_id: uuid.UUID | None = Field(
        default=None, foreign_key="requirement.id", ondelete="CASCADE", index=True
    )
    state_id: uuid.UUID | None = Field(
        default=None, foreign_key="ui_state.id", ondelete="CASCADE"
    )
    # Short code for filtering: no_candidate | judged_absent | not_ui_observable | undocumented_screen
    code: str = Field(default="", max_length=40)
    reason: str = ""
    # Evidence shown to the reviewer: nearest screens considered, route group, etc.
    evidence: dict[str, Any] = jsonb(dict)
    severity: str = Field(default="medium", max_length=10)
    # open | accepted | resolved
    status: str = Field(default="open", max_length=20)
    created_at: datetime = ts_field()
