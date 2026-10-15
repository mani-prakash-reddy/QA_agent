"""Integration: real Postgres + Redis, scripted agents (no LLM, no browser)."""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from redis.asyncio import Redis
from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import User
from app.qa.models import AgentEvent, AgentRun, Project, Run
from app.qa.runs.control import RunControl
from app.qa.runs.executor import execute_run
from app.qa.runs.status import is_partial, status_from_agents


@pytest.fixture
def project(db: Session) -> Iterator[Project]:
    owner = db.exec(select(User)).first()
    assert owner
    p = Project(
        owner_id=owner.id,
        name="exec-test",
        target_base_url="http://localhost:5173",
        environment_ack=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    yield p
    db.delete(p)
    db.commit()


def new_run(db: Session, project: Project, **params: object) -> Run:
    run = Run(project_id=project.id, kind="echo", config_snapshot=dict(params))
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_status_from_agents() -> None:
    assert status_from_agents(["succeeded", "succeeded"]) == "succeeded"
    assert status_from_agents(["succeeded", "stopped"]) == "stopped"
    assert status_from_agents(["succeeded", "failed"]) == "failed"
    # a budget stop is a partial result, not a failure
    assert status_from_agents(["succeeded", "budget_exceeded"]) == "succeeded"
    assert is_partial(["succeeded", "budget_exceeded"]) and not is_partial(
        ["succeeded"]
    )


@pytest.mark.anyio
async def test_echo_run_end_to_end_persists_everything(
    db: Session, project: Project, redis: Redis
) -> None:
    run = new_run(db, project, agents=2, steps=3, delay=0.01)
    assert await execute_run(run.id, redis) == "succeeded"

    with Session(engine) as s:
        done = s.get(Run, run.id)
        assert (
            done and done.status == "succeeded" and done.started_at and done.finished_at
        )
        agents = s.exec(select(AgentRun).where(AgentRun.run_id == run.id)).all()
        events = s.exec(
            select(AgentEvent)
            .where(AgentEvent.run_id == run.id)
            .order_by(col(AgentEvent.seq))
        ).all()

    kinds = sorted(a.kind for a in agents)
    assert kinds == ["explorer", "explorer", "supervisor"]
    assert all(a.status == "succeeded" for a in agents)
    supervisor = next(a for a in agents if a.kind == "supervisor")
    assert all(
        a.parent_agent_id == supervisor.id for a in agents if a.kind == "explorer"
    )
    assert all(a.steps == 3 for a in agents if a.kind == "explorer")

    seqs = [e.seq for e in events]
    assert seqs == sorted(set(seqs)), "event seqs are unique and ordered"
    types = [e.type for e in events]
    assert types[0] == "run_status" and types[-1] == "run_status"
    assert events[-1].payload["status"] == "succeeded"
    assert types.count("tool_call") == 6 and types.count("agent_finished") == 3


@pytest.mark.anyio
async def test_stop_mid_run_stops_all_agents(
    db: Session, project: Project, redis: Redis
) -> None:
    run = new_run(db, project, agents=2, steps=50, delay=0.05)
    task = asyncio.create_task(execute_run(run.id, redis))
    await asyncio.sleep(0.6)
    await RunControl(redis, str(run.id)).set("stopped")
    assert await asyncio.wait_for(task, 10) == "stopped"
    with Session(engine) as s:
        assert s.get(Run, run.id).status == "stopped"  # type: ignore[union-attr]
        explorers = s.exec(
            select(AgentRun).where(
                AgentRun.run_id == run.id, AgentRun.kind == "explorer"
            )
        ).all()
    assert explorers and all(a.status == "stopped" for a in explorers)
    assert all(0 < a.steps < 50 for a in explorers)


@pytest.mark.anyio
async def test_pause_blocks_progress_and_resume_continues(
    db: Session, project: Project, redis: Redis
) -> None:
    run = new_run(db, project, agents=1, steps=6, delay=0.05)
    control = RunControl(redis, str(run.id))
    task = asyncio.create_task(execute_run(run.id, redis))
    await asyncio.sleep(0.4)
    await control.set("paused")
    await asyncio.sleep(0.4)  # let in-flight step settle
    before = int(await redis.get(f"run:{run.id}:seq") or 0)
    await asyncio.sleep(0.6)
    assert int(await redis.get(f"run:{run.id}:seq") or 0) == before, (
        "no events while paused"
    )
    assert not task.done()
    await control.set("running")
    assert await asyncio.wait_for(task, 10) == "succeeded"


@pytest.mark.anyio
async def test_unknown_run_kind_fails_cleanly(
    db: Session, project: Project, redis: Redis
) -> None:
    run = new_run(db, project)
    run.kind = "nope"
    db.add(run)
    db.commit()
    assert await execute_run(run.id, redis) == "failed"
    with Session(engine) as s:
        done = s.get(Run, run.id)
        assert (
            done
            and done.status == "failed"
            and "unknown run kind" in (done.error or "")
        )


@pytest.mark.anyio
async def test_missing_run_is_handled(redis: Redis) -> None:
    assert await execute_run(uuid.uuid4(), redis) == "failed"
