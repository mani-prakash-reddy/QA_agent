"""Two roles crawl the fixture app; the role that cannot see /admin opens it by address and the
outcome is recorded as observed access."""

import asyncio
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.qa.crawler.access import classify
from app.qa.models import (
    AppRole,
    AppSnapshot,
    Project,
    RoleCredential,
    RoleStateAccess,
    Run,
    UiState,
)
from app.qa.runs.executor import execute_run
from app.qa.security.crypto import encrypt_json
from tests.qa.conftest import _chromium_available
from tests.qa.fixture_app import USERS, FixtureServer

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def test_classify() -> None:
    f = lambda status, req, final, heading="", text="": classify(  # noqa: E731
        status, req, final, heading, text, "/login"
    )
    assert f(403, "/a", "/a")[0] == "forbidden"
    assert f(404, "/a", "/a")[0] == "not_found"
    assert f(200, "/a", "/login")[0] == "redirected"
    assert f(200, "/a", "/home")[0] == "redirected"
    assert f(200, "/a", "/a", "Forbidden", "You do not have access")[0] == "forbidden"
    assert f(200, "/a", "/a", "Admin", "users table")[0] == "visible"
    assert f(200, "/a", "/a", "Reports", "Q3 report")[0] == "visible"


@pytest.fixture(scope="module")
def two_role_crawl(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[dict[str, object]]:
    if not _chromium_available():
        pytest.skip("chromium is not installed")
    mp = pytest.MonkeyPatch()
    mp.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    mp.setattr(settings, "ARTIFACT_DIR", str(tmp_path_factory.mktemp("artifacts")))
    server = FixtureServer().start()
    with Session(engine) as s:
        owner = s.exec(select(User)).first()
        assert owner
        project = Project(
            owner_id=owner.id,
            name="fixture-access-probe",
            target_base_url=server.url,
            environment_ack=True,
            crawl_config={"max_depth": 2},
        )
        s.add(project)
        s.flush()
        for name, email in (
            ("Admin", "admin@fixture.test"),
            ("User", "user@fixture.test"),
        ):
            r = AppRole(project_id=project.id, name=name, login_recipe={})
            s.add(r)
            s.flush()
            s.add(
                RoleCredential(
                    role_id=r.id,
                    ciphertext=encrypt_json(
                        {"username": email, "password": USERS[email][0]}
                    ),
                )
            )
        run = Run(project_id=project.id, kind="crawl", config_snapshot={})
        s.add(run)
        s.commit()
        run_id, project_id = run.id, project.id

    async def go() -> str:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return await execute_run(run_id, redis)
        finally:
            await redis.aclose()

    status = asyncio.run(go())
    with Session(engine) as s:
        snap = s.exec(select(AppSnapshot).where(AppSnapshot.run_id == run_id)).one()
        yield {
            "status": status,
            "snapshot": snap.id,
            "stats": dict(snap.stats),
            "server": server,
        }
    server.stop()
    with Session(engine) as s:
        p = s.get(Project, project_id)
        if p:
            s.delete(p)
            s.commit()
    mp.undo()


def test_forbidden_route_is_observed(two_role_crawl: dict[str, object]) -> None:
    assert two_role_crawl["status"] == "succeeded"
    with Session(engine) as s:
        roles = {r.id: r.name for r in s.exec(select(AppRole))}
        admin_state = s.exec(
            select(UiState).where(
                UiState.snapshot_id == two_role_crawl["snapshot"],
                UiState.title == "Admin",
            )
        ).one()
        rows = {
            roles[a.role_id]: a
            for a in s.exec(
                select(RoleStateAccess).where(
                    RoleStateAccess.state_id == admin_state.id
                )
            )
        }
    assert rows["Admin"].access == "visible"
    assert rows["User"].access == "forbidden", rows["User"]
    assert rows["User"].evidence
    probes = two_role_crawl["stats"]["access_probes"]  # type: ignore[index]
    assert probes["User"]["forbidden"] >= 1


def test_probing_never_mutates(two_role_crawl: dict[str, object]) -> None:
    server = two_role_crawl["server"]
    assert server.mutations == []  # type: ignore[attr-defined]


def test_generation_uses_the_observed_denial(two_role_crawl: dict[str, object]) -> None:
    from app.qa.generation.gather import role_access

    with Session(engine) as s:
        snap = s.get(AppSnapshot, two_role_crawl["snapshot"])
        assert snap
        admin_state = s.exec(
            select(UiState).where(
                UiState.snapshot_id == snap.id, UiState.title == "Admin"
            )
        ).one()
        ra = role_access(s, snap.id, snap.project_id)
    assert admin_state.id in ra.denied
    assert len(ra.denied[admin_state.id]) == 1
