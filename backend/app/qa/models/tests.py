"""Generated test cases."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field
from app.qa.models.requirements import vector_column


class TestCase(SQLModel, table=True):
    __tablename__ = "test_case"
    __test__ = False  # not a pytest class
    __table_args__ = (
        UniqueConstraint("project_id", "tc_key"),
        Index("ix_test_case_project_status", "project_id", "status"),
        Index("ix_test_case_project_signature", "project_id", "signature"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    tc_key: str = Field(max_length=40)  # TC-0001
    requirement_id: uuid.UUID = Field(
        foreign_key="requirement.id", nullable=False, ondelete="CASCADE", index=True
    )
    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", nullable=False, ondelete="CASCADE"
    )
    state_id: uuid.UUID | None = Field(
        default=None, foreign_key="ui_state.id", ondelete="SET NULL"
    )
    role_id: uuid.UUID | None = Field(
        default=None, foreign_key="app_role.id", ondelete="SET NULL"
    )
    role_name: str | None = Field(default=None, max_length=100)
    module: str | None = Field(default=None, max_length=200)
    title: str = Field(max_length=500)
    preconditions: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    steps: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )  # structured Step[]
    rendered_steps: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    test_data: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    expected_result: str = ""
    priority: str = Field(default="P3", max_length=10)
    # positive|negative|boundary|field_validation|role_access|e2e|business_rule
    test_type: str = Field(default="positive", max_length=30)
    # rule | llm | manual
    origin: str = Field(default="rule", max_length=10)
    # draft | approved | rejected | edited | needs_regen
    status: str = Field(default="draft", max_length=20)
    # How each part is known: {"basis": observed|inferred|requirement|not_observed, "rule", "field", ...}
    evidence: dict[str, Any] = jsonb(dict)
    signature: str = Field(default="", max_length=64)
    embedding: Any = Field(default=None, sa_column=vector_column())
    version: int = 1
    run_id: uuid.UUID | None = Field(
        default=None, foreign_key="run.id", ondelete="SET NULL"
    )
    created_at: datetime = ts_field()
    updated_at: datetime = ts_field()


class TestCaseVersion(SQLModel, table=True):
    """One saved state of a test case: generated, edited by a person, or regenerated."""

    __tablename__ = "test_case_version"
    __test__ = False
    __table_args__ = (UniqueConstraint("test_case_id", "version"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    test_case_id: uuid.UUID = Field(
        foreign_key="test_case.id", nullable=False, ondelete="CASCADE", index=True
    )
    version: int
    # The editable fields as they were: title, preconditions, steps, test_data, expected_result,
    # priority, test_type, role_name.
    content: dict[str, Any] = jsonb(dict)
    # generated | edited | regenerated | status
    change_type: str = Field(default="generated", max_length=20)
    change_note: str = ""
    changed_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = ts_field()
