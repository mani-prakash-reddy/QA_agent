"""Asking a person for something a run cannot get alone (a one-time code from their phone).

The code is passed through Redis for a few minutes and read once; it is never put in an event."""

import asyncio

from redis.asyncio import Redis

from app.qa.events import EventPublisher
from app.qa.runs.control import RunControl, StopRequested

HUMAN_TTL_S = 600


def human_key(run_id: str, role: str) -> str:
    return f"run:{run_id}:human:{role}"


async def submit_human_value(redis: Redis, run_id: str, role: str, value: str) -> None:
    await redis.set(human_key(run_id, role), value, ex=HUMAN_TTL_S)


async def ask_human(
    redis: Redis,
    events: EventPublisher,
    control: RunControl,
    *,
    run_id: str,
    agent_id: str,
    role: str,
    prompt: str,
    timeout_s: float,
    poll_s: float = 0.5,
) -> str:
    """Emit `needs_human` and wait for the answer. Raises TimeoutError, or StopRequested if stopped."""
    from app.qa.browser.auth import AuthError

    await redis.delete(
        human_key(run_id, role)
    )  # an old answer must not satisfy a new question
    await events.emit(
        "needs_human",
        agent_id=agent_id,
        kind="otp",
        role=role,
        prompt=f"Enter the {prompt} for the '{role}' account",
        timeout_s=int(timeout_s),
    )
    waited = 0.0
    while waited < timeout_s:
        if await control.get(agent_id) == "stopped":
            raise StopRequested
        value = await redis.getdel(human_key(run_id, role))
        if value:
            return value if isinstance(value, str) else value.decode()
        await asyncio.sleep(poll_s)
        waited += poll_s
    raise AuthError(f"nobody entered the {prompt} within {int(timeout_s)} seconds")
