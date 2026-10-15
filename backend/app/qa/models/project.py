import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def ts_field() -> Any:
    return Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


def jsonb(default: Any = None) -> Any:
    return Field(
        default_factory=(lambda: default() if callable(default) else default)
        if default is not None
        else None,
        sa_column=Column(JSONB, nullable=default is None),
    )


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    target_base_url: str = Field(max_length=2048)
    environment_ack: bool = False
    # confidential -> strict provider routing, free-tier models refused
    confidential: bool = False
    crawl_config: dict[str, Any] = jsonb(dict)
    generation_config: dict[str, Any] = jsonb(dict)
    budget_caps: dict[str, Any] = jsonb(dict)
    created_at: datetime = ts_field()
    updated_at: datetime = ts_field()


class AppRole(SQLModel, table=True):
    """A user role *of the application under test* (not a QA Agent user)."""

    __tablename__ = "app_role"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    # URLs/selector hints/MFA type. Never contains secrets.
    login_recipe: dict[str, Any] = jsonb(dict)


class RoleCredential(SQLModel, table=True):
    """Write-only via the API; decrypted only inside the worker's auth module."""

    __tablename__ = "role_credential"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    role_id: uuid.UUID = Field(
        foreign_key="app_role.id", nullable=False, ondelete="CASCADE", unique=True
    )
    ciphertext: str
    key_version: int = 1
    updated_at: datetime = ts_field()
