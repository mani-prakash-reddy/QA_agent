import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.qa.models import RoleCredential
from tests.utils.user import authentication_token_from_email

API = settings.API_V1_STR
PROJECT = {
    "name": "Demo",
    "target_base_url": "http://localhost:5173",
    "environment_ack": True,
}


def make_project(
    client: TestClient, headers: dict[str, str], **over: Any
) -> dict[str, Any]:
    r = client.post(f"{API}/projects/", headers=headers, json={**PROJECT, **over})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_project_requires_environment_ack(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{API}/projects/",
        headers=superuser_token_headers,
        json={**PROJECT, "environment_ack": False},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "ftp://x.test",
        "http://10.0.0.5/app",
        "http://169.254.169.254/",
        "http://u:p@host.test/",
        "not a url",
    ],
)
def test_project_rejects_unsafe_target_urls(
    client: TestClient, superuser_token_headers: dict[str, str], url: str
) -> None:
    r = client.post(
        f"{API}/projects/",
        headers=superuser_token_headers,
        json={**PROJECT, "target_base_url": url},
    )
    assert r.status_code == 422


def test_project_crud_and_defaults(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    p = make_project(client, superuser_token_headers)
    assert p["crawl_config"]["allow_destructive"] is False  # safe by default
    assert p["crawl_config"]["max_depth"] == 4
    r = client.patch(
        f"{API}/projects/{p['id']}",
        headers=superuser_token_headers,
        json={"confidential": True},
    )
    assert r.json()["confidential"] is True
    assert any(
        x["id"] == p["id"]
        for x in client.get(f"{API}/projects/", headers=superuser_token_headers).json()
    )
    assert (
        client.delete(
            f"{API}/projects/{p['id']}", headers=superuser_token_headers
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"{API}/projects/{p['id']}", headers=superuser_token_headers
        ).status_code
        == 404
    )


def test_projects_are_private_to_their_owner(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    p = make_project(client, normal_user_token_headers)
    stranger = authentication_token_from_email(
        client=client, email="stranger@example.com", db=db
    )
    pid = p["id"]
    assert (
        client.get(
            f"{API}/projects/{pid}", headers=normal_user_token_headers
        ).status_code
        == 200
    )
    # a different normal user is denied on every project-scoped endpoint
    assert client.get(f"{API}/projects/{pid}", headers=stranger).status_code == 403
    assert (
        client.patch(
            f"{API}/projects/{pid}", headers=stranger, json={"name": "x"}
        ).status_code
        == 403
    )
    assert client.delete(f"{API}/projects/{pid}", headers=stranger).status_code == 403
    assert (
        client.get(f"{API}/projects/{pid}/roles", headers=stranger).status_code == 403
    )
    assert (
        client.post(
            f"{API}/projects/{pid}/roles", headers=stranger, json={"name": "R"}
        ).status_code
        == 403
    )
    assert client.get(f"{API}/projects/{pid}/runs", headers=stranger).status_code == 403
    assert (
        client.get(f"{API}/projects/", headers=stranger).json() == []
    )  # not even listed
    assert (
        client.get(
            f"{API}/projects/{uuid.uuid4()}", headers=normal_user_token_headers
        ).status_code
        == 404
    )
    # the superuser can see it
    assert (
        client.get(f"{API}/projects/{pid}", headers=superuser_token_headers).status_code
        == 200
    )


def test_credentials_are_write_only_and_encrypted(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    p = make_project(client, superuser_token_headers)
    secret = "Sup3r-Secret-pw!"
    r = client.post(
        f"{API}/projects/{p['id']}/roles",
        headers=superuser_token_headers,
        json={
            "name": "Admin",
            "login_recipe": {"mfa": "none"},
            "credentials": {"username": "admin@app.test", "password": secret},
        },
    )
    assert r.status_code == 200, r.text
    role = r.json()
    assert role["has_credentials"] is True
    assert secret not in r.text and "admin@app.test" not in r.text

    listing = client.get(
        f"{API}/projects/{p['id']}/roles", headers=superuser_token_headers
    )
    assert secret not in listing.text and "ciphertext" not in listing.text

    stored = db.exec(
        select(RoleCredential).where(RoleCredential.role_id == uuid.UUID(role["id"]))
    ).one()
    assert secret not in stored.ciphertext and "admin@app.test" not in stored.ciphertext

    # rotate
    r = client.put(
        f"{API}/projects/{p['id']}/roles/{role['id']}/credentials",
        headers=superuser_token_headers,
        json={"username": "admin@app.test", "password": "another-pw-123"},
    )
    assert r.status_code == 200 and "another-pw-123" not in r.text


def test_role_recipe_cannot_carry_secrets_and_names_are_unique(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    p = make_project(client, superuser_token_headers)
    bad = client.post(
        f"{API}/projects/{p['id']}/roles",
        headers=superuser_token_headers,
        json={"name": "X", "login_recipe": {"password": "oops"}},
    )
    assert bad.status_code == 422
    ok = client.post(
        f"{API}/projects/{p['id']}/roles",
        headers=superuser_token_headers,
        json={"name": "User"},
    )
    assert ok.status_code == 200
    dup = client.post(
        f"{API}/projects/{p['id']}/roles",
        headers=superuser_token_headers,
        json={"name": "User"},
    )
    assert dup.status_code == 409
