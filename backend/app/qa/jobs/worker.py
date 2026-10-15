"""arq worker: `arq app.qa.jobs.worker.WorkerSettings`"""

import uuid
from typing import Any

from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import settings
from app.qa.runs.checkpoint import ensure_checkpoint_tables
from app.qa.runs.executor import execute_run


async def startup(ctx: dict[str, Any]) -> None:
    await ensure_checkpoint_tables()
    ctx["redis_client"] = Redis.from_url(settings.REDIS_URL, decode_responses=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["redis_client"].aclose()


async def execute_run_job(ctx: dict[str, Any], run_id: str) -> str:
    return await execute_run(uuid.UUID(run_id), ctx["redis_client"])


class WorkerSettings:
    functions = [execute_run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    job_timeout = 4 * 3600
    keep_result = 3600
    allow_abort_jobs = True
