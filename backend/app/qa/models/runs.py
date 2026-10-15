import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field


class Run(SQLModel, table=True):
    __tablename__ = "run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    # echo | crawl | map | generate | regenerate | full
    kind: str = Field(max_length=50)
    # queued | running | paused | waiting_human | succeeded | failed | stopped
    status: str = Field(default="queued", max_length=30, index=True)
    snapshot_id: uuid.UUID | None = None
    config_snapshot: dict[str, Any] = jsonb(dict)
    arq_job_id: str | None = None
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    error: str | None = None
    summary: dict[str, Any] = jsonb(dict)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = ts_field()


class RunStage(SQLModel, table=True):
    __tablename__ = "run_stage"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(
        foreign_key="run.id", nullable=False, ondelete="CASCADE", index=True
    )
    stage: str = Field(max_length=30)
    status: str = Field(max_length=30)
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    duration_ms: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(
        foreign_key="run.id", nullable=False, ondelete="CASCADE", index=True
    )
    parent_agent_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_run.id", ondelete="SET NULL"
    )
    kind: str = Field(max_length=30)
    spec: dict[str, Any] = jsonb(dict)
    # running | succeeded | failed | stopped | budget_exceeded
    status: str = Field(default="running", max_length=30)
    model: str | None = None
    result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    started_at: datetime = ts_field()
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AgentEvent(SQLModel, table=True):
    """Persisted copy of the live event stream (replay, audit)."""

    __tablename__ = "agent_event"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_event_run_seq"),)

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    run_id: uuid.UUID = Field(foreign_key="run.id", nullable=False, ondelete="CASCADE")
    agent_id: str | None = Field(default=None, max_length=64)
    seq: int = Field(sa_column=Column(BigInteger, nullable=False))
    ts: datetime = Field(sa_type=DateTime(timezone=True))
    type: str = Field(max_length=40)
    payload: dict[str, Any] = jsonb(dict)


class CrawlEvent(SQLModel, table=True):
    """What was crawled / skipped / blocked, and why (assignment: logging requirement)."""

    __tablename__ = "crawl_event"
    __table_args__ = (
        Index("ix_crawl_event_run_status_reason", "run_id", "status", "reason"),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    run_id: uuid.UUID = Field(foreign_key="run.id", nullable=False, ondelete="CASCADE")
    agent_id: str | None = Field(default=None, max_length=64)
    role_id: uuid.UUID | None = None
    ts: datetime = ts_field()
    url: str | None = None
    action: str | None = None
    element_label: str | None = None
    # crawled | skipped | blocked | error
    status: str = Field(max_length=20)
    reason: str | None = Field(default=None, max_length=60)
    # nav | link | click | network | brd | sitemap | seed
    source: str | None = Field(default=None, max_length=20)
    state_id: uuid.UUID | None = None
