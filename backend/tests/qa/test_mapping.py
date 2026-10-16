"""Requirement <-> screen mapping and the two-way gap report, with a deterministic judge."""

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.llm.fake import RuleChatModel
from app.qa.mapping.cards import ScreenCard
from app.qa.mapping.judge import JudgeResult, Verdict, verify
from app.qa.mapping.retrieve import Candidate, Retriever
from app.qa.models import (
    AppSnapshot,
    FormField,
    Gap,
    Requirement,
    RequirementMapping,
    UiElement,
    UiForm,
    UiState,
)
from tests.qa.test_api_projects import make_project
from tests.qa.test_brd_api import run_worker

API = settings.API_V1_STR
JUDGE_BEHAVIOUR: dict[str, Any] = {
    "invent": False,
    "unverifiable": False,
    "says_invisible": False,
    "lazy_first": False,
}

# requirement key -> (screen heading that implements it, label to cite) | None (absent)
TRUTH: dict[str, tuple[str, str] | None] = {
    "FR-1": ("Items", "Add Item"),
    "FR-2": ("User Management", "Add User"),
    "FR-3": None,  # "export to PDF": the app has no such screen
}


def judge_model(prompt: str) -> dict[str, Any]:
    key = re.search(r"Requirement (\S+) \(", prompt)
    assert key
    if key[1] == "NFR-1":
        return {"ui_observable": False, "verdicts": []}
    if key[1] == "FR-3" and JUDGE_BEHAVIOUR["says_invisible"]:
        # a model that wrongly calls a functional requirement "not visible in the UI"
        return {"ui_observable": False, "verdicts": []}
    if (
        key[1] == "FR-1"
        and JUDGE_BEHAVIOUR["lazy_first"]
        and "Look again" not in prompt
    ):
        return {"ui_observable": True, "verdicts": []}  # misses it on the first pass
    truth = TRUTH.get(key[1])
    verdicts: list[dict[str, Any]] = []
    if JUDGE_BEHAVIOUR["invent"]:
        verdicts.append(
            {
                "screen": "S99",
                "confidence": 0.95,
                "rationale": "made up",
                "evidence": ["X"],
            }
        )
    if truth:
        for alias, heading in re.findall(r"\[(S\d+)\]\nScreen: (.+?) \[", prompt):
            if heading == truth[0]:
                verdicts.append(
                    {
                        "screen": alias,
                        "confidence": 0.9,
                        "rationale": f"{heading} offers {truth[1]}",
                        "evidence": ["Nonexistent label"]
                        if JUDGE_BEHAVIOUR["unverifiable"]
                        else [truth[1]],
                    }
                )
    return {"ui_observable": True, "verdicts": verdicts}


@dataclass
class World:
    project_id: str
    snapshot_id: uuid.UUID
    states: dict[str, uuid.UUID]
    reqs: dict[str, uuid.UUID]


def seed(project_id: str) -> World:
    pid = uuid.UUID(project_id)
    with Session(engine) as s:
        snap = AppSnapshot(project_id=pid, status="ready")
        s.add(snap)
        s.flush()
        states: dict[str, UiState] = {}

        def screen(
            heading: str,
            route: str,
            labels: list[str],
            kind: str = "page",
            fp: str | None = None,
        ) -> UiState:
            st = UiState(
                snapshot_id=snap.id,
                fingerprint=fp or heading,
                kind=kind,
                route_template=route,
                title=heading,
                heading=heading,
            )
            s.add(st)
            s.flush()
            for lab in labels:
                s.add(
                    UiElement(
                        state_id=st.id, key=f"button|{lab}|0", kind="button", label=lab
                    )
                )
            states[fp or heading] = st
            return st

        screen("Login", "/login", ["Sign in"])
        items = screen("Items", "/items", ["Add Item", "Search items"])
        screen(
            "Items", "/items", ["Add Item"], fp="Items-empty"
        )  # a data variant of the same route
        screen("User Management", "/admin", ["Add User", "Deactivate"])
        screen("Audit Log", "/audit", ["Filter by date"])  # nobody mentions it
        screen("Blank", "blank", [])  # an empty shell is not a screen
        modal = screen("Add Item", "/items", ["Save"], kind="modal")
        form = UiForm(state_id=modal.id, name="Add Item", submit_label="Save")
        s.add(form)
        s.flush()
        s.add(FormField(form_id=form.id, label="Title", required=True, max_length=255))
        reqs: dict[str, Requirement] = {}
        for key, rtype, title, desc in [
            (
                "FR-1",
                "functional",
                "Add an item",
                "A user can add an item with a title.",
            ),
            (
                "FR-2",
                "functional",
                "Manage users",
                "An admin can add and deactivate users.",
            ),
            (
                "FR-3",
                "functional",
                "Export to PDF",
                "A user can export the item list to PDF.",
            ),
            ("NFR-1", "nfr", "Page load time", "Pages load within two seconds."),
        ]:
            reqs[key] = Requirement(
                project_id=pid,
                req_key=key,
                key_origin="brd",
                type=rtype,
                title=title,
                description=desc,
                source_anchor={"quote": desc},
                review_status="approved",
            )
        s.add_all(reqs.values())
        s.commit()
        assert items.id
        return World(
            project_id,
            snap.id,
            {k: v.id for k, v in states.items()},
            {k: v.id for k, v in reqs.items()},
        )


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", SecretStr("test-key-not-real"))
    JUDGE_BEHAVIOUR.update(
        invent=False, unverifiable=False, says_invisible=False, lazy_first=False
    )

    async def no_queue(_run_id: uuid.UUID) -> str:
        return "test-job"

    async def no_embed(_t: list[str], *, confidential: bool) -> list[list[float]]:  # noqa: ARG001
        raise RuntimeError("embeddings offline")

    monkeypatch.setattr("app.api.routes.mapping.enqueue_run", no_queue)
    monkeypatch.setattr(
        "app.qa.runs.kinds.map.get_chat_model",
        lambda *a, **k: RuleChatModel(handler=judge_model, tool_name="JudgeResult"),
    )
    monkeypatch.setattr("app.qa.runs.kinds.map.embed_texts", no_embed)


