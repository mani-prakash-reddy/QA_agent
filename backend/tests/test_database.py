"""Provision an isolated `<db>_test` database for the test session.

The template's fixtures delete every user and item at session end, so tests must never run
against the development database. This module is imported by conftest.py *before* the app's
engine is created.
"""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.qa.runs.checkpoint import ensure_checkpoint_tables

TEST_SUFFIX = "_test"


def use_test_database() -> str:
    url = make_url(str(settings.DATABASE_URL))
    dev_name = url.database or "app"
    test_name = dev_name if dev_name.endswith(TEST_SUFFIX) else dev_name + TEST_SUFFIX
    test_url = url.set(database=test_name)

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_name}
        ).first()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{test_name}"'))
    admin.dispose()

    # Same search_path as the dev DB (see db/init-extensions.sql) so AGE cypher() works unqualified.
    bootstrap = create_engine(test_url, isolation_level="AUTOCOMMIT")
    with bootstrap.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS age"))
        conn.execute(
            text(
                f'ALTER DATABASE "{test_name}" SET search_path = "$user", public, ag_catalog'
            )
        )
    bootstrap.dispose()

    # Point the app at the test DB before app.core.db creates its engine.
    settings.DATABASE_URL = test_url.render_as_string(hide_password=False)  # type: ignore[assignment]

    command.upgrade(Config("alembic.ini"), "head")
    # Before any test session holds a transaction open (see ensure_checkpoint_tables).
    asyncio.run(ensure_checkpoint_tables())
    return test_name
