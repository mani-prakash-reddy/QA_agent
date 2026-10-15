"""Live event pipeline: every agent/tool/LLM event goes to a Redis Stream (live) and Postgres (durable)."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.qa.security.scanner import SecretScanner

logger = logging.getLogger(__name__)

EventType = Literal[
    "run_status",
    "agent_spawned",
    "agent_status",
    "agent_finished",
    "llm_token",
    "llm_message",
    "llm_usage",
    "tool_call",
    "tool_result",
    "screenshot",
    "state_discovered",
    "form_recorded",
    "transition_recorded",
    "skipped",
    "blocked",
    "needs_human",
    "error",
    "metric",
    "document_parsed",
    "requirement_extracted",
]
# Streamed token deltas are live-only; everything else is also persisted.
LIVE_ONLY: set[str] = {"llm_token"}
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "stopped"}
STREAM_MAXLEN = 50_000


def stream_key(run_id: str) -> str:
    return f"run:{run_id}:events"


class Event(BaseModel):
    run_id: str
    agent_id: str | None = None
    parent_id: str | None = None
    seq: int
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


PersistSink = Callable[[list[Event]], Awaitable[None]]


class EventPublisher:
    def __init__(
        self,
        redis: Redis,
        run_id: str,
        *,
        sink: PersistSink | None = None,
        scanner: SecretScanner | None = None,
        flush_interval: float = 0.5,
        batch_size: int = 100,
    ) -> None:
        self.redis, self.run_id = redis, run_id
        self._sink, self._scanner = sink, scanner
        self._buffer: list[Event] = []
        self._flush_interval, self._batch_size = flush_interval, batch_size
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._sink and self._task is None:
            self._task = asyncio.create_task(self._flusher())

    async def aclose(self) -> None:
        # Graceful: let an in-flight write finish instead of cancelling it (a cancelled write
        # would drop a batch that was already taken from the buffer).
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        await self.flush()

    async def emit(
        self,
        type: EventType,
        *,
        agent_id: str | None = None,
        parent_id: str | None = None,
        **payload: Any,
    ) -> Event:
        seq = int(await self.redis.incr(f"run:{self.run_id}:seq"))
        if self._scanner is not None:
            # Redact before the event leaves the process (stream, DB, UI).
            payload = json.loads(self._scanner.redact(json.dumps(payload, default=str)))
        event = Event(
            run_id=self.run_id,
            agent_id=agent_id,
            parent_id=parent_id,
            seq=seq,
            type=type,
            payload=payload,
        )
        await self.redis.xadd(
            stream_key(self.run_id),
            {"data": event.model_dump_json()},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        if self._sink and type not in LIVE_ONLY:
            async with self._lock:
                self._buffer.append(event)
                full = len(self._buffer) >= self._batch_size
            if full:
                await self.flush()
        return event

    async def flush(self) -> None:
        if not self._sink:
            return
        # One writer at a time, taking the batch *inside* the lock: batches must reach the sink
        # in order (an agent_finished must never be written before its agent_spawned).
        async with self._flush_lock:
            async with self._lock:
                batch, self._buffer = self._buffer, []
            if batch:
                try:
                    await self._sink(batch)
                except asyncio.CancelledError:
                    async with self._lock:  # keep the events for the final flush
                        self._buffer = batch + self._buffer
                    raise
                except Exception:
                    logger.exception("failed to persist %d events", len(batch))

    async def _flusher(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), self._flush_interval)
            except TimeoutError:
                pass
            await self.flush()
