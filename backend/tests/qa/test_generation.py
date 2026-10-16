"""Test generation: rule cases from recorded constraints, a grounded author, dedup and the API."""

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.qa.generation.author import AuthorCase, AuthorResult, AuthorStep, ground
from app.qa.generation.gather import FormCtx
from app.qa.generation.rules import form_cases
from app.qa.generation.schema import Step, Target, Value, render_step
from app.qa.generation.validate import dedup
from app.qa.llm.fake import RuleChatModel
from app.qa.mapping.cards import ScreenCard
from app.qa.models import (
    AppRole,
    AppSnapshot,
    FormField,
    Requirement,
    RequirementMapping,
    RoleStateAccess,
    Transition,
    UiElement,
    UiForm,
    UiState,
)
from app.qa.models import TestCase as TC
from tests.qa.test_api_projects import make_project
from tests.qa.test_brd_api import run_worker

API = settings.API_V1_STR
AUTHOR = {"hallucinate": False, "fail": False}


def author_model(prompt: str) -> dict[str, Any]:
    key = re.search(r"Requirement (\S+) \(", prompt)
    assert key
    if AUTHOR["fail"]:
        raise RuntimeError("model unavailable")
    if key[1] == "FR-2":
        return {
            "cases": [
                {
                    "title": "Admin opens user management and adds a user",
                    "test_type": "business_rule",
                    "priority": "P1",
                    "role": "Admin",
                    "steps": [
                        {"action": "open_screen", "screen": "S1", "target": "Admin"},
                        {"action": "click", "screen": "S1", "target": "Add User"},
                        {
                            "action": "verify",
                            "screen": "S1",
                            "expect": "the new user appears in the list",
                        },
                    ],
                    "expected_result": "The administrator can add a user from the Admin screen",
                }
            ]
        }
    cases: list[dict[str, Any]] = [
        {
            "title": "Add an item from the Items screen",
            "test_type": "positive",
            "priority": "P1",
            "role": "Admin",
            "steps": [
                {"action": "open_screen", "screen": "S1", "target": "Items"},
                {"action": "click", "screen": "S1", "target": "Add Item"},
                {
                    "action": "verify",
                    "screen": "S1",
                    "expect": "the Add Item form is shown",
                },
            ],
            "expected_result": "The Add Item form opens from the Items screen",
        }
    ]
    if AUTHOR["hallucinate"]:
        cases += [
            {
                "title": "Export items to PDF",
                "test_type": "positive",
                "steps": [
                    {"action": "click", "screen": "S1", "target": "Export to PDF"}
                ],
                "expected_result": "A PDF is downloaded",
            },
            {
                "title": "Remove an item",
                "test_type": "negative",
                "steps": [{"action": "click", "screen": "S1", "target": "Delete"}],
                "expected_result": "The item is removed from the list",
            },
        ]
    return {"cases": cases}


@dataclass
class World:
    project_id: str
    snapshot_id: uuid.UUID
    reqs: dict[str, uuid.UUID]
    states: dict[str, uuid.UUID]


