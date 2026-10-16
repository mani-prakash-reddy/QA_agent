"""Test-management integrations: one saved connection per user and platform, and what was pushed where."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.qa.models.project import jsonb, ts_field


class UserIntegration(SQLModel, table=True):
    """The user's own credentials for Jira, TestRail or Zephyr (bring your own key). One per platform,
    shared by all of the user's projects. The secret is encrypted and never returned by the API."""

    __tablename__ = "user_integration"
    __table_args__ = (UniqueConstraint("user_id", "platform"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    platform: str = Field(max_length=20)  # jira | testrail | zephyr
    base_url: str = Field(default="", max_length=500)
    username: str = Field(
        default="", max_length=300
    )  # e-mail / login; empty for token-only platforms
    ciphertext: str = ""  # encrypted {"secret": ...}
    settings: dict[str, Any] = jsonb(dict)
    last_ok_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=500)
    created_at: datetime = ts_field()
    updated_at: datetime = ts_field()


class TestCaseExternal(SQLModel, table=True):
    """A test case that exists in an external tool, so a later push updates it instead of duplicating it."""

    __tablename__ = "test_case_external"
    __test__ = False
    __table_args__ = (UniqueConstraint("test_case_id", "platform", "destination"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    test_case_id: uuid.UUID = Field(
        foreign_key="test_case.id", nullable=False, ondelete="CASCADE", index=True
    )
    platform: str = Field(max_length=20)
    destination: str = Field(
        max_length=300
    )  # e.g. "PROJ/Task", "12/3/45", "KEY/folder"
    external_id: str = Field(max_length=100)
    external_url: str = Field(default="", max_length=1000)
    content_hash: str = Field(default="", max_length=64)
    pushed_at: datetime = ts_field()
