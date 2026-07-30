import os

import psycopg
import pytest
from fastapi.testclient import TestClient

# Tests must never drop/recreate the development database used by a running H5.
TEST_DATABASE_NAME = os.getenv("TEST_DATABASE_NAME", "card_reward_test")
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    f"postgresql+psycopg://card_reward:card_reward@localhost:5432/{TEST_DATABASE_NAME}",
)
if not TEST_DATABASE_NAME.endswith("_test"):
    raise RuntimeError("Refusing to run destructive tests against a non-test database")

with psycopg.connect(
    "postgresql://card_reward:card_reward@localhost:5432/postgres", autocommit=True
) as connection:
    exists = connection.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE_NAME,)
    ).fetchone()
    if not exists:
        connection.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')

os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.db import Base, engine
from app.main import app
from app.seed import seed


@pytest.fixture(autouse=True)
def clean_database():
    assert engine.url.database.endswith("_test")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed()


@pytest.fixture
def client():
    return TestClient(app)
