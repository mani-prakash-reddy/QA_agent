"""Confluence import: URL parsing, the REST call (mocked), HTML -> Markdown, and the API endpoint."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.qa.brd.confluence import (
    ConfluenceError,
    fetch_page,
    html_to_markdown,
    parse_page_ref,
)
from app.qa.brd.parse import parse_bytes
from tests.qa.test_api_projects import API, make_project
from tests.qa.test_brd_api import stack  # noqa: F401

HTML = """<h1>Login</h1><p>The system shall let a registered user sign in.</p>
<h2>FR-1 Sign in</h2><ul><li>Email is required</li><li>Password is required</li></ul>
<table><tr><th>Field</th><th>Rule</th></tr><tr><td>Email</td><td>valid format | max 255</td></tr></table>
<script>alert(1)</script>"""


def test_parse_page_ref() -> None:
    assert parse_page_ref(
        "https://acme.atlassian.net/wiki/spaces/PRD/pages/12345/Title"
    ) == (
        "https://acme.atlassian.net/wiki",
        "12345",
    )
    assert parse_page_ref("https://wiki.acme.com/pages/viewpage.action?pageId=987") == (
        "https://wiki.acme.com",
        "987",
    )
    with pytest.raises(ConfluenceError, match="page id"):
        parse_page_ref("https://acme.atlassian.net/wiki/spaces/PRD/overview")
    with pytest.raises(ConfluenceError, match="private"):
        parse_page_ref("http://10.0.0.5/pages/1")


def test_html_to_markdown_is_parsable() -> None:
    md = html_to_markdown("Shop BRD", HTML)
    assert "alert" not in md
    parsed = parse_bytes(md.encode(), "x.md")
    kinds = [b.kind for b in parsed.blocks]
    assert "heading" in kinds and "table" in kinds and "list" in kinds
    assert (
        "valid format / max 255" in md
    )  # pipes inside a cell must not break the table


def mock(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_uses_basic_auth_for_cloud_and_bearer_for_server() -> None:
    seen: list[httpx.Request] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, json={"title": "T", "body": {"export_view": {"value": HTML}}}
        )

    title, html = fetch_page(
        "https://x/wiki", "1", email="a@b.c", token="tok", client=mock(ok)
    )
    assert title == "T" and "<h1>" in html
    assert seen[0].headers["authorization"].startswith("Basic ")
    fetch_page("https://x", "1", email=None, token="tok", client=mock(ok))
    assert seen[1].headers["authorization"] == "Bearer tok"
    assert "expand=body.export_view" in str(seen[0].url)


@pytest.mark.parametrize(
    ("status", "text"),
    [(401, "refused the credentials"), (404, "not found"), (500, "HTTP 500")],
)
def test_fetch_errors_never_leak_the_token(status: int, text: str) -> None:
    with pytest.raises(ConfluenceError, match=text) as exc:
        fetch_page(
            "https://x",
            "1",
            email=None,
            token="SECRET-TOKEN",
            client=mock(lambda r: httpx.Response(status, text="SECRET-TOKEN")),
        )
    assert "SECRET-TOKEN" not in str(exc.value)


@pytest.mark.usefixtures("stack")
def test_endpoint_imports_the_page_as_a_brd(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_fetch(
        base: str, page_id: str, *, email: str | None, token: str, client: Any = None
    ) -> tuple[str, str]:
        calls.append({"base": base, "id": page_id, "email": email})
        assert token and client is None
        return "Shop BRD", HTML

    monkeypatch.setattr("app.api.routes.brd.fetch_page", fake_fetch)
    h = superuser_token_headers
    project = make_project(client, h)
    try:
        body = {
            "page_url": "https://acme.atlassian.net/wiki/spaces/PRD/pages/12345/Shop",
            "email": "qa@acme.com",
            "api_token": "tok-123",
        }
        r = client.post(
            f"{API}/projects/{project['id']}/brd/confluence", headers=h, json=body
        )
        assert r.status_code == 200, r.text
        doc = r.json()["document"]
        assert doc["filename"] == "Shop BRD (Confluence).md"
        assert calls == [
            {
                "base": "https://acme.atlassian.net/wiki",
                "id": "12345",
                "email": "qa@acme.com",
            }
        ]
        assert "tok-123" not in json.dumps(r.json())
        again = client.post(
            f"{API}/projects/{project['id']}/brd/confluence", headers=h, json=body
        )
        assert again.status_code == 409
        bad = client.post(
            f"{API}/projects/{project['id']}/brd/confluence",
            headers=h,
            json={
                **body,
                "page_url": "https://acme.atlassian.net/wiki/spaces/PRD/overview",
            },
        )
        assert bad.status_code == 422 and "page id" in bad.json()["detail"]
    finally:
        client.delete(f"{API}/projects/{project['id']}", headers=h)
