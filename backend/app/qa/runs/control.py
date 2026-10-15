"""Run/agent control flags (pause / resume / stop), polled between agent steps."""

import asyncio
from typing import Literal

from redis.asyncio import Redis

ControlState = Literal["running", "paused", "stopped"]
CONTROL_TTL_SECONDS = 24 * 3600


class StopRequested(Exception):
    """Raised inside an agent when its run (or the agent itself) was stopped."""


def _key(run_id: str, agent_id: str | None) -> str:
    return f"run:{run_id}:control" + (f":agent:{agent_id}" if agent_id else "")


class RunControl:
    def __init__(self, redis: Redis, run_id: str, poll_interval: float = 0.25) -> None:
        self.redis, self.run_id, self.poll_interval = redis, run_id, poll_interval

    async def set(self, state: ControlState, agent_id: str | None = None) -> None:
        await self.redis.set(_key(self.run_id, agent_id), state, ex=CONTROL_TTL_SECONDS)

    async def get(self, agent_id: str | None = None) -> ControlState:
        # The run-level flag wins over the agent-level flag.
        for aid in (None, agent_id) if agent_id else (None,):
            value = await self.redis.get(_key(self.run_id, aid))
            if value:
                state = value.decode() if isinstance(value, bytes) else value
                if state != "running":
                    return state  # type: ignore[return-value]
        return "running"

    async def checkpoint(self, agent_id: str | None = None) -> None:
        """Call between steps: blocks while paused, raises StopRequested when stopped."""
        while True:
            state = await self.get(agent_id)
            if state == "stopped":
                raise StopRequested
            if state == "running":
                return
            await asyncio.sleep(self.poll_interval)
