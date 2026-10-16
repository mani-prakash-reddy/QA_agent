"""Application knowledge model: what the crawler observed. Versioned per snapshot."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field
from app.qa.models.requirements import vector_column


class AppSnapshot(SQLModel, table=True):
    __tablename__ = "app_snapshot"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    run_id: uuid.UUID | None = Field(
        default=None, foreign_key="run.id", ondelete="SET NULL"
    )
    # building | ready | failed
    status: str = Field(default="building", max_length=20)
    roles_covered: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    stats: dict[str, Any] = jsonb(dict)
    created_at: datetime = ts_field()
    stale_after: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class UiState(SQLModel, table=True):
    """A screen, overlay, tab or wizard step (not a URL: SPAs reuse URLs)."""

    __tablename__ = "ui_state"
    __table_args__ = (UniqueConstraint("snapshot_id", "fingerprint"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", nullable=False, ondelete="CASCADE", index=True
    )
    fingerprint: str = Field(max_length=64)
    # page | modal | menu | listbox | tab
    kind: str = Field(max_length=20)
    parent_state_id: uuid.UUID | None = Field(
        default=None, foreign_key="ui_state.id", ondelete="SET NULL"
    )
    route_template: str | None = Field(default=None, max_length=1024)
    sample_url: str | None = Field(default=None, max_length=2048)
    title: str = Field(default="", max_length=500)
    heading: str | None = Field(default=None, max_length=500)
    module: str | None = Field(default=None, max_length=200)
    nav_path: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    summary: str | None = None  # LLM-written purpose (M4)
    # Masked static content seen on the screen: {"headers": [[column, ...]], "text": [caption, ...]}.
    # Never table data; e-mails/tokens masked, digits dropped (see browser.extract.mask_content).
    content: dict[str, Any] = jsonb(dict)
    screenshot_path: str | None = None
    discovered_by_agent: str | None = Field(default=None, max_length=64)
    discovery_source: str = Field(default="click", max_length=20)
    depth: int = 0
    # Mapping retrieval vector over the screen's text card (computed lazily by the map run).
    embedding: Any = Field(default=None, sa_column=vector_column())
    embedding_model: str | None = Field(default=None, max_length=200)


class UiElement(SQLModel, table=True):
    __tablename__ = "ui_element"
    __table_args__ = (UniqueConstraint("state_id", "key"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", nullable=False, ondelete="CASCADE", index=True
    )
    # Stable identity within a state: "<kind>|<label>|<nth>". Live handles ("e12") are per snapshot.
    key: str = Field(max_length=600)
    kind: str = Field(max_length=20)
    role_aria: str | None = Field(default=None, max_length=50)
    label: str = Field(default="", max_length=500)
    name_attr: str | None = Field(default=None, max_length=200)
    region: str = Field(default="main", max_length=20)
    in_row: bool = False
    locators: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    attrs: dict[str, Any] = jsonb(dict)
    is_submit: bool = False
    is_destructive: bool = False
    destructive_reason: str | None = Field(default=None, max_length=200)


class UiForm(SQLModel, table=True):
    __tablename__ = "form"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(default="", max_length=500)
    submit_label: str | None = Field(default=None, max_length=200)
    submit_element_id: uuid.UUID | None = Field(
        default=None, foreign_key="ui_element.id", ondelete="SET NULL"
    )
    purpose: str | None = None  # LLM-written (M4)


class FormField(SQLModel, table=True):
    __tablename__ = "form_field"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    form_id: uuid.UUID = Field(
        foreign_key="form.id", nullable=False, ondelete="CASCADE", index=True
    )
    element_id: uuid.UUID | None = Field(
        default=None, foreign_key="ui_element.id", ondelete="SET NULL"
    )
    ordinal: int = 0
    label: str = Field(default="", max_length=500)
    input_type: str = Field(default="text", max_length=30)
    required: bool = False
    min_length: int | None = None
    max_length: int | None = None
    min_value: str | None = Field(default=None, max_length=50)
    max_value: str | None = Field(default=None, max_length=50)
    pattern: str | None = Field(default=None, max_length=500)
    step: str | None = Field(default=None, max_length=50)
    readonly: bool = False
    placeholder: str | None = Field(default=None, max_length=500)
    # {"values": [...], "truncated": bool} for select/combobox
    options: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    # [{"trigger": "empty|too_long|invalid_format", "message": "..."}]
    validation_messages: list[Any] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    default_present: bool = False  # a value was pre-filled (never stored)


class Transition(SQLModel, table=True):
    __tablename__ = "transition"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", nullable=False, ondelete="CASCADE", index=True
    )
    from_state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", nullable=False, ondelete="CASCADE"
    )
    to_state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", nullable=False, ondelete="CASCADE"
    )
    via_label: str = Field(default="", max_length=500)
    via_key: str | None = Field(default=None, max_length=600)
    # click | navigate
    action: str = Field(default="click", max_length=20)
    # navigation | overlay | tab
    kind: str = Field(default="navigation", max_length=20)
    observed_by_role_id: uuid.UUID | None = None


class RoleStateAccess(SQLModel, table=True):
    __tablename__ = "role_state_access"

    snapshot_id: uuid.UUID = Field(
        foreign_key="app_snapshot.id", primary_key=True, ondelete="CASCADE"
    )
    role_id: uuid.UUID = Field(
        foreign_key="app_role.id", primary_key=True, ondelete="CASCADE"
    )
    state_id: uuid.UUID = Field(
        foreign_key="ui_state.id", primary_key=True, ondelete="CASCADE"
    )
    # visible | forbidden | not_found | redirected
    access: str = Field(default="visible", max_length=20)
    evidence: str | None = None