@pytest.fixture
def world(
    request: pytest.FixtureRequest,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> Iterator[Callable[[], World]]:
    request.getfixturevalue("stack")
    created: list[str] = []

    def _make() -> World:
        p = make_project(client, superuser_token_headers)
        created.append(p["id"])
        return seed(p["id"])

    yield _make
    for pid in created:
        client.delete(f"{API}/projects/{pid}", headers=superuser_token_headers)


def start(client: TestClient, h: dict[str, str], w: World) -> dict[str, Any]:
    r = client.post(f"{API}/projects/{w.project_id}/mapping/run", headers=h, json={})
    assert r.status_code == 200, r.text
    assert run_worker(r.json()["id"]) == "succeeded"
    return client.get(f"{API}/runs/{r.json()['id']}", headers=h).json()["summary"]  # type: ignore[no-any-return]


def gaps(
    client: TestClient, h: dict[str, str], w: World, **q: str
) -> list[dict[str, Any]]:
    r = client.get(f"{API}/projects/{w.project_id}/gaps", headers=h, params=q)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def maps(client: TestClient, h: dict[str, str], w: World) -> list[dict[str, Any]]:
    r = client.get(f"{API}/projects/{w.project_id}/mappings", headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------------- unit: retrieval
def card(title: str, text: str, key: str | None = None) -> ScreenCard:
    return ScreenCard(
        group_key=key or title,
        state_id=uuid.uuid4(),
        state_ids=[],
        title=title,
        kind="page",
        route=None,
        module=None,
        nav=[],
        text=text,
        labels={title.lower()},
    )


def test_retrieval_ranks_the_relevant_screen_first_without_embeddings() -> None:
    cards = [
        card("Login", "Screen: Login\nControls: Sign in; Forgot password"),
        card("Items", "Screen: Items\nControls: Add Item; Search items"),
        card("Audit Log", "Screen: Audit Log\nControls: Filter by date"),
    ]
    got = Retriever(cards).search("A user can add an item with a title", None, 3)
    assert got[0].card.title == "Items"
    assert all(
        c.card.title != "Audit Log" for c in got
    )  # no shared vocabulary -> not a candidate


def test_embeddings_rescue_a_paraphrase_the_keywords_miss() -> None:
    cards = [
        card("Basket", "Screen: Basket\nControls: Checkout"),
        card("Items", "Screen: Items"),
    ]
    vecs = {"Basket": [1.0, 0.0], "Items": [0.0, 1.0]}
    got = Retriever(cards, vecs).search("Customers pay for goods", [0.9, 0.1], 2)
    assert got and got[0].card.title == "Basket"


# --------------------------------------------------------------------------------- unit: verification
def cand(title: str, text: str) -> Candidate:
    return Candidate(card(title, text), 0.5, None, 0.5)


def test_verdicts_are_candidate_constrained_and_evidence_is_checked() -> None:
    cs = [cand("Items", "Screen: Items\nControls: Add Item")]
    res = JudgeResult(
        verdicts=[
            Verdict(screen="S1", confidence=0.9, rationale="ok", evidence=["add item"]),
            Verdict(screen="S7", confidence=0.99, rationale="invented", evidence=["x"]),
        ]
    )
    out = verify(res, cs)
    assert [o.group_key for o in out] == ["Items"] and out[0].verified
    assert out[0].confidence == 0.9 and out[0].citations == [{"label": "add item"}]


def test_a_claim_with_no_checkable_evidence_cannot_be_confident() -> None:
    cs = [cand("Items", "Screen: Items\nControls: Add Item")]
    res = JudgeResult(
        verdicts=[
            Verdict(
                screen="S1", confidence=0.95, rationale="trust me", evidence=["Nope"]
            )
        ]
    )
    out = verify(res, cs)
    assert out[0].confidence <= 0.4 and not out[0].verified


def test_a_requirement_is_not_spread_across_every_screen() -> None:
    cs = [
        cand(f"Screen {i}", f"Screen: Screen {i}\nControls: Open search")
        for i in range(1, 7)
    ]
    res = JudgeResult(
        verdicts=[
            Verdict(
                screen=f"S{i}",
                confidence=c,
                rationale="shell search",
                evidence=["open search"],
            )
            for i, c in zip(range(1, 7), [0.9, 0.85, 0.8, 0.78, 0.75, 0.5], strict=True)
        ]
    )
    out = verify(res, cs)
    assert [o.group_key for o in out] == [
        "Screen 1",
        "Screen 2",
        "Screen 3",
        "Screen 4",
    ]


# --------------------------------------------------------------------------------- run + API
def test_mapping_run_links_requirements_and_reports_gaps_in_both_directions(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    summary = start(client, h, w)
    assert (
        summary["matched"] == 2 and summary["not_ui"] == 1 and summary["unmatched"] == 1
    )
    assert (
        "embeddings offline" in summary["embedding_errors"][0]
    )  # fell back to keywords

    got = {(m["req_key"], m["screen_title"]): m for m in maps(client, h, w)}
    assert set(got) == {("FR-1", "Items"), ("FR-2", "User Management")}
    fr1 = got[("FR-1", "Items")]
    assert fr1["confidence"] >= 0.8 and fr1["citations"] == [{"label": "Add Item"}]
    assert fr1["effective"] and fr1["review_status"] == "proposed"

    req_gaps = {
        g["req_key"]: g for g in gaps(client, h, w, direction="requirement_unmatched")
    }
    assert set(req_gaps) == {"FR-3", "NFR-1"}
    assert (
        req_gaps["FR-3"]["code"] == "judged_absent"
        and req_gaps["FR-3"]["severity"] == "high"
    )
    assert (
        req_gaps["NFR-1"]["code"] == "not_ui_observable"
        and req_gaps["NFR-1"]["severity"] == "low"
    )

    feats = gaps(client, h, w, direction="feature_unmentioned")
    titles = {g["screen_title"] for g in feats}
    assert titles == {
        "Login",
        "Audit Log",
        "Add Item",
    }  # the Items variants are one screen, and mapped

    s = client.get(f"{API}/projects/{w.project_id}/mapping/summary", headers=h).json()
    assert (s["requirements_total"], s["requirements_matched"]) == (4, 2)
    assert s["feature_gaps"] == 3 and s["screens_total"] == 5


def test_screen_variants_of_one_route_are_one_candidate(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    start(client, h, w)
    items = [m for m in maps(client, h, w) if m["screen_title"] == "Items"]
    assert len(items) == 1  # not one per data variant


def test_a_hallucinated_screen_never_becomes_a_mapping(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    JUDGE_BEHAVIOUR["invent"] = True
    start(client, h, w)
    assert {m["req_key"] for m in maps(client, h, w)} == {"FR-1", "FR-2"}


def test_unverifiable_evidence_leaves_a_weak_match_that_is_still_a_gap(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    JUDGE_BEHAVIOUR["unverifiable"] = True
    summary = start(client, h, w)
    assert summary["matched"] == 0 and summary["weak"] == 2
    assert all(
        not m["effective"] and m["confidence"] <= 0.4 for m in maps(client, h, w)
    )
    assert {
        g["req_key"] for g in gaps(client, h, w, direction="requirement_unmatched")
    } == {
        "FR-1",
        "FR-2",
        "FR-3",
        "NFR-1",
    }


def test_human_decisions_survive_a_rerun_and_rederive_the_gaps(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    start(client, h, w)
    fr1 = next(m for m in maps(client, h, w) if m["req_key"] == "FR-1")
    r = client.patch(
        f"{API}/mappings/{fr1['id']}", headers=h, json={"review_status": "rejected"}
    )
    assert r.status_code == 200 and not r.json()["effective"]
    assert "FR-1" in {
        g["req_key"] for g in gaps(client, h, w, direction="requirement_unmatched")
    }

    # link FR-3 to a screen by hand: it stops being a gap, and the screen stops being undocumented
    audit = w.states["Audit Log"]
    r = client.post(
        f"{API}/projects/{w.project_id}/mappings",
        headers=h,
        json={
            "requirement_id": str(w.reqs["FR-3"]),
            "state_id": str(audit),
            "note": "audit export",
        },
    )
    assert r.status_code == 200 and r.json()["method"] == "manual"
    assert "FR-3" not in {
        g["req_key"] for g in gaps(client, h, w, direction="requirement_unmatched")
    }
    assert "Audit Log" not in {
        g["screen_title"] for g in gaps(client, h, w, direction="feature_unmentioned")
    }

    start(client, h, w)  # re-run: the rejection and the manual link both stand
    after = {(m["req_key"], m["screen_title"]): m for m in maps(client, h, w)}
    assert after[("FR-1", "Items")]["review_status"] == "rejected"
    assert after[("FR-3", "Audit Log")]["review_status"] == "confirmed"
    assert ("FR-2", "User Management") in after


def test_an_accepted_gap_stays_accepted_across_runs(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    start(client, h, w)
    fr3 = next(g for g in gaps(client, h, w) if g["req_key"] == "FR-3")
    r = client.patch(f"{API}/gaps/{fr3['id']}", headers=h, json={"status": "accepted"})
    assert r.status_code == 200
    start(client, h, w)
    again = next(g for g in gaps(client, h, w) if g["req_key"] == "FR-3")
    assert again["status"] == "accepted" and again["id"] == fr3["id"]


def test_rejected_requirements_are_not_mapped_or_reported(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    client.post(f"{API}/requirements/{w.reqs['FR-3']}/reject", headers=h)
    start(client, h, w)
    assert "FR-3" not in {g["req_key"] for g in gaps(client, h, w)}


def test_gap_report_exports_csv_and_json(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    start(client, h, w)
    csv_r = client.get(
        f"{API}/projects/{w.project_id}/gaps/export",
        headers=h,
        params={"format": "csv"},
    )
    assert (
        csv_r.status_code == 200 and "FR-3" in csv_r.text and "Audit Log" in csv_r.text
    )
    assert csv_r.text.splitlines()[0].startswith("Direction,Requirement ID")
    js = client.get(
        f"{API}/projects/{w.project_id}/gaps/export",
        headers=h,
        params={"format": "json"},
    )
    assert len(js.json()) == 5  # 2 requirement gaps + 3 undocumented screens


def test_mapping_needs_a_crawl_and_requirements(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    p = make_project(client, superuser_token_headers)
    try:
        r = client.post(
            f"{API}/projects/{p['id']}/mapping/run",
            headers=superuser_token_headers,
            json={},
        )
        assert r.status_code == 409 and "crawl" in r.json()["detail"]
    finally:
        client.delete(f"{API}/projects/{p['id']}", headers=superuser_token_headers)


def test_other_users_cannot_see_or_change_mappings(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    w = world()
    start(client, superuser_token_headers, w)
    m = maps(client, superuser_token_headers, w)[0]
    assert (
        client.get(
            f"{API}/projects/{w.project_id}/gaps", headers=normal_user_token_headers
        ).status_code
        == 403
    )
    r = client.patch(
        f"{API}/mappings/{m['id']}",
        headers=normal_user_token_headers,
        json={"review_status": "rejected"},
    )
    assert r.status_code == 403
    with Session(engine) as s:  # nothing changed
        assert s.get(RequirementMapping, uuid.UUID(m["id"])).review_status == "proposed"  # type: ignore[union-attr]
        assert s.exec(
            select(Gap).where(col(Gap.project_id) == uuid.UUID(w.project_id))
        ).first()


def test_a_partial_crawl_is_called_out_next_to_the_gaps(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    start(client, h, w)
    url = f"{API}/projects/{w.project_id}/mapping/summary"
    assert client.get(url, headers=h).json()["crawl_partial"] is False
    with Session(engine) as s:
        snap = s.get(AppSnapshot, w.snapshot_id)
        assert snap is not None
        snap.stats = {"roles": {"Admin": "budget_exceeded"}}
        s.add(snap)
        s.commit()
    assert client.get(url, headers=h).json()["crawl_partial"] is True


def test_a_functional_requirement_is_never_dismissed_as_not_visible_in_the_ui(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    JUDGE_BEHAVIOUR["says_invisible"] = True
    summary = start(client, h, w)
    fr3 = next(g for g in gaps(client, h, w) if g["req_key"] == "FR-3")
    assert fr3["code"] == "judged_absent" and fr3["severity"] == "high"
    assert summary["not_ui"] == 1  # only the NFR


def test_an_empty_first_answer_gets_one_second_look_before_it_becomes_a_gap(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    JUDGE_BEHAVIOUR["lazy_first"] = True
    start(client, h, w)
    assert "FR-1" in {m["req_key"] for m in maps(client, h, w)}
    assert "FR-1" not in {g["req_key"] for g in gaps(client, h, w)}
    # a genuinely absent requirement is still a gap after the second look
    assert "FR-3" in {g["req_key"] for g in gaps(client, h, w)}