def seed(project_id: str) -> World:
    pid = uuid.UUID(project_id)
    with Session(engine) as s:
        snap = AppSnapshot(
            project_id=pid,
            status="ready",
            stats={"roles": {"Admin": "succeeded", "User": "succeeded"}},
        )
        s.add(snap)
        s.flush()
        admin = AppRole(project_id=pid, name="Admin")
        user = AppRole(project_id=pid, name="User")
        s.add_all([admin, user])

        def state(title: str, kind: str, nav: list[str], fp: str) -> UiState:
            st = UiState(
                snapshot_id=snap.id,
                fingerprint=fp,
                kind=kind,
                route_template=f"/{fp}",
                title=title,
                heading=title,
                module=nav[0] if nav else title,
                nav_path=nav,
                depth=0 if fp == "items" else 1,
            )
            s.add(st)
            s.flush()
            return st

        items = state("Items", "page", ["Items"], "items")
        modal = state("Add Item", "modal", [], "add-item")
        admin_page = state("Admin", "page", ["Admin"], "admin")
        for st, labels in [
            (items, [("Add Item", False), ("Delete", True)]),
            (modal, [("Save", False)]),
            (admin_page, [("Add User", False)]),
        ]:
            for lab, destructive in labels:
                s.add(
                    UiElement(
                        state_id=st.id,
                        key=f"button|{lab}|0",
                        kind="button",
                        label=lab,
                        is_destructive=destructive,
                    )
                )
        s.add(
            Transition(
                snapshot_id=snap.id,
                from_state_id=items.id,
                to_state_id=modal.id,
                via_label="Add Item",
                kind="overlay",
            )
        )
        s.add(
            Transition(
                snapshot_id=snap.id,
                from_state_id=items.id,
                to_state_id=admin_page.id,
                via_label="Admin",
                kind="navigation",
            )
        )
        s.add(
            UiElement(state_id=items.id, key="link|Admin|0", kind="link", label="Admin")
        )
        form = UiForm(state_id=modal.id, name="Add Item", submit_label="Save")
        s.add(form)
        s.flush()
        for i, f in enumerate(
            [
                FormField(
                    label="Title",
                    required=True,
                    max_length=255,
                    validation_messages=[
                        {"trigger": "empty", "message": "Title is required"}
                    ],
                ),
                FormField(label="Description", max_length=500),
                FormField(
                    label="Quantity",
                    input_type="number",
                    required=True,
                    min_value="1",
                    max_value="5",
                ),
                FormField(label="Contact email", input_type="email", required=True),
                FormField(label="Agree", input_type="checkbox"),
            ]
        ):
            f.form_id, f.ordinal = form.id, i
            s.add(f)
        for st in (items, modal):
            s.add(
                RoleStateAccess(snapshot_id=snap.id, role_id=admin.id, state_id=st.id)
            )
            s.add(RoleStateAccess(snapshot_id=snap.id, role_id=user.id, state_id=st.id))
        s.add(
            RoleStateAccess(
                snapshot_id=snap.id, role_id=admin.id, state_id=admin_page.id
            )
        )
        reqs: dict[str, Requirement] = {}
        for key, rtype, title, flagged in [
            ("FR-1", "functional", "Add an item", False),
            ("FR-5", "validation", "Item fields are validated", False),
            ("FR-2", "functional", "Manage users", False),
            ("FR-3", "functional", "Export to PDF", False),
            ("FR-4", "functional", "Make it user friendly", True),
            ("NFR-1", "nfr", "Fast", False),
        ]:
            reqs[key] = Requirement(
                project_id=pid,
                req_key=key,
                key_origin="brd",
                type=rtype,
                title=title,
                description=f"{title}.",
                source_anchor={"quote": title},
                review_status="approved",
                ambiguity_flag=flagged,
            )
        s.add_all(reqs.values())
        s.flush()
        for key, st in [
            ("FR-1", items),
            ("FR-5", items),
            ("FR-2", admin_page),
            ("FR-4", items),
            ("NFR-1", items),
        ]:
            s.add(
                RequirementMapping(
                    project_id=pid,
                    requirement_id=reqs[key].id,
                    snapshot_id=snap.id,
                    state_id=st.id,
                    confidence=0.9,
                    rationale="seeded",
                )
            )
        s.commit()
        return World(
            project_id,
            snap.id,
            {k: v.id for k, v in reqs.items()},
            {"items": items.id, "modal": modal.id, "admin": admin_page.id},
        )


@pytest.fixture
def world(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[], World]]:
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", SecretStr("test-key-not-real"))
    AUTHOR.update(hallucinate=False, fail=False)

    async def no_queue(_run_id: uuid.UUID) -> str:
        return "test-job"

    async def no_embed(_t: list[str], *, confidential: bool) -> list[list[float]]:  # noqa: ARG001
        raise RuntimeError("embeddings offline")

    monkeypatch.setattr("app.api.routes.tests.enqueue_run", no_queue)
    monkeypatch.setattr(
        "app.qa.runs.kinds.generate.get_chat_model",
        lambda *a, **k: RuleChatModel(handler=author_model, tool_name="AuthorResult"),
    )
    monkeypatch.setattr("app.qa.runs.kinds.generate.embed_texts", no_embed)
    created: list[str] = []

    def _make() -> World:
        p = make_project(client, superuser_token_headers)
        created.append(p["id"])
        return seed(p["id"])

    yield _make
    for pid in created:
        client.delete(f"{API}/projects/{pid}", headers=superuser_token_headers)


