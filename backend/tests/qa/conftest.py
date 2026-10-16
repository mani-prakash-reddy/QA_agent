import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User
from app.qa.agents.budget import BudgetTracker
from app.qa.agents.spec import AgentSpec, Budget
from app.qa.events import EventPublisher
from app.qa.gate.gate import AgentContext
from app.qa.models import AppRole, AppSnapshot, Project, RoleCredential, Run
from app.qa.runs.control import RunControl
from app.qa.runs.executor import execute_run
from app.qa.security.crypto import encrypt_json
from app.qa.security.scanner import SecretScanner
from tests.qa.fixture_app import USERS, FixtureServer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def redis() -> AsyncGenerator[Redis]:
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("redis is not available")
    yield client
    await client.aclose()


@pytest.fixture
def run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def make_ctx(redis: Redis, run_id: str):  # type: ignore[no-untyped-def]
    def _make(
        tools: list[str],
        *,
        budget: Budget | None = None,
        secrets: tuple[str, ...] = (),
        agent_id: str = "agent-1",
    ) -> AgentContext:
        scanner = SecretScanner(secrets)
        spec = AgentSpec(
            kind="explorer", goal="test", tools=tools, budget=budget or Budget()
        )
        return AgentContext(
            run_id=run_id,
            agent_id=agent_id,
            spec=spec,
            budget=BudgetTracker(spec.budget),
            events=EventPublisher(redis, run_id, scanner=scanner),
            control=RunControl(redis, run_id, poll_interval=0.02),
            scanner=scanner,
        )

    return _make


@dataclass
class Crawl:
    run_id: uuid.UUID
    snapshot_id: uuid.UUID
    status: str
    server: FixtureServer
    artifact_dir: Path
    project_id: uuid.UUID


def _chromium_available() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def crawl(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Crawl]:
    if not _chromium_available():
        pytest.skip("chromium is not installed (run: playwright install chromium)")
    mp = pytest.MonkeyPatch()
    mp.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    artifact_dir = tmp_path_factory.mktemp("artifacts")
    mp.setattr(settings, "ARTIFACT_DIR", str(artifact_dir))
    server = FixtureServer().start()

    with Session(engine) as s:
        owner = s.exec(select(User)).first()
        assert owner
        project = Project(
            owner_id=owner.id,
            name="fixture-crawl",
            target_base_url=server.url,
            environment_ack=True,
            crawl_config={"max_depth": 3, "max_concurrent_agents": 2},
        )
        s.add(project)
        s.flush()
        for email, password in [("admin@fixture.test", USERS["admin@fixture.test"][0])]:
            r = AppRole(project_id=project.id, name="Admin", login_recipe={})
            s.add(r)
            s.flush()
            s.add(
                RoleCredential(
                    role_id=r.id,
                    ciphertext=encrypt_json({"username": email, "password": password}),
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
        snapshot_id = snap.id
    yield Crawl(run_id, snapshot_id, status, server, artifact_dir, project_id)
    server.stop()
    with Session(engine) as s:
        p = s.get(Project, project_id)
        if p:
            s.delete(p)
            s.commit()
    mp.undo()
