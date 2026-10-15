"""Durable copy of the live events, plus agent_run rows derived from agent lifecycle events."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from app.core.db import engine
from app.qa.events import Event
from app.qa.models import AgentEvent, AgentRun


def _as_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def _write_batch(events: list[Event]) -> None:
    with Session(engine) as session:
        rows: list[dict[str, Any]] = [
            {
                "run_id": uuid.UUID(e.run_id),
                "agent_id": e.agent_id,
                "seq": e.seq,
                "ts": e.ts,
                "type": e.type,
                # parent_id has no column; keep it so a replayed run rebuilds its agent tree
                "payload": {
                    **e.payload,
                    **({"parent_id": e.parent_id} if e.parent_id else {}),
                },
            }
            for e in events
        ]
        session.exec(
            insert(AgentEvent)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_agent_event_run_seq")
        )
        for e in events:
            agent_id = _as_uuid(e.agent_id)
            if agent_id is None:
                continue
            if e.type == "agent_spawned":
                spec = e.payload.get("spec", {})
                session.merge(
                    AgentRun(
                        id=agent_id,
                        run_id=uuid.UUID(e.run_id),
                        parent_agent_id=_as_uuid(e.parent_id),
                        kind=spec.get("kind", "unknown"),
                        spec=spec,
                        started_at=e.ts,
                    )
                )
            elif e.type == "agent_finished":
                agent = session.get(AgentRun, agent_id)
                if agent is None:
                    continue
                budget = e.payload.get("budget", {})
                agent.status = e.payload.get("status", "succeeded")
                agent.result = {
                    "result": e.payload.get("result", ""),
                    "detail": e.payload.get("detail", ""),
                }
                agent.steps = int(budget.get("steps", 0))
                agent.finished_at = (
                    e.ts if isinstance(e.ts, datetime) else datetime.now(UTC)
                )
                session.add(agent)
        session.commit()


async def persist_events(events: list[Event]) -> None:
    await asyncio.to_thread(_write_batch, events)
