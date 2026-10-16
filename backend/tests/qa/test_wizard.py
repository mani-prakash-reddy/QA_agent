"""Multi-step forms: with the option on, the crawler fills each step with valid sample values and presses
Next/Continue (never the final button), records what it typed on the transition, and test paths replay it."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.qa.generation.rules import open_steps
from app.qa.mapping.paths import build_planner
from app.qa.models import (
    AppRole,
    AppSnapshot,
    Project,
    RoleCredential,
    Run,
    Transition,
    UiElement,
    UiState,
)
from app.qa.runs.executor import execute_run
from app.qa.security.crypto import encrypt_json
from tests.qa.conftest import _chromium_available
from tests.qa.fixture_app import USERS, FixtureServer

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def run_crawl(walk: bool, name: str, tmp: Any) -> dict[str, Any]:
    mp = pytest.MonkeyPatch()
    mp.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    mp.setattr(settings, "ARTIFACT_DIR", str(tmp))
    server = FixtureServer().start()
    with Session(engine) as s:
        owner = s.exec(select(User)).first()
        assert owner
        p = Project(
            owner_id=owner.id,
            name=name,
            target_base_url=server.url,
            environment_ack=True,
            crawl_config={
                "max_depth": 2,
                "walk_wizards": walk,
                "probe_forbidden_routes": False,
            },
        )
        s.add(p)
        s.flush()
        r = AppRole(project_id=p.id, name="Admin", login_recipe={})
        s.add(r)
        s.flush()
        s.add(
            RoleCredential(
                role_id=r.id,
                ciphertext=encrypt_json(
                    {
                        "username": "admin@fixture.test",
                        "password": USERS["admin@fixture.test"][0],
                    }
                ),
            )
        )
        run = Run(project_id=p.id, kind="crawl", config_snapshot={})
        s.add(run)
        s.commit()
        run_id, project_id = run.id, p.id

    async def go() -> str:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return await execute_run(run_id, redis)
        finally:
            await redis.aclose()

    status = asyncio.run(go())
    with Session(engine) as s:
        snap = s.exec(select(AppSnapshot).where(AppSnapshot.run_id == run_id)).one()
        out = {
            "status": status,
            "snap": snap.id,
            "server": server,
            "project_id": project_id,
            "mp": mp,
        }
    return out


def cleanup(w: dict[str, Any]) -> None:
    w["server"].stop()
    with Session(engine) as s:
        pr = s.get(Project, w["project_id"])
        if pr:
            s.delete(pr)
            s.commit()
    w["mp"].undo()


@pytest.fixture(scope="module")
def walked(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    if not _chromium_available():
        pytest.skip("chromium is not installed")
    w = run_crawl(True, "fixture-wizard-on", tmp_path_factory.mktemp("a"))
    yield w
    cleanup(w)


@pytest.fixture(scope="module")
def not_walked(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    if not _chromium_available():
        pytest.skip("chromium is not installed")
    w = run_crawl(False, "fixture-wizard-off", tmp_path_factory.mktemp("b"))
    yield w
    cleanup(w)


def booking_states(snap: Any) -> list[UiState]:
    with Session(engine) as s:
        sts = s.exec(
            select(UiState).where(
                UiState.snapshot_id == snap, UiState.title == "Booking"
            )
        ).all()
        if not sts:
            sts = s.exec(
                select(UiState).where(
                    UiState.snapshot_id == snap, UiState.route_template == "/wizard"
                )
            ).all()
        return list(sts)


def test_the_steps_are_walked_but_the_final_button_is_never_pressed(
    walked: dict[str, Any],
) -> None:
    assert walked["status"] == "succeeded"
    sts = booking_states(walked["snap"])
    assert len(sts) == 3, [(s.title, s.fingerprint[:6]) for s in sts]
    with Session(engine) as s:
        labels = {
            e.label
            for st in sts
            for e in s.exec(select(UiElement).where(UiElement.state_id == st.id))
        }
    assert {"Next", "Continue", "Confirm booking"} <= labels, labels
    server: FixtureServer = walked["server"]
    assert server.wizard_posts == 2, "one step request per Next / Continue"
    assert server.mutations == [], "Confirm booking was never pressed"


def test_what_was_typed_is_recorded_on_the_transition(walked: dict[str, Any]) -> None:
    with Session(engine) as s:
        ts = [
            t
            for t in s.exec(
                select(Transition).where(Transition.snapshot_id == walked["snap"])
            )
            if t.fills
        ]
    typed = {(f["kind"], f["label"], f["value"]) for t in ts for f in t.fills}
    assert ("fill", "Guest name", "Sample") in typed
    assert ("fill", "Nights", "2") in typed, "the field's minimum is respected"
    assert ("select", "Room type", "Single") in typed, (
        "the placeholder option is skipped"
    )


def test_test_paths_replay_the_typed_steps(walked: dict[str, Any]) -> None:
    with Session(engine) as s:
        planner = build_planner(s, walked["snap"])
        last = next(
            st
            for st in booking_states(walked["snap"])
            if any(
                e.label == "Confirm booking"
                for e in s.exec(select(UiElement).where(UiElement.state_id == st.id))
            )
        )
        planned = planner.plan(last.id)
    assert planned is not None
    kinds = [k for _l, k in planned[0]]
    assert kinds.count("fill") >= 2 and "select" in kinds
    steps = open_steps("", planned[0])
    actions = [
        (
            x.action,
            x.target.label if x.target else None,
            x.value.text if x.value else None,
        )
        for x in steps
    ]
    assert ("fill", "Guest name", "Sample") in actions and (
        "select",
        "Room type",
        "Single",
    ) in actions
    # fills come before the click that needs them
    assert actions.index(("fill", "Guest name", "Sample")) < next(
        i for i, x in enumerate(actions) if x[1] == "Next"
    )


def test_with_the_option_off_no_step_is_pressed(not_walked: dict[str, Any]) -> None:
    assert len(booking_states(not_walked["snap"])) == 1
    assert not_walked["server"].wizard_posts == 0