def generate(
    client: TestClient, h: dict[str, str], w: World, **body: Any
) -> dict[str, Any]:
    r = client.post(
        f"{API}/projects/{w.project_id}/tests/generate", headers=h, json=body
    )
    assert r.status_code == 200, r.text
    assert run_worker(r.json()["id"]) == "succeeded"
    return client.get(f"{API}/runs/{r.json()['id']}", headers=h).json()["summary"]  # type: ignore[no-any-return]


def cases(
    client: TestClient, h: dict[str, str], w: World, **q: Any
) -> list[dict[str, Any]]:
    r = client.get(
        f"{API}/projects/{w.project_id}/tests", headers=h, params={"limit": 1000, **q}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------------------ units: rules
def ctx_with(fields: list[FormField], submit: str | None = "Save") -> FormCtx:
    st = UiState(
        snapshot_id=uuid.uuid4(), fingerprint="f", kind="modal", title="Add Item"
    )
    form = UiForm(state_id=st.id, name="Add Item", submit_label=submit)
    return FormCtx(
        st, form, fields, [("Add Item", "button")], "Items", "Items", uuid.uuid4()
    )


def test_boundaries_come_from_recorded_limits_and_messages_from_the_app() -> None:
    fields = [
        FormField(
            label="Title",
            required=True,
            max_length=10,
            validation_messages=[{"trigger": "empty", "message": "Title is required"}],
        ),
        FormField(label="Qty", input_type="number", min_value="1", max_value="5"),
    ]
    out = form_cases(ctx_with(fields), "FR-1", "Admin", set())
    titles = {c.title for c in out}
    assert "Title is required on 'Add Item'" in titles
    assert any("accepts exactly 10 characters" in t for t in titles)
    assert any("rejects 11 characters" in t for t in titles)
    assert {t for t in titles if t.startswith("Qty:")} == {
        "Qty: 0 (below the minimum) on 'Add Item'",
        "Qty: 1 (the minimum) on 'Add Item'",
        "Qty: 5 (the maximum) on 'Add Item'",
        "Qty: 6 (above the maximum) on 'Add Item'",
    }
    req = next(c for c in out if c.title.startswith("Title is required"))
    assert (
        req.evidence["basis"] == "observed"
        and "Title is required" in req.expected_result
    )
    over = next(c for c in out if "rejects 11" in c.title)
    assert over.evidence["basis"] == "inferred"  # the app showed no too-long message


def test_a_destructive_submit_is_never_clicked() -> None:
    fields = [FormField(label="Email", input_type="email", required=True)]
    out = form_cases(
        ctx_with(fields, submit="Delete User Data"), "FR-1", None, {"delete user data"}
    )
    assert out, "validation cases are still generated"
    for c in out:
        assert not any(
            s.action == "click" and s.target and s.target.label == "Delete User Data"
            for s in c.steps
        )
    assert not any(
        c.test_type == "positive" for c in out
    )  # a positive flow would submit


def test_values_never_contain_credentials_and_steps_render_with_real_labels() -> None:
    fields = [
        FormField(label="Password", input_type="password", required=True),
        FormField(label="Email", input_type="email", required=True),
    ]
    out = form_cases(ctx_with(fields), "FR-1", "User", set())
    data = [d for c in out for d in c.test_data()]
    assert {d["value"] for d in data if d["field"] == "Password"} == {
        "<valid password>"
    }
    pos = next(c for c in out if c.test_type == "positive")
    assert pos.rendered()[0] == "1. Open the application at its start page (Items)"
    assert "2. Click the 'Add Item' button" in pos.rendered()
    assert render_step(
        Step(
            action="fill",
            target=Target(label="Title", kind="field"),
            value=Value(kind="repeat", count=256),
        )
    ) == ("Enter a string of 256 characters (\"a\" repeated) in the 'Title' field")


def card(
    labels: set[str],
    destructive: set[str] | None = None,
    fields: set[str] | None = None,
) -> ScreenCard:
    return ScreenCard(
        group_key="k",
        state_id=uuid.uuid4(),
        state_ids=[],
        title="Items",
        kind="page",
        route="/items",
        module=None,
        nav=["Items"],
        text="",
        labels=labels,
        destructive=destructive or set(),
        path=[("Items", "link")],
        kinds={x: ("field" if x in (fields or set()) else "button") for x in labels},
    )


def step(action: str, target: str | None = None, **kw: Any) -> AuthorStep:
    return AuthorStep(action=action, screen="S1", target=target, **kw)  # type: ignore[arg-type]


def test_author_cases_with_invented_or_destructive_targets_are_dropped() -> None:
    c = card({"items", "add item", "delete"}, {"delete"})
    res = AuthorResult(
        cases=[
            AuthorCase(
                title="ok",
                test_type="positive",
                steps=[step("click", "Add Item"), step("verify", expect="form shown")],
                expected_result="form opens",
            ),
            AuthorCase(
                title="invented",
                test_type="positive",
                steps=[step("click", "Export")],
                expected_result="pdf",
            ),
            AuthorCase(
                title="destructive",
                test_type="negative",
                steps=[step("click", "Delete")],
                expected_result="gone",
            ),
            AuthorCase(
                title="only navigation",
                test_type="positive",
                steps=[step("open_screen")],
                expected_result="opens",
            ),
        ]
    )
    good, problems = ground(res, "FR-1", [c], {"Admin"}, 5)
    assert [d.title for d in good] == ["ok"]
    assert len(problems) == 3 and any("destructive" in p for p in problems)


def test_dedup_drops_repeats_and_keeps_the_second_requirement_traceable() -> None:
    fields = [FormField(label="Title", required=True)]
    a = form_cases(ctx_with(fields), "FR-1", "Admin", set())
    b = form_cases(ctx_with(fields), "FR-5", "Admin", set())
    for x in b:
        x.state_id = a[0].state_id
    for x in a:
        x.state_id = a[0].state_id
    out = dedup(a + b, None)
    assert len(out.kept) == len(a) and out.exact == len(b)
    assert all(
        c.requirement_key == "FR-1" and c.evidence["also_covers"] == ["FR-5"]
        for c in out.kept
    )


# ------------------------------------------------------------------------------ run + API
def test_generation_creates_grounded_cases_and_explains_what_it_skipped(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    AUTHOR["hallucinate"] = True
    summary = generate(client, h, w)
    skipped = {x["req_key"]: x["reason"] for x in summary["requirements_skipped"]}
    assert set(skipped) == {"FR-3", "FR-4", "NFR-1"}
    assert "no screen is mapped" in skipped["FR-3"] and "ambiguous" in skipped["FR-4"]
    assert "non-functional" in skipped["NFR-1"]
    assert summary["dropped"]["ungrounded_author_cases"] == 2
    all_cases = cases(client, h, w)
    titles = {c["title"] for c in all_cases}
    assert "Export items to PDF" not in titles and "Remove an item" not in titles
    assert "Add an item from the Items screen" in titles
    assert "Title is required on 'Add Item'" in titles
    assert {c["origin"] for c in all_cases} == {"rule", "llm"}
    # every referenced label exists in the recorded app; no step touches the destructive control
    known = {
        "Items",
        "Admin",
        "Add Item",
        "Save",
        "Add User",
        "Title",
        "Description",
        "Quantity",
        "Contact email",
    }
    for c in all_cases:
        for st in c["steps"]:
            if st["target"]:
                assert st["target"]["label"] in known, st
                assert st["target"]["label"] != "Delete"
    # keys are sequential and unique
    keys = sorted(c["tc_key"] for c in all_cases)
    assert keys[0] == "TC-0001" and len(set(keys)) == len(keys)


def test_role_access_cases_distinguish_observed_from_not_observed(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    generate(client, h, w)
    roles = {c["title"]: c for c in cases(client, h, w, test_type="role_access")}
    assert roles["Admin can open 'Items'"]["evidence"]["basis"] == "observed"
    assert roles["User can open 'Items'"]["evidence"]["basis"] == "observed"
    denied = roles["User cannot open 'Admin'"]
    assert denied["evidence"]["basis"] == "not_observed" and denied["priority"] == "P3"
    assert "not probed" in denied["evidence"]["note"]


def test_identical_cases_from_two_requirements_are_stored_once(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    generate(
        client, h, w
    )  # FR-1 and FR-5 are both mapped to the Items page and its form
    required = [
        c
        for c in cases(client, h, w)
        if c["title"] == "Title is required on 'Add Item'"
    ]
    assert len(required) == 1 and required[0]["req_key"] == "FR-1"
    assert required[0]["also_covers"] == ["FR-5"]


def test_regeneration_replaces_drafts_but_keeps_reviewed_cases_and_never_reuses_keys(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    generate(client, h, w)
    first = cases(client, h, w)
    keep = next(c for c in first if c["title"] == "Title is required on 'Add Item'")
    r = client.patch(
        f"{API}/tests/{keep['id']}/status", headers=h, json={"status": "approved"}
    )
    assert r.status_code == 200
    generate(client, h, w)
    second = cases(client, h, w)
    ids = {c["id"] for c in second}
    assert keep["id"] in ids  # the approved case is untouched
    titles = [c["title"] for c in second]
    assert (
        titles.count("Title is required on 'Add Item'") == 1
    )  # not duplicated by the re-run
    assert len({c["tc_key"] for c in second}) == len(second)
    new_keys = {c["tc_key"] for c in second if c["id"] != keep["id"]}
    assert not (new_keys & {c["tc_key"] for c in first if c["id"] != keep["id"]})


def test_generation_still_produces_rule_cases_when_the_author_fails(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    AUTHOR["fail"] = True
    summary = generate(client, h, w)
    assert summary["author_failed"] >= 1 and summary["by_origin"]["llm"] == 0
    assert summary["by_origin"]["rule"] > 10


def test_test_types_can_be_switched_off(
    request: pytest.FixtureRequest,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    request.getfixturevalue("world")
    h = superuser_token_headers
    p = make_project(client, h, generation_config={"test_types": ["boundary"]})
    w = seed(p["id"])
    try:
        generate(client, h, w)
        assert {c["test_type"] for c in cases(client, h, w)} == {"boundary"}
    finally:
        client.delete(f"{API}/projects/{p['id']}", headers=h)


def test_generation_needs_mapping_and_is_private_to_the_owner(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    p = make_project(client, superuser_token_headers)
    try:
        r = client.post(
            f"{API}/projects/{p['id']}/tests/generate",
            headers=superuser_token_headers,
            json={},
        )
        assert r.status_code == 409 and "mapping" in r.json()["detail"]
    finally:
        client.delete(f"{API}/projects/{p['id']}", headers=superuser_token_headers)
    w = world()
    generate(client, superuser_token_headers, w)
    one = cases(client, superuser_token_headers, w)[0]
    assert (
        client.get(
            f"{API}/tests/{one['id']}", headers=normal_user_token_headers
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{API}/tests/{one['id']}/status",
            headers=normal_user_token_headers,
            json={"status": "rejected"},
        ).status_code
        == 403
    )
    s = client.get(
        f"{API}/projects/{w.project_id}/tests/summary", headers=superuser_token_headers
    ).json()
    assert (
        s["total"] > 0
        and s["requirements_covered"] == 2
        and s["requirements_total"] == 6
    )
    with Session(engine) as sess:
        assert sess.exec(select(TC).where(TC.id == uuid.UUID(one["id"]))).first()


def test_a_role_whose_crawl_did_not_finish_is_never_said_to_lack_access(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    with Session(engine) as s:
        snap = s.get(AppSnapshot, w.snapshot_id)
        assert snap is not None
        snap.stats = {"roles": {"Admin": "succeeded", "User": "budget_exceeded"}}
        s.add(snap)
        s.commit()
    generate(client, h, w)
    titles = {c["title"] for c in cases(client, h, w, test_type="role_access")}
    assert "User cannot open 'Admin'" not in titles  # its crawl stopped early: unproven
    assert "Admin can open 'Admin'" in titles


def test_a_form_behind_a_row_menu_is_reached_through_the_menu_and_needs_a_row(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    with Session(engine) as s:
        items = s.get(UiState, w.states["items"])
        assert items is not None
        menu = UiState(
            snapshot_id=w.snapshot_id,
            fingerprint="row-menu",
            kind="menu",
            title="Items row actions",
            route_template="/items",
        )
        edit = UiState(
            snapshot_id=w.snapshot_id,
            fingerprint="edit-item",
            kind="modal",
            title="Edit Item",
            route_template="/items",
        )
        s.add_all([menu, edit])
        s.flush()
        s.add(
            UiElement(
                state_id=items.id,
                key="button|Row actions|row",
                kind="button",
                label="Row actions",
                in_row=True,
            )
        )
        s.add(
            UiElement(
                state_id=menu.id,
                key="menuitem|Edit Item|0",
                kind="menuitem",
                label="Edit Item",
            )
        )
        s.add(
            Transition(
                snapshot_id=w.snapshot_id,
                from_state_id=items.id,
                to_state_id=menu.id,
                via_label="Row actions",
                kind="overlay",
            )
        )
        s.add(
            Transition(
                snapshot_id=w.snapshot_id,
                from_state_id=menu.id,
                to_state_id=edit.id,
                via_label="Edit Item",
                kind="overlay",
            )
        )
        form = UiForm(state_id=edit.id, name="Edit Item", submit_label="Update")
        s.add(form)
        s.flush()
        s.add(FormField(form_id=form.id, label="Name", required=True, ordinal=0))
        s.commit()
    generate(client, h, w)
    got = [
        c
        for c in cases(client, h, w)
        if c["title"] == "Name is required on 'Edit Item'"
    ]
    assert got, [c["title"] for c in cases(client, h, w)][:20]
    steps = got[0]["rendered_steps"]
    assert steps[0] == "1. Open the application"
    assert (
        "2. Click the 'Row actions' button" in steps
        and "3. Click the 'Edit Item' menuitem" in steps
    )
    assert got[0]["evidence"]["needs_rows"] is True
    assert any("At least one row exists" in p for p in got[0]["preconditions"])


def test_author_steps_on_a_dialog_open_it_first_and_unreachable_screens_drop_the_case() -> (
    None
):
    page = card({"items", "add item"})
    dialog = card({"title", "save"}, fields={"title"})
    dialog.title, dialog.path = "Add Item", [("Items", "link"), ("Add Item", "button")]
    lost = card({"orphan"})
    lost.title, lost.path = "Orphan", None
    res = AuthorResult(
        cases=[
            AuthorCase(
                title="fill the dialog",
                test_type="positive",
                steps=[
                    AuthorStep(action="fill", screen="S2", target="Title", value="x"),
                    AuthorStep(action="click", screen="S2", target="Save"),
                    AuthorStep(
                        action="verify", screen="S2", expect="the dialog closes"
                    ),
                ],
                expected_result="The item is saved",
            ),
            AuthorCase(
                title="uses an orphan",
                test_type="positive",
                steps=[AuthorStep(action="click", screen="S3", target="Orphan")],
                expected_result="never reached",
            ),
        ]
    )
    good, problems = ground(res, "FR-1", [page, dialog, lost], set(), 5, "Items")
    assert [d.title for d in good] == ["fill the dialog"]
    assert good[0].rendered()[:3] == [
        "1. Open the application at its start page (Items)",
        "2. Click the 'Items' link",
        "3. Click the 'Add Item' button",
    ]
    assert len(problems) == 1 and "cannot be reached" in problems[0]


def test_an_author_click_that_opens_a_dialog_is_not_repeated_by_the_added_path() -> (
    None
):
    page = card({"items", "add item"})
    dialog = card({"title", "save"}, fields={"title"})
    dialog.title, dialog.path = "Add Item", [("Items", "link"), ("Add Item", "button")]
    res = AuthorResult(
        cases=[
            AuthorCase(
                title="open then fill",
                test_type="positive",
                steps=[
                    AuthorStep(action="click", screen="S1", target="Add Item"),
                    AuthorStep(action="fill", screen="S2", target="Title", value="x"),
                ],
                expected_result="The item can be filled in",
            )
        ]
    )
    good, _ = ground(res, "FR-1", [page, dialog], set(), 5, "")
    assert [s for s in good[0].rendered() if "Add Item" in s] == [
        "3. Click the 'Add Item' button"
    ]


def test_a_page_with_no_recorded_clicks_is_opened_by_its_address_and_its_dialogs_follow(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    with Session(engine) as s:
        # no menu click to this page was ever recorded (a single-page app navigated by address)
        lonely = UiState(
            snapshot_id=w.snapshot_id,
            fingerprint="lonely",
            kind="page",
            title="Wallet",
            heading="Wallet",
            route_template="#/wallet",
            depth=1,
        )
        modal = UiState(
            snapshot_id=w.snapshot_id,
            fingerprint="topup",
            kind="modal",
            title="Top up",
            route_template="#/wallet",
            depth=2,
        )
        s.add_all([lonely, modal])
        s.flush()
        s.add(
            UiElement(
                state_id=lonely.id, key="button|Top up|0", kind="button", label="Top up"
            )
        )
        s.add(
            Transition(
                snapshot_id=w.snapshot_id,
                from_state_id=lonely.id,
                to_state_id=modal.id,
                via_label="Top up",
                kind="overlay",
            )
        )
        form = UiForm(state_id=modal.id, name="Top up", submit_label="Pay")
        s.add(form)
        s.flush()
        s.add(
            FormField(
                form_id=form.id,
                label="Amount",
                input_type="number",
                required=True,
                min_value="5",
                max_value="100",
                ordinal=0,
            )
        )
        s.add(
            RequirementMapping(
                project_id=uuid.UUID(w.project_id),
                requirement_id=w.reqs["FR-3"],
                snapshot_id=w.snapshot_id,
                state_id=lonely.id,
                confidence=0.9,
                rationale="seeded",
            )
        )
        s.commit()
    generate(client, h, w)
    got = [
        c for c in cases(client, h, w) if c["title"] == "Amount is required on 'Top up'"
    ]
    assert got, [c["title"] for c in cases(client, h, w)]
    assert got[0]["rendered_steps"][:2] == [
        "1. Open the page '#/wallet' of the application",
        "2. Click the 'Top up' button",
    ]


def test_a_full_run_clears_drafts_of_requirements_that_can_no_longer_be_generated(
    world: Callable[[], World],
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    h, w = superuser_token_headers, world()
    generate(client, h, w)
    assert any(c["req_key"] == "FR-2" for c in cases(client, h, w))
    with Session(engine) as s:  # FR-2's mapping is withdrawn
        m = s.exec(
            select(RequirementMapping).where(
                RequirementMapping.requirement_id == w.reqs["FR-2"]
            )
        ).one()
        m.review_status = "rejected"
        s.add(m)
        s.commit()
    generate(client, h, w)
    after = cases(client, h, w)
    assert not any(c["req_key"] == "FR-2" for c in after)  # no orphaned drafts
    assert any(c["req_key"] == "FR-1" for c in after)


def test_a_heading_is_not_a_control_the_author_may_press_or_type_in() -> None:
    c = card(
        {"items", "add item"}
    )  # "items" is the page heading on the card, not a control
    c.kinds.pop("items")
    res = AuthorResult(
        cases=[
            AuthorCase(
                title="press a heading",
                test_type="positive",
                steps=[step("click", "Items")],
                expected_result="nothing happens",
            ),
            AuthorCase(
                title="type into a button",
                test_type="positive",
                steps=[step("fill", "Add Item", value="x")],
                expected_result="text entered",
            ),
            AuthorCase(
                title="fine",
                test_type="positive",
                steps=[step("click", "Add Item")],
                expected_result="a dialog opens",
            ),
        ]
    )
    good, problems = ground(res, "FR-1", [c], set(), 5)
    assert [d.title for d in good] == ["fine"] and len(problems) == 2
