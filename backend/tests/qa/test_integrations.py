"""Test-management integrations: connectors against mocked platform APIs, the push (create / update /
unchanged / dry run / failure), and the per-user credential endpoints. No real Jira, TestRail or Zephyr is
contacted: the HTTP layer is replaced, so the *shape* of each request is what these tests pin down."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.qa.integrations.base import CasePayload, IntegrationError
from app.qa.integrations.jira import Jira
from app.qa.integrations.push import push_cases
from app.qa.integrations.testrail import TestRail
from app.qa.integrations.zephyr import Zephyr
from app.qa.models import (
    AppSnapshot,
    Project,
    Requirement,
    TestCase,
    TestCaseExternal,
    UserIntegration,
)
from app.qa.security.crypto import encrypt_json
from tests.qa.test_api_projects import API

SECRET = "tok-SECRET-123"


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )


CASE = CasePayload(
    tc_key="TC-0001",
    title="Sign in with a wrong password",
    requirement="FR-3",
    priority="P1",
    test_type="negative",
    module="Sign in",
    role="Admin",
    preconditions=["The tester is signed out"],
    steps=[
        "1. Open the page '/login'",
        "2. Enter x in the 'Password' field",
        "3. Click the 'Sign in' button",
    ],
    expected="An error is shown",
    basis="observed",
)


class Recorder:
    def __init__(self, handler: Any) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def client(self) -> httpx.Client:
        def h(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            return self._handler(req)

        return httpx.Client(transport=httpx.MockTransport(h))

    def bodies(self) -> list[Any]:
        return [json.loads(r.content) for r in self.requests if r.content]


# --- connectors -----------------------------------------------------------------------------------


def test_testrail_creates_a_case_with_separate_steps_and_basic_auth() -> None:
    rec = Recorder(lambda r: httpx.Response(200, json={"id": 77}))
    tr = TestRail("https://x.testrail.io", "qa@x.io", SECRET, client=rec.client())
    out = tr.create({"section_id": 5}, CASE)
    req = rec.requests[0]
    assert out.external_id == "77" and out.url.endswith("/index.php?/cases/view/77")
    assert str(req.url) == "https://x.testrail.io/index.php?/api/v2/add_case/5"
    assert req.headers["authorization"].startswith("Basic ")
    body = rec.bodies()[0]
    assert (
        body["title"] == "[TC-0001] Sign in with a wrong password"
        and body["priority_id"] == 4
    )
    assert body["refs"] == "FR-3"
    steps = body["custom_steps_separated"]
    assert [s["content"] for s in steps][0] == "Open the page '/login'"
    assert steps[-1]["expected"] == "An error is shown" and steps[0]["expected"] == ""


def test_testrail_falls_back_to_text_steps_for_a_text_template() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if b"custom_steps_separated" in req.content:
            return httpx.Response(
                400,
                json={"error": "Field :custom_steps_separated is not a valid field"},
            )
        return httpx.Response(200, json={"id": 8})

    rec = Recorder(handler)
    out = TestRail("https://x.testrail.io", "u", SECRET, client=rec.client()).create(
        {"section_id": 1}, CASE
    )
    assert out.external_id == "8"
    assert "custom_steps" in rec.bodies()[1] and "custom_expected" in rec.bodies()[1]


def test_testrail_options_handle_both_list_shapes() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "get_projects" in str(req.url):
            return httpx.Response(
                200, json={"offset": 0, "projects": [{"id": 1, "name": "Shop"}]}
            )
        return httpx.Response(200, json=[{"id": 4, "name": "Master"}])

    tr = TestRail(
        "https://x.testrail.io", "u", SECRET, client=Recorder(handler).client()
    )
    assert tr.options("projects") == [{"id": "1", "name": "Shop"}]
    assert tr.options("suites", "1") == [{"id": "4", "name": "Master"}]


def test_jira_creates_an_issue_and_retries_without_priority_when_the_screen_lacks_it() -> (
    None
):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and b'"priority"' in req.content:
            return httpx.Response(
                400, json={"errors": {"priority": "Field 'priority' cannot be set."}}
            )
        return httpx.Response(201, json={"key": "QA-12"})

    rec = Recorder(handler)
    jira = Jira("https://x.atlassian.net", "qa@x.io", SECRET, client=rec.client())
    out = jira.create({"project_key": "QA", "issue_type": "Task"}, CASE)
    assert (
        out.external_id == "QA-12" and out.url == "https://x.atlassian.net/browse/QA-12"
    )
    first, second = rec.bodies()
    assert (
        first["fields"]["priority"] == {"name": "Highest"}
        and "priority" not in second["fields"]
    )
    f = second["fields"]
    assert f["project"] == {"key": "QA"} and f["issuetype"] == {"name": "Task"}
    assert f["summary"].startswith("[TC-0001]") and "*Steps*" in f["description"]
    assert "# Open the page '/login'" in f["description"]
    assert "qa-agent" in f["labels"] and "FR-3" in f["labels"]


def test_zephyr_creates_a_case_then_writes_its_steps_with_bearer_auth() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 1, "key": "QA-T7"})

    rec = Recorder(handler)
    z = Zephyr(
        "", "", SECRET, {"jira_site": "https://x.atlassian.net"}, client=rec.client()
    )
    out = z.create({"project_key": "QA", "folder_id": "9"}, CASE)
    assert out.external_id == "QA-T7" and "testCase/QA-T7" in out.url
    case_req, steps_req = rec.requests
    assert str(case_req.url) == "https://api.zephyrscale.smartbear.com/v2/testcases"
    assert case_req.headers["authorization"] == f"Bearer {SECRET}"
    assert str(steps_req.url).endswith("/testcases/QA-T7/teststeps")
    body, steps = rec.bodies()
    assert (
        body["projectKey"] == "QA"
        and body["folderId"] == 9
        and body["priorityName"] == "High"
    )
    assert (
        steps["mode"] == "OVERWRITE"
        and steps["items"][-1]["inline"]["expectedResult"] == "An error is shown"
    )


@pytest.mark.parametrize(
    ("status", "text"),
    [(401, "refused the credentials"), (404, "HTTP 404"), (500, "HTTP 500")],
)
def test_errors_explain_themselves_and_never_leak_the_secret(
    status: int, text: str
) -> None:
    rec = Recorder(lambda r: httpx.Response(status, text=f"nope {SECRET}"))
    with pytest.raises(IntegrationError, match=text) as exc:
        Jira("https://x.atlassian.net", "u", SECRET, client=rec.client()).test()
    assert SECRET not in str(exc.value)


def test_rate_limits_are_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (
            httpx.Response(429, headers={"Retry-After": "1"})
            if calls["n"] < 3
            else httpx.Response(200, json={"displayName": "Ann"})
        )

    assert (
        "Ann"
        in Jira(
            "https://x.atlassian.net", "u", SECRET, client=Recorder(handler).client()
        ).test()
    )
    assert calls["n"] == 3


# --- the push ---------------------------------------------------------------------------------------


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    with Session(engine) as s:
        owner = s.exec(select(User)).first()
        assert owner
        proj = Project(
            owner_id=owner.id,
            name=f"push-{uuid.uuid4().hex[:6]}",
            target_base_url="http://x",
            environment_ack=True,
        )
        s.add(proj)
        s.flush()
        snap = AppSnapshot(project_id=proj.id, status="ready")
        req = Requirement(
            project_id=proj.id,
            req_key="FR-3",
            title="Sign in",
            description="d",
            type="functional",
        )
        s.add_all([snap, req])
        s.flush()
        cases = []
        for i, status in enumerate(["approved", "approved", "draft"], 1):
            c = TestCase(
                project_id=proj.id,
                tc_key=f"TC-000{i}",
                requirement_id=req.id,
                snapshot_id=snap.id,
                title=f"Case {i}",
                steps=[],
                rendered_steps=[f"1. Do thing {i}"],
                expected_result="ok",
                status=status,
                preconditions=[],
                evidence={"basis": "observed"},
            )
            s.add(c)
            cases.append(c)
        integ = UserIntegration(
            user_id=owner.id,
            platform="testrail",
            base_url="https://x.testrail.io",
            username="u",
            ciphertext=encrypt_json({"secret": SECRET}),
        )
        s.add(integ)
        s.commit()
        yield {"project": proj.id, "integ": integ.id, "cases": [c.id for c in cases]}
    with Session(engine) as s:
        p = s.get(Project, proj.id)
        i = s.get(UserIntegration, integ.id)
        if p:
            s.delete(p)
        if i:
            s.delete(i)
        s.commit()


def test_push_creates_then_updates_then_leaves_unchanged_cases_alone(
    world: dict[str, Any],
) -> None:
    ids = iter(range(100, 200))
    rec = Recorder(lambda r: httpx.Response(200, json={"id": next(ids)}))
    dest = {"project_id": 1, "suite_id": 2, "section_id": 3}
    with Session(engine) as s:
        integ = s.get(UserIntegration, world["integ"])
        assert integ
        dry = push_cases(
            s,
            integ,
            world["project"],
            dest,
            dry_run=True,
            include_drafts=False,
            client=rec.client(),
        )
        assert dry["counts"] == {"would create": 2} and not rec.requests, (
            "a dry run writes nothing"
        )

        first = push_cases(
            s,
            integ,
            world["project"],
            dest,
            dry_run=False,
            include_drafts=False,
            client=rec.client(),
        )
        assert first["counts"] == {"created": 2}, "the draft is not pushed"
        assert len(rec.requests) == 2

        again = push_cases(
            s,
            integ,
            world["project"],
            dest,
            dry_run=False,
            include_drafts=False,
            client=rec.client(),
        )
        assert again["counts"] == {"unchanged": 2} and len(rec.requests) == 2, (
            "nothing duplicated"
        )

        c = s.get(TestCase, world["cases"][0])
        assert c
        c.expected_result = "changed"
        s.add(c)
        s.commit()
        third = push_cases(
            s,
            integ,
            world["project"],
            dest,
            dry_run=False,
            include_drafts=False,
            client=rec.client(),
        )
        assert third["counts"] == {"updated": 1, "unchanged": 1}
        assert "update_case/" in str(rec.requests[-1].url)

        rows = s.exec(
            select(TestCaseExternal).where(TestCaseExternal.platform == "testrail")
        ).all()
        assert len([r for r in rows if r.test_case_id in world["cases"]]) == 2

        other = {
            "project_id": 1,
            "suite_id": 2,
            "section_id": 99,
        }  # a different destination is a new push
        fresh = push_cases(
            s,
            integ,
            world["project"],
            other,
            dry_run=False,
            include_drafts=True,
            client=rec.client(),
        )
        assert fresh["counts"] == {"created": 3}


def test_a_failing_case_is_reported_and_does_not_stop_the_others(
    world: dict[str, Any],
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(400, json={"error": "Field :section_id is not valid"})
            if "add_case/3" in str(req.url) and b"Case 2" in req.content
            else httpx.Response(200, json={"id": 5})
        )

    rec = Recorder(handler)
    with Session(engine) as s:
        integ = s.get(UserIntegration, world["integ"])
        assert integ
        res = push_cases(
            s,
            integ,
            world["project"],
            {"section_id": 3},
            dry_run=False,
            include_drafts=False,
            client=rec.client(),
        )
    assert res["counts"] == {"created": 1, "failed": 1}
    failed = [c for c in res["cases"] if c["action"] == "failed"][0]
    assert "HTTP 400" in failed["error"] and SECRET not in failed["error"]


# --- the credential endpoints --------------------------------------------------------------------------


def test_credentials_are_per_user_write_only_and_validated(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    client.delete(f"{API}/integrations/jira", headers=h)
    listed = client.get(f"{API}/integrations/", headers=h).json()
    assert {x["platform"] for x in listed} == {"jira", "testrail", "zephyr"}
    assert not any(x["connected"] for x in listed if x["platform"] == "jira")

    bad = client.put(
        f"{API}/integrations/jira",
        headers=h,
        json={
            "base_url": "http://x.atlassian.net",
            "username": "a@b.c",
            "secret": SECRET,
        },
    )
    assert bad.status_code == 422 and "https" in bad.json()["detail"]
    private = client.put(
        f"{API}/integrations/jira",
        headers=h,
        json={"base_url": "https://10.0.0.5", "username": "a@b.c", "secret": SECRET},
    )
    assert private.status_code == 422
    assert (
        client.put(
            f"{API}/integrations/jira",
            headers=h,
            json={"base_url": "https://x.atlassian.net", "username": "a@b.c"},
        ).status_code
        == 422
    )

    ok = client.put(
        f"{API}/integrations/jira",
        headers=h,
        json={
            "base_url": "https://x.atlassian.net/",
            "username": "a@b.c",
            "secret": SECRET,
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (
        body["connected"]
        and body["base_url"] == "https://x.atlassian.net"
        and SECRET not in json.dumps(body)
    )
    assert SECRET not in json.dumps(
        client.get(f"{API}/integrations/", headers=h).json()
    )

    keep = client.put(
        f"{API}/integrations/jira",
        headers=h,
        json={"base_url": "https://x.atlassian.net", "username": "new@b.c"},
    )
    assert keep.status_code == 200 and keep.json()["username"] == "new@b.c", (
        "omitting the secret keeps the saved one"
    )
    with Session(engine) as s:
        row = s.exec(
            select(UserIntegration).where(UserIntegration.platform == "jira")
        ).first()
        assert row and SECRET not in row.ciphertext and row.ciphertext, (
            "stored encrypted"
        )

    assert client.delete(f"{API}/integrations/jira", headers=h).status_code == 200
    assert client.post(f"{API}/integrations/jira/test", headers=h).status_code == 409


def test_integrations_require_login(client: TestClient) -> None:
    assert client.get(f"{API}/integrations/").status_code in {401, 403}
