"""Read API over a real crawl's knowledge model."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.qa.models import UiState
from tests.qa.conftest import Crawl

API = settings.API_V1_STR


def state_id(crawl: Crawl, title: str, kind: str | None = None) -> str:
    with Session(engine) as s:
        stmt = select(UiState).where(
            UiState.snapshot_id == crawl.snapshot_id, UiState.title == title
        )
        if kind:
            stmt = stmt.where(UiState.kind == kind)
        return str(s.exec(stmt).first().id)  # type: ignore[union-attr]


def test_snapshots_and_state_listing_with_filters(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    h = superuser_token_headers
    snaps = client.get(f"{API}/projects/{crawl.project_id}/snapshots", headers=h).json()
    assert [s["status"] for s in snaps] == ["ready"]
    base = f"{API}/snapshots/{crawl.snapshot_id}/states"
    allstates = client.get(base, headers=h).json()
    assert len(allstates) == snaps[0]["stats"]["states"]
    modals = client.get(base, params={"kind": "modal"}, headers=h).json()
    assert {s["title"] for s in modals} >= {"Add Item", "Edit Item"}
    assert {
        s["title"] for s in client.get(base, params={"q": "add"}, headers=h).json()
    } == {"Add Item"}
    assert client.get(base, params={"role": "Admin"}, headers=h).json() == allstates
    assert client.get(base, params={"role": "Nobody"}, headers=h).json() == []
    add = next(s for s in modals if s["title"] == "Add Item")
    assert add["n_forms"] == 1 and add["has_screenshot"] and add["roles"] == ["Admin"]


def test_state_detail_exposes_fields_rules_and_graph(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    sid = state_id(crawl, "Add Item", "modal")
    d = client.get(f"{API}/states/{sid}", headers=superuser_token_headers).json()
    assert d["kind"] == "modal" and d["nav_path"][0] == "Items"
    (form,) = d["forms"]
    title = next(f for f in form["fields"] if f["label"] == "Title")
    assert title["required"] and any(
        m["message"] == "Title is required" for m in title["validation_messages"]
    )
    assert {e["label"] for e in d["elements"]} >= {"Title", "Save", "Cancel"}
    assert any(e["is_submit"] for e in d["elements"] if e["label"] == "Save")
    assert any(
        t["from_title"] == "Items" and t["via_label"] == "Add Item"
        for t in d["incoming"]
    )


def test_destructive_elements_are_visible_in_the_model(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    sid = state_id(crawl, "Row actions menu", "menu")
    d = client.get(f"{API}/states/{sid}", headers=superuser_token_headers).json()
    delete = next(e for e in d["elements"] if e["label"] == "Delete")
    assert delete["is_destructive"] and delete["destructive_reason"].startswith(
        "denylist"
    )


def test_screenshot_served_as_png(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    r = client.get(
        f"{API}/states/{state_id(crawl, 'Items', 'page')}/screenshot",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_screenshot_path_traversal_is_refused(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    sid = state_id(crawl, "Dashboard", "page")
    with Session(engine) as s:
        st = s.get(UiState, uuid.UUID(sid))
        original, st.screenshot_path = st.screenshot_path, "../../../../etc/passwd"  # type: ignore[union-attr]
        s.add(st)
        s.commit()
    try:
        r = client.get(
            f"{API}/states/{sid}/screenshot", headers=superuser_token_headers
        )
        assert r.status_code == 404
    finally:
        with Session(engine) as s:
            st = s.get(UiState, uuid.UUID(sid))
            st.screenshot_path = original  # type: ignore[union-attr]
            s.add(st)
            s.commit()


def test_role_matrix_and_json_export(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    h = superuser_token_headers
    m = client.get(f"{API}/snapshots/{crawl.snapshot_id}/matrix", headers=h).json()
    assert m["roles"] == ["Admin"]
    assert all(row["access"] == {"Admin": True} for row in m["rows"])
    assert {"Items", "Admin"} <= {r["module"] for r in m["rows"]}

    export = client.get(
        f"{API}/snapshots/{crawl.snapshot_id}/export.json", headers=h
    ).json()
    stats = export["snapshot"]["stats"]
    assert len(export["states"]) == stats["states"]
    assert (
        len(export["forms"]) == stats["forms"]
        and len(export["fields"]) == stats["fields"]
    )
    assert len(export["transitions"]) == stats["transitions"]
    assert (
        "screenshot_path" not in export["states"][0]
    )  # server paths are not part of the model
    assert export["role_access"] and export["roles"] == ["Admin"]


def test_crawl_log_summary_and_filters(
    client: TestClient, superuser_token_headers: dict[str, str], crawl: Crawl
) -> None:
    h = superuser_token_headers
    url = f"{API}/runs/{crawl.run_id}/crawl-log"
    log = client.get(url, headers=h).json()
    summary = {(x["status"], x["reason"]): x["count"] for x in log["summary"]}
    assert summary[("skipped", "external_link")] >= 1
    assert any(k[0] == "blocked" for k in summary)
    blocked = client.get(url, params={"status": "blocked"}, headers=h).json()["events"]
    assert blocked and {e["status"] for e in blocked} == {"blocked"}
    assert {"Delete", "Log out"} <= {e["element_label"] for e in blocked}
    assert all(e["role"] == "Admin" for e in blocked)
    only = client.get(url, params={"reason": "external_link"}, headers=h).json()[
        "events"
    ]
    assert only and all(e["reason"] == "external_link" for e in only)


@pytest.mark.parametrize(
    "path",
    [
        "/projects/{p}/snapshots",
        "/snapshots/{s}/states",
        "/snapshots/{s}/matrix",
        "/snapshots/{s}/export.json",
        "/runs/{r}/crawl-log",
    ],
)
def test_other_users_cannot_read_the_model(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    crawl: Crawl,
    path: str,
) -> None:
    url = API + path.format(p=crawl.project_id, s=crawl.snapshot_id, r=crawl.run_id)
    assert client.get(url, headers=normal_user_token_headers).status_code == 403
    assert client.get(url).status_code == 401
