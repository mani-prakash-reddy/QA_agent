from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings


def postgres_dsn() -> str:
    """psycopg DSN (the app's SQLAlchemy URL carries a '+psycopg' driver suffix)."""
    return str(settings.DATABASE_URL).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


async def ensure_checkpoint_tables() -> None:
    """One-time schema setup. Call at worker startup / DB provisioning, never per run:
    it issues CREATE INDEX CONCURRENTLY, which waits for every open transaction."""
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
        await saver.setup()


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """LangGraph checkpoint store so an interrupted run's agent state is recoverable."""
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
        yield saver
