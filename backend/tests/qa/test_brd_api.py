"""BRD upload -> ingest run -> requirements review, with a deterministic model (no network)."""

import asyncio
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.qa.brd.extract import normalize
from app.qa.llm.fake import RuleChatModel
from app.qa.models import AgentEvent, BrdChunk, BrdDocument, Requirement, Run
from app.qa.runs.executor import execute_run
from tests.qa.test_api_projects import make_project

API = settings.API_V1_STR
SAMPLES = Path(__file__).resolve().parents[3] / "demo" / "brds"
LINE = re.compile(r"^(?P<id>(?:FR|BR|NFR)-\d+):\s*(?P<text>.+)$", re.M)
BEHAVE = {"hallucinate": False}


def ideal_model(prompt: str) -> dict[str, Any]:
    """A well-behaved extractor: one requirement per 'FR-1: ...' line, plus the one unnumbered NFR."""
    reqs = []
    for m in LINE.finditer(prompt):
        kind = (
            "nfr"
            if m["id"].startswith("NFR")
            else "business_rule"
            if m["id"].startswith("BR")
            else "functional"
        )
        reqs.append(
            {
                "native_id": m["id"],
                "type": kind,
                "title": m["text"][:60],
                "description": m["text"],
                "quote": m["text"],
                "actors": [],
                "acceptance_criteria": [],
            }
        )
    for m in re.finditer(r"^(The system should respond quickly[^\n]*)$", prompt, re.M):
        reqs.append(
            {
                "native_id": None,
                "type": "nfr",
                "title": "Respond quickly",
                "description": m[1],
                "quote": m[1],
            }
        )
    if BEHAVE["hallucinate"] and reqs:
        reqs.append(
            {
                "native_id": "FR-99",
                "type": "functional",
                "title": "Invented requirement",
                "description": "The system sends SMS codes.",
                "quote": "The system shall send an SMS code on every login.",
            }
        )
    return {"requirements": reqs}


def fake_embed_factory() -> Callable[..., Any]:
    async def fake_embed(texts: list[str], *, confidential: bool) -> list[list[float]]:  # noqa: ARG001
        return [[0.01 * (i % 7)] * 1536 for i, _ in enumerate(texts)]

    return fake_embed


@dataclass
class Ingested:
    project_id: str
    document_id: str
    run_id: str
    summary: dict[str, Any]


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(
        settings, "LLM_API_KEY", __import__("pydantic").SecretStr("test-key-not-real")
    )
    BEHAVE["hallucinate"] = False

    async def no_queue(_run_id: uuid.UUID) -> str:
        return "test-job"

    monkeypatch.setattr("app.api.routes.brd.enqueue_run", no_queue)
    monkeypatch.setattr(
        "app.qa.runs.kinds.ingest.get_chat_model",
        lambda *a, **k: RuleChatModel(handler=ideal_model),
    )
    monkeypatch.setattr("app.qa.runs.kinds.ingest.embed_texts", fake_embed_factory())


def run_worker(run_id: str) -> str:
    async def go() -> str:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return await execute_run(uuid.UUID(run_id), redis)
        finally:
            await redis.aclose()

    return asyncio.run(go())


