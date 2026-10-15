from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from tests.test_database import use_test_database

# Must run before app.core.db is imported: tests delete all users/items on teardown.
TEST_DB_NAME = use_test_database()

from app.core.config import settings  # noqa: E402
from app.core.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Item, User  # noqa: E402
from tests.utils.user import authentication_token_from_email  # noqa: E402
from tests.utils.utils import get_superuser_token_headers  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        yield session
        statement = delete(Item)
        session.execute(statement)
        statement = delete(User)
        session.execute(statement)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
