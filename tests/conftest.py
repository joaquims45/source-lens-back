import os

import pytest
from sqlalchemy.orm import Session

from sourcelens.persistence.database import engine


@pytest.fixture
def db():
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 with migrated PostgreSQL and Redis")
    with engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, expire_on_commit=False) as session:
            yield session
        transaction.rollback()