@pytest.fixture
def ingest(
    request: pytest.FixtureRequest,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> Iterator[Callable[..., Ingested]]:
    request.getfixturevalue("stack")  # artifacts dir, fake model, no queue
    created: list[str] = []

    def _ingest(
        name: str = "juice-shop.md", *, headers: dict[str, str] | None = None
    ) -> Ingested:
        h = headers or superuser_token_headers
        project = make_project(client, h)
        created.append(project["id"])
        data = (SAMPLES / name).read_bytes()
        r = client.post(
            f"{API}/projects/{project['id']}/brd",
            headers=h,
            files={"file": (name, data)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert run_worker(body["run_id"]) == "succeeded"
        with Session(engine) as s:
            summary = s.get(Run, uuid.UUID(body["run_id"])).summary  # type: ignore[union-attr]
        return Ingested(project["id"], body["document"]["id"], body["run_id"], summary)

    yield _ingest
    for pid in created:
        client.delete(f"{API}/projects/{pid}", headers=superuser_token_headers)


def reqs(
    client: TestClient, h: dict[str, str], project_id: str, **params: Any
) -> list[dict[str, Any]]:
    r = client.get(
        f"{API}/projects/{project_id}/requirements",
        headers=h,
        params={"limit": 500, **params},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------------------ ingest quality
def test_every_expected_requirement_is_extracted_with_its_brd_id(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    import json

    gold = json.loads((SAMPLES / "juice-shop.gold.json").read_text())["requirements"]
    ing = ingest()
    got = {
        r["req_key"]: r for r in reqs(client, superuser_token_headers, ing.project_id)
    }
    for g in gold:
        if g["key"]:
            assert g["key"] in got, f"missing {g['key']}"
            assert got[g["key"]]["key_origin"] == "brd"
            assert (
                g["match"].lower()
                in (
                    got[g["key"]]["description"]
                    + got[g["key"]]["source_anchor"]["quote"]
                ).lower()
            )
    generated = [r for r in got.values() if r["key_origin"] == "generated"]
    assert [r["req_key"] for r in generated] == [
        "REQ-001"
    ]  # the unnumbered "respond quickly" requirement
    assert ing.summary["requirements"] == len(gold) == len(got)


def test_vague_requirements_are_flagged_and_precise_ones_are_not(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    import json

    gold = json.loads((SAMPLES / "juice-shop.gold.json").read_text())["requirements"]
    expected_flagged = {g["key"] or "REQ-001" for g in gold if g["flagged"]}
    ing = ingest()
    flagged = {
        r["req_key"]
        for r in reqs(client, superuser_token_headers, ing.project_id)
        if r["ambiguity_flag"]
    }
    assert flagged == expected_flagged, flagged ^ expected_flagged
    nfr3 = next(
        r
        for r in reqs(client, superuser_token_headers, ing.project_id)
        if r["req_key"] == "NFR-3"
    )
    assert any("TBD" in x["reason"] for x in nfr3["ambiguity_reasons"])
    assert nfr3["needs_decision"] is True


def test_every_requirement_quote_is_real_text_from_its_source_chunk(
    ingest: Callable[..., Ingested],
) -> None:
    ing = ingest()
    with Session(engine) as s:
        for r in s.exec(
            select(Requirement).where(
                Requirement.project_id == uuid.UUID(ing.project_id)
            )
        ).all():
            chunk = s.get(BrdChunk, uuid.UUID(r.source_anchor["chunk_id"]))
            assert chunk is not None
            assert normalize(r.source_anchor["quote"]) in normalize(chunk.text), (
                r.req_key
            )
            assert r.source_anchor["section"] == chunk.section_path
            assert r.embedding is not None and r.embedding_model


def test_an_invented_requirement_is_rejected_by_grounding(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    BEHAVE["hallucinate"] = True
    ing = ingest()
    keys = {r["req_key"] for r in reqs(client, superuser_token_headers, ing.project_id)}
    assert "FR-99" not in keys
    assert ing.summary["rejected_quotes"] >= 1


def test_all_three_formats_ingest_to_the_same_requirements(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    sets = {}
    for name in ("juice-shop.md", "juice-shop.docx", "juice-shop.pdf"):
        ing = ingest(name)
        sets[name] = {
            r["req_key"] for r in reqs(client, superuser_token_headers, ing.project_id)
        }
    assert sets["juice-shop.md"] == sets["juice-shop.docx"] == sets["juice-shop.pdf"]
    assert len(sets["juice-shop.md"]) == 27


def test_pdf_requirements_know_their_page(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    ing = ingest("juice-shop.pdf")
    nfr = next(
        r
        for r in reqs(client, superuser_token_headers, ing.project_id)
        if r["req_key"] == "NFR-3"
    )
    assert nfr["source_anchor"]["page"] >= 2


# ------------------------------------------------------------------------------ upload safety
@pytest.mark.usefixtures("stack")
def test_upload_rejections(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    p = make_project(client, h)["id"]
    url = f"{API}/projects/{p}/brd"
    assert client.post(url, headers=h, files={"file": ("a.md", b"")}).status_code == 422
    assert (
        client.post(
            url, headers=h, files={"file": ("a.bin", bytes(range(256)) * 8)}
        ).status_code
        == 415
    )
    assert (
        client.post(
            url, headers=h, files={"file": ("a.txt", b"x" * (21 * 1024 * 1024))}
        ).status_code
        == 413
    )
    ok = client.post(
        url,
        headers=h,
        files={"file": ("a.md", b"# T\n\nFR-1: The user shall log in.\n")},
    )
    assert ok.status_code == 200
    dup = client.post(
        url,
        headers=h,
        files={"file": ("renamed.md", b"# T\n\nFR-1: The user shall log in.\n")},
    )
    assert dup.status_code == 409 and "a.md" in dup.json()["detail"]
    client.delete(f"{API}/projects/{p}", headers=h)


@pytest.mark.usefixtures("stack")
def test_a_hostile_filename_never_reaches_the_filesystem_path(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    h = superuser_token_headers
    p = make_project(client, h)["id"]
    r = client.post(
        f"{API}/projects/{p}/brd",
        headers=h,
        files={
            "file": (
                "../../../etc/passwd\x00.md",
                b"# T\n\nFR-1: The user shall log in.\n",
            )
        },
    )
    assert r.status_code == 200
    shown = r.json()["document"]["filename"]
    assert (
        shown.startswith("passwd") and "/" not in shown and ".." not in shown
    )  # display only, never a path
    with Session(engine) as s:
        doc = s.get(BrdDocument, uuid.UUID(r.json()["document"]["id"]))
        assert doc is not None
        stored = (tmp_path / doc.artifact_path).resolve()
    assert tmp_path.resolve() in stored.parents and stored.name == "original.txt"
    client.delete(f"{API}/projects/{p}", headers=h)


@pytest.mark.usefixtures("stack")
def test_a_corrupt_file_fails_cleanly_with_a_readable_reason(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    p = make_project(client, h)["id"]
    r = client.post(
        f"{API}/projects/{p}/brd",
        headers=h,
        files={"file": ("broken.pdf", b"%PDF-1.7 not a real pdf")},
    )
    assert r.status_code == 200
    assert run_worker(r.json()["run_id"]) == "failed"
    doc = client.get(f"{API}/brd/{r.json()['document']['id']}", headers=h).json()
    assert doc["status"] == "failed" and "PDF" in doc["error"]
    client.delete(f"{API}/projects/{p}", headers=h)


# ------------------------------------------------------------------------------ review workflow
def test_flagged_requirements_cannot_be_approved_until_a_human_decides(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    by_key = {r["req_key"]: r for r in reqs(client, h, ing.project_id)}
    flagged, clean = by_key["FR-12"], by_key["FR-1"]
    blocked = client.post(f"{API}/requirements/{flagged['id']}/approve", headers=h)
    assert blocked.status_code == 409 and "ambiguous" in blocked.json()["detail"]
    assert (
        client.post(f"{API}/requirements/{clean['id']}/approve", headers=h).json()[
            "review_status"
        ]
        == "approved"
    )
    # accepting the wording is an explicit human decision
    assert (
        client.post(
            f"{API}/requirements/{flagged['id']}/accept-wording", headers=h
        ).json()["needs_decision"]
        is False
    )
    assert (
        client.post(f"{API}/requirements/{flagged['id']}/approve", headers=h).json()[
            "review_status"
        ]
        == "approved"
    )
    assert (
        client.post(
            f"{API}/requirements/{clean['id']}/accept-wording", headers=h
        ).status_code
        == 409
    )  # not flagged


def test_editing_the_wording_rechecks_ambiguity_and_needs_reapproval(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    nfr1 = next(r for r in reqs(client, h, ing.project_id) if r["req_key"] == "NFR-1")
    assert nfr1["ambiguity_flag"] and nfr1["version"] == 1
    fixed = client.patch(
        f"{API}/requirements/{nfr1['id']}",
        headers=h,
        json={
            "title": "Page load time",
            "description": "Pages shall load within 2 seconds on a 10 Mbit/s connection for 95% of requests.",
        },
    ).json()
    assert fixed["review_status"] == "edited" and fixed["version"] == 2
    assert (
        fixed["ambiguity_flag"] is False and fixed["has_embedding"] is False
    )  # re-embedded later
    assert (
        client.post(f"{API}/requirements/{nfr1['id']}/approve", headers=h).json()[
            "review_status"
        ]
        == "approved"
    )
    # editing an approved requirement withdraws the approval
    again = client.patch(
        f"{API}/requirements/{nfr1['id']}", headers=h, json={"title": "Page load time"}
    ).json()
    assert again["review_status"] == "edited" and again["version"] == 3


def test_bulk_approve_skips_flagged_and_reports_why(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    everything = reqs(client, h, ing.project_id)
    res = client.post(
        f"{API}/projects/{ing.project_id}/requirements/bulk",
        headers=h,
        json={"ids": [r["id"] for r in everything], "action": "approve"},
    ).json()
    flagged = [r for r in everything if r["ambiguity_flag"]]
    assert res["updated"] == len(everything) - len(flagged)
    assert {s["req_key"] for s in res["skipped"]} == {r["req_key"] for r in flagged}
    summary = client.get(
        f"{API}/projects/{ing.project_id}/requirements/summary", headers=h
    ).json()
    assert summary["approved"] == res["updated"] and summary["needs_decision"] == len(
        flagged
    )


def test_listing_filters(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    assert {r["type"] for r in reqs(client, h, ing.project_id, type="nfr")} == {"nfr"}
    assert len(reqs(client, h, ing.project_id, needs_decision=True)) == 4
    assert {r["req_key"] for r in reqs(client, h, ing.project_id, q="basket")} >= {
        "FR-9",
        "FR-10",
        "BR-2",
    }
    first = reqs(client, h, ing.project_id, limit=5)
    assert len(first) == 5
    page = client.get(
        f"{API}/projects/{ing.project_id}/requirements", headers=h, params={"limit": 5}
    ).json()
    assert page["count"] == 27


def test_manual_requirement_and_detail_view_with_source_context(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    manual = client.post(
        f"{API}/projects/{ing.project_id}/requirements",
        headers=h,
        json={
            "title": "Audit log",
            "description": "Every login attempt is recorded in an audit log.",
        },
    )
    assert manual.status_code == 200 and manual.json()["key_origin"] == "manual"
    assert manual.json()["req_key"] == "REQ-002"
    dup = client.post(
        f"{API}/projects/{ing.project_id}/requirements",
        headers=h,
        json={"title": "x", "description": "y", "req_key": "fr-1"},
    )
    assert dup.status_code == 409
    fr9 = next(r for r in reqs(client, h, ing.project_id) if r["req_key"] == "FR-9")
    detail = client.get(f"{API}/requirements/{fr9['id']}", headers=h).json()
    assert fr9["source_anchor"]["quote"] in detail["source_text"]
    assert "3.3 Basket and Checkout" in detail["source_anchor"]["section"]


def test_reextract_keeps_reviewed_requirements_and_is_idempotent(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h = superuser_token_headers
    ing = ingest()
    before = {r["req_key"]: r for r in reqs(client, h, ing.project_id)}
    fr1 = before["FR-1"]
    client.post(f"{API}/requirements/{fr1['id']}/approve", headers=h)
    r = client.post(f"{API}/brd/{ing.document_id}/reextract", headers=h)
    assert r.status_code == 200
    assert run_worker(r.json()["run_id"]) == "succeeded"
    after = {x["req_key"]: x for x in reqs(client, h, ing.project_id)}
    assert set(after) == set(before)  # same ids, no duplicates
    assert (
        after["FR-1"]["id"] == fr1["id"]
        and after["FR-1"]["review_status"] == "approved"
    )  # untouched
    assert (
        after["FR-2"]["id"] != before["FR-2"]["id"]
    )  # unreviewed ones were regenerated


def test_deleting_a_document_removes_its_file_and_unreviewed_requirements(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    h = superuser_token_headers
    ing = ingest()
    fr1 = next(r for r in reqs(client, h, ing.project_id) if r["req_key"] == "FR-1")
    client.post(f"{API}/requirements/{fr1['id']}/approve", headers=h)
    assert any(tmp_path.rglob("original.txt"))
    assert client.delete(f"{API}/brd/{ing.document_id}", headers=h).status_code == 200
    assert not any(tmp_path.rglob("original.txt"))
    left = reqs(client, h, ing.project_id)
    assert [r["req_key"] for r in left] == ["FR-1"] and left[0]["document_id"] is None


def test_other_users_cannot_see_or_change_a_projects_requirements(
    ingest: Callable[..., Ingested],
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    ing = ingest()
    n = normal_user_token_headers
    some = reqs(client, superuser_token_headers, ing.project_id, limit=1)[0]["id"]
    for method, path in [
        ("get", f"/projects/{ing.project_id}/requirements"),
        ("get", f"/projects/{ing.project_id}/brd"),
        ("get", f"/requirements/{some}"),
        ("patch", f"/requirements/{some}"),
        ("post", f"/requirements/{some}/approve"),
        ("get", f"/brd/{ing.document_id}"),
        ("get", f"/brd/{ing.document_id}/chunks"),
        ("delete", f"/brd/{ing.document_id}"),
    ]:
        r = getattr(client, method)(
            API + path,
            headers=n,
            **({"json": {"title": "x"}} if method == "patch" else {}),
        )
        assert r.status_code == 403, (method, path, r.status_code)
    up = client.post(
        f"{API}/projects/{ing.project_id}/brd",
        headers=n,
        files={"file": ("a.md", b"# t\n\nFR-1: x shall y.\n")},
    )
    assert up.status_code == 403


@pytest.mark.usefixtures("stack")
def test_a_missing_language_model_fails_the_ingest_with_a_readable_reason(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API holds no LLM keys (only the worker does), so upload must succeed and the run must explain."""
    from app.qa.llm.gateway import LLMConfigError

    def broken(*_a: Any, **_k: Any) -> Any:
        raise LLMConfigError("LLM_BASE_URL / LLM_API_KEY are not configured")

    monkeypatch.setattr("app.qa.runs.kinds.ingest.get_chat_model", broken)
    monkeypatch.setattr(settings, "LLM_BASE_URL", None)
    h = superuser_token_headers
    p = make_project(client, h)["id"]
    r = client.post(
        f"{API}/projects/{p}/brd",
        headers=h,
        files={"file": ("a.md", b"# T\n\nFR-1: The user shall log in.\n")},
    )
    assert r.status_code == 200
    assert run_worker(r.json()["run_id"]) == "failed"
    doc = client.get(f"{API}/brd/{r.json()['document']['id']}", headers=h).json()
    assert doc["status"] == "failed" and "not configured" in doc["error"]
    client.delete(f"{API}/projects/{p}", headers=h)


def test_usage_events_are_deltas_that_sum_to_the_runs_token_total(
    ingest: Callable[..., Ingested],
) -> None:
    ing = ingest()
    with Session(engine) as s:
        events = s.exec(
            select(AgentEvent).where(
                AgentEvent.run_id == uuid.UUID(ing.run_id),
                AgentEvent.type == "llm_usage",
            )
        ).all()
    assert sum(e.payload["input_tokens"] for e in events) == ing.summary["input_tokens"]
    assert (
        sum(e.payload["output_tokens"] for e in events) == ing.summary["output_tokens"]
    )
