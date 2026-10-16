import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from anyio import from_thread
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, SessionDep, TokenDep, authenticate_token
from app.api.routes.projects import get_owned_project
from app.core.config import settings
from app.core.db import engine
from app.models import Message, User
from app.qa.events import TERMINAL_RUN_STATUSES, EventPublisher, stream_key
from app.qa.models import AgentEvent, AgentRun, Run
from app.qa.runs.control import RunControl
from app.qa.runs.human import submit_human_value
from app.qa.runs.persist import persist_events
from app.qa.schemas import (
    AgentPublic,
    ControlRequest,
    EventPublic,
    HumanInput,
    RunCreate,
    RunPublic,
)

router = APIRouter(tags=["runs"])

LIVE_STATUSES = {"queued", "running", "paused", "waiting_human"}


def get_owned_run(session: Session, user: User, run_id: uuid.UUID) -> Run:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    get_owned_project(session, user, run.project_id)  # ownership via project
    return run


async def enqueue_run(run_id: uuid.UUID) -> str | None:
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        job = await pool.enqueue_job("execute_run_job", str(run_id))
        return job.job_id if job else None
    finally:
        await pool.aclose()


@router.post("/projects/{project_id}/runs", response_model=RunPublic)
def create_run(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: RunCreate,
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    run = Run(
        project_id=project.id,
        kind=body.kind,
        config_snapshot={
            **body.params,
            "crawl_config": project.crawl_config,
            "generation_config": project.generation_config,
            "budget_caps": project.budget_caps,
            "confidential": project.confidential,
        },
        created_by=current_user.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    run.arq_job_id = from_thread.run(enqueue_run, run.id)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("/projects/{project_id}/runs", response_model=list[RunPublic])
def list_runs(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> Any:
    get_owned_project(session, current_user, project_id)
    stmt = (
        select(Run)
        .where(Run.project_id == project_id)
        .order_by(col(Run.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    return session.exec(stmt).all()


@router.get("/runs/{run_id}", response_model=RunPublic)
def read_run(session: SessionDep, current_user: CurrentUser, run_id: uuid.UUID) -> Any:
    return get_owned_run(session, current_user, run_id)


@router.get("/runs/{run_id}/agents", response_model=list[AgentPublic])
def list_agents(
    session: SessionDep, current_user: CurrentUser, run_id: uuid.UUID
) -> Any:
    get_owned_run(session, current_user, run_id)
    return session.exec(
        select(AgentRun)
        .where(AgentRun.run_id == run_id)
        .order_by(col(AgentRun.started_at))
    ).all()


@router.get("/runs/{run_id}/events", response_model=list[EventPublic])
def list_events(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
    after_seq: int = 0,
    limit: int = Query(default=500, le=5000),
    agent_id: str | None = None,
) -> Any:
    get_owned_run(session, current_user, run_id)
    stmt = select(AgentEvent).where(
        AgentEvent.run_id == run_id, AgentEvent.seq > after_seq
    )
    if agent_id:
        stmt = stmt.where(AgentEvent.agent_id == agent_id)
    return session.exec(stmt.order_by(col(AgentEvent.seq)).limit(limit)).all()


@router.post("/runs/{run_id}/control", response_model=Message)
def control_run(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
    body: ControlRequest,
) -> Any:
    run = get_owned_run(session, current_user, run_id)
    if run.status not in LIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Run is {run.status}")
    state = {"pause": "paused", "resume": "running", "stop": "stopped"}[body.action]
    from_thread.run(_apply_control, run_id, state, body.agent_id, current_user.id)
    if body.agent_id is None:
        if body.action == "stop" and run.status == "queued":
            run.status, run.finished_at = "stopped", datetime.now(UTC)  # never started
        elif body.action in {"pause", "resume"} and run.status in {"running", "paused"}:
            run.status = "paused" if body.action == "pause" else "running"
        session.add(run)
        session.commit()
    return Message(message=f"{body.action} requested")


async def _apply_control(
    run_id: uuid.UUID, state: str, agent_id: str | None, user_id: uuid.UUID
) -> None:
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await RunControl(redis, str(run_id)).set(state, agent_id)  # type: ignore[arg-type]
        # Persisted too: who paused/stopped a run is part of the audit trail.
        events = EventPublisher(redis, str(run_id), sink=persist_events)
        await events.emit(
            "agent_status" if agent_id else "run_status",
            agent_id=agent_id,
            status=f"{state}_requested",
            requested_by=str(user_id),
        )
        await events.aclose()
    finally:
        await redis.aclose()


@router.post("/runs/{run_id}/human-input", response_model=Message)
def human_input(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: uuid.UUID,
    body: HumanInput,
) -> Any:
    """Answer a `needs_human` prompt (a one-time code). The value goes to Redis, never to events."""
    run = get_owned_run(session, current_user, run_id)
    if run.status not in LIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Run is {run.status}")
    from_thread.run(_submit_human, run_id, body.role, body.value.get_secret_value())
    return Message(message="sent")


async def _submit_human(run_id: uuid.UUID, role: str, value: str) -> None:
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await submit_human_value(redis, str(run_id), role, value)
    finally:
        await redis.aclose()


def _authorize_stream(token: str, run_id: uuid.UUID) -> tuple[bool, str]:
    """Short-lived DB session: the stream itself must not pin a pooled connection."""
    with Session(engine) as session:
        user = authenticate_token(session, token)
        run = get_owned_run(session, user, run_id)
        return run.status in TERMINAL_RUN_STATUSES, str(run.id)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    request: Request,
    token: TokenDep,
    run_id: uuid.UUID,
    last_event_id: Annotated[str | None, Header()] = None,
    after: str | None = None,
) -> StreamingResponse:
    """Server-Sent Events. Resume with the `Last-Event-ID` header (or `?after=`).
    Browsers' EventSource cannot send Authorization headers; use a fetch-based client."""
    _, rid = await run_in_threadpool(_authorize_stream, token, run_id)

    async def gen() -> AsyncIterator[str]:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        cursor = last_event_id or after or "0-0"
        try:
            while not await request.is_disconnected():
                batch = await redis.xread(
                    {stream_key(rid): cursor}, block=2000, count=200
                )
                if not batch:
                    yield ": keepalive\n\n"
                    continue
                for _, entries in batch:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        data = fields["data"]
                        event = json.loads(data)
                        yield f"id: {entry_id}\nevent: {event['type']}\ndata: {data}\n\n"
                        if (
                            event["type"] == "run_status"
                            and event["payload"].get("status") in TERMINAL_RUN_STATUSES
                        ):
                            return
        finally:
            await redis.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
