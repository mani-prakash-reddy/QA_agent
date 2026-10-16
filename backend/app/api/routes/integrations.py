"""Test-management integrations: the user's own credentials, one per platform (bring your own key), used
for every project of that user, and pushing approved test cases to the connected tool."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.projects import get_owned_project
from app.core.config import settings
from app.models import Message
from app.qa.integrations.base import IntegrationError
from app.qa.integrations.push import PLATFORMS, connector_for, push_cases
from app.qa.integrations.zephyr import DEFAULT_BASE
from app.qa.models import TestCase, TestCaseExternal, UserIntegration
from app.qa.security.crypto import CryptoError, encrypt_json
from app.qa.security.urls import TargetUrlError, validate_target_url

router = APIRouter(tags=["integrations"])
Platform = Literal["jira", "testrail", "zephyr"]

HELP = {
    "jira": "Jira Cloud: site URL (https://your-site.atlassian.net), your e-mail and an API token (id.atlassian.com > Security > API tokens).",
    "testrail": "TestRail: URL (https://your-company.testrail.io), your login e-mail and an API key (My Settings > API Keys; the API must be enabled by an administrator).",
    "zephyr": "Zephyr Scale Cloud: an API access token (Jira profile > Zephyr Scale API Access Tokens). The URL is only needed for the EU or another region.",
}


class IntegrationPublic(BaseModel):
    platform: Platform
    connected: bool
    base_url: str
    username: str
    settings: dict[str, Any]
    last_ok_at: datetime | None
    last_error: str | None
    help: str


class IntegrationSet(BaseModel):
    base_url: str = Field(default="", max_length=500)
    username: str = Field(default="", max_length=300)
    secret: SecretStr | None = None  # omit to keep the saved one
    settings: dict[str, Any] = Field(default_factory=dict)


class Option(BaseModel):
    id: str
    name: str


class PushRequest(BaseModel):
    destination: dict[str, Any]
    dry_run: bool = True
    include_drafts: bool = False
    case_ids: list[uuid.UUID] | None = None


class PushedCase(BaseModel):
    tc_key: str
    title: str
    action: str
    external_id: str | None
    url: str | None
    error: str | None


class PushResult(BaseModel):
    platform: str
    destination: str
    dry_run: bool
    counts: dict[str, int]
    cases: list[PushedCase]


class ExternalRef(BaseModel):
    tc_key: str
    platform: str
    external_id: str
    url: str
    pushed_at: datetime


def public(platform: str, row: UserIntegration | None) -> IntegrationPublic:
    return IntegrationPublic(
        platform=platform,  # type: ignore[arg-type]
        connected=bool(row and row.ciphertext),
        base_url=row.base_url if row else "",
        username=row.username if row else "",
        settings=row.settings if row else {},
        last_ok_at=row.last_ok_at if row else None,
        last_error=row.last_error if row else None,
        help=HELP[platform],
    )


def mine(session: Session, user_id: uuid.UUID, platform: str) -> UserIntegration | None:
    return session.exec(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id, UserIntegration.platform == platform
        )
    ).first()


def need(session: Session, user_id: uuid.UUID, platform: str) -> UserIntegration:
    row = mine(session, user_id, platform)
    if row is None or not row.ciphertext:
        raise HTTPException(
            409, f"{platform} is not connected: add it under Integrations"
        )
    return row


def checked_url(platform: str, url: str) -> str:
    url = url.strip().rstrip("/") or (DEFAULT_BASE if platform == "zephyr" else "")
    if not url:
        raise HTTPException(422, "A site URL is required")
    try:
        validate_target_url(url)
    except TargetUrlError as exc:
        raise HTTPException(422, str(exc)) from exc
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
    # plain http only for hosts that cannot be on the public internet: allowed hosts and bare names
    # (a compose service such as demo-testrail-mock only resolves on the private network)
    private = host in settings.allowed_target_hosts or "." not in host
    if not url.startswith("https://") and not private:
        raise HTTPException(422, "Use an https:// URL (credentials are sent to it)")
    return url


@router.get("/integrations/", response_model=list[IntegrationPublic])
def list_integrations(session: SessionDep, user: CurrentUser) -> Any:
    return [public(p, mine(session, user.id, p)) for p in PLATFORMS]


@router.put("/integrations/{platform}", response_model=IntegrationPublic)
def save_integration(
    session: SessionDep, user: CurrentUser, platform: Platform, body: IntegrationSet
) -> Any:
    row = mine(session, user.id, platform)
    if body.secret is None and (row is None or not row.ciphertext):
        raise HTTPException(422, "The API token / key is required")
    if platform in {"jira", "testrail"} and not body.username.strip():
        raise HTTPException(422, "The account e-mail / login is required")
    url = checked_url(platform, body.base_url)
    if row is None:
        row = UserIntegration(user_id=user.id, platform=platform)
    row.base_url, row.username, row.settings = url, body.username.strip(), body.settings
    if body.secret is not None:
        try:
            row.ciphertext = encrypt_json({"secret": body.secret.get_secret_value()})
        except CryptoError as exc:
            raise HTTPException(500, str(exc)) from exc
    row.last_ok_at, row.last_error = None, None
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return public(platform, row)


@router.delete("/integrations/{platform}", response_model=Message)
def remove_integration(
    session: SessionDep, user: CurrentUser, platform: Platform
) -> Any:
    row = mine(session, user.id, platform)
    if row:
        session.delete(row)
        session.commit()
    return Message(message="removed")


@router.post("/integrations/{platform}/test", response_model=Message)
def test_integration(session: SessionDep, user: CurrentUser, platform: Platform) -> Any:
    row = need(session, user.id, platform)
    conn = connector_for(row)
    try:
        said = conn.test()
        row.last_ok_at, row.last_error = datetime.now(UTC), None
    except IntegrationError as exc:
        row.last_error = str(exc)[:500]
        session.add(row)
        session.commit()
        raise HTTPException(422, str(exc)) from exc
    finally:
        conn.close()
    session.add(row)
    session.commit()
    return Message(message=said)


@router.get("/integrations/{platform}/options", response_model=list[Option])
def integration_options(
    session: SessionDep,
    user: CurrentUser,
    platform: Platform,
    kind: str,
    parent: str = "",
) -> Any:
    conn = connector_for(need(session, user.id, platform))
    try:
        return conn.options(kind, parent)
    except IntegrationError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        conn.close()


@router.post(
    "/projects/{project_id}/integrations/{platform}/push", response_model=PushResult
)
def push(
    session: SessionDep,
    user: CurrentUser,
    project_id: uuid.UUID,
    platform: Platform,
    body: PushRequest,
) -> Any:
    get_owned_project(session, user, project_id)
    row = need(session, user.id, platform)
    try:
        return push_cases(
            session,
            row,
            project_id,
            body.destination,
            dry_run=body.dry_run,
            include_drafts=body.include_drafts,
            case_ids=body.case_ids,
        )
    except KeyError as exc:
        raise HTTPException(422, f"Missing destination field: {exc}") from exc
    except IntegrationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/projects/{project_id}/integrations/{platform}/pushed",
    response_model=list[ExternalRef],
)
def pushed(
    session: SessionDep, user: CurrentUser, project_id: uuid.UUID, platform: Platform
) -> Any:
    get_owned_project(session, user, project_id)
    rows = session.exec(
        select(TestCaseExternal, TestCase.tc_key)
        .join(TestCase, col(TestCase.id) == col(TestCaseExternal.test_case_id))
        .where(TestCase.project_id == project_id, TestCaseExternal.platform == platform)
    ).all()
    return [
        ExternalRef(
            tc_key=k,
            platform=e.platform,
            external_id=e.external_id,
            url=e.external_url,
            pushed_at=e.pushed_at,
        )
        for e, k in rows
    ]
