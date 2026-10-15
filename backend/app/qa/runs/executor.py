"""Executes a queued run: status lifecycle, event publishing, secret scanning, error handling."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.qa.events import EventPublisher
from app.qa.models import Project, Run
from app.qa.runs.checkpoint import open_checkpointer
from app.qa.runs.context import RunContext
from app.qa.runs.control import RunControl, StopRequested
from app.qa.runs.kinds.crawl import run_crawl
from app.qa.runs.kinds.echo import run_echo
from app.qa.runs.kinds.ingest import run_ingest
from app.qa.runs.persist import persist_events
from app.qa.runs.status import status_from_agents
from app.qa.security.scanner import SecretScanner

logger = logging.getLogger(__name__)

RunHandler = Callable[[RunContext], Awaitable[dict[str, Any]]]
RUN_KINDS: dict[str, RunHandler] = {
    "echo": run_echo,
    "crawl": run_crawl,
    "ingest": run_ingest,
}


def app_secret_values() -> set[str]:
    """Secrets the process itself holds; they must never appear in prompts or events."""
    values = {
        s.get_secret_value()
        for s in (
            settings.LLM_API_KEY,
            settings.EMBEDDING_API_KEY,
            settings.CREDENTIAL_ENCRYPTION_KEY,
        )
        if s is not None
    }
    values |= {settings.SECRET_KEY, settings.FIRST_SUPERUSER_PASSWORD}
    return {v for v in values if v}


def _load(run_id: uuid.UUID) -> tuple[Run, Project] | None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            return None
        project = session.get(Project, run.project_id)
        assert project is not None
        session.expunge(run)
        session.expunge(project)
        return run, project


def _update_run(run_id: uuid.UUID, **fields: Any) -> None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        session.add(run)
        session.commit()


async def execute_run(run_id: uuid.UUID, redis: Redis) -> str:
    """Run to completion. Returns the final status."""
    loaded = await asyncio.to_thread(_load, run_id)
    if loaded is None:
        logger.warning("run %s not found", run_id)
        return "failed"
    run, project = loaded
    if run.status == "stopped":  # stopped while queued
        return "stopped"

    scanner = SecretScanner(app_secret_values())
    events = EventPublisher(redis, str(run_id), sink=persist_events, scanner=scanner)
    control = RunControl(redis, str(run_id))
    await events.start()
    await asyncio.to_thread(
        _update_run, run_id, status="running", started_at=datetime.now(UTC)
    )
    await events.emit("run_status", status="running", kind=run.kind)

    status, error, summary = "succeeded", None, {}
    try:
        handler = RUN_KINDS.get(run.kind)
        if handler is None:
            raise ValueError(f"unknown run kind '{run.kind}'")
        async with open_checkpointer() as saver:
            summary = await handler(
                RunContext(run, project, redis, events, control, scanner, saver)
            )
        await control.checkpoint()  # a stop issued during the final step still counts
    except StopRequested:
        status = "stopped"
    except Exception as exc:
        logger.exception("run %s failed", run_id)
        status, error = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
    if status == "succeeded" and summary.get("statuses"):
        status = status_from_agents(summary["statuses"])
        if status == "failed":
            error = f"agents ended with: {summary['statuses']}"

    await asyncio.to_thread(
        _update_run,
        run_id,
        status=status,
        error=error,
        summary=summary,
        finished_at=datetime.now(UTC),
    )
    # Persist everything *before* announcing completion: clients refetch agents/events when they
    # see the terminal event and must find them final.
    await events.flush()
    await events.emit(
        "run_status", status=status, error=error
    )  # terminal event ends SSE streams
    await events.aclose()
    return status
