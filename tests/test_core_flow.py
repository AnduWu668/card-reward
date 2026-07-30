from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import CardBalance, CardType, QuotaKind, QuotaUsage, Rarity, Role, User
from app.seed import seed
from app.services import local_period_start


@pytest.fixture(autouse=True)
def clean_database():
    assert engine.url.database.endswith("_test")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed()


@pytest.fixture
def client():
    return TestClient(app)


def users_by_role():
    with SessionLocal() as session:
        users = session.scalars(select(User)).all()
        return {
            role: [str(user.id) for user in users if user.role == role]
            for role in Role
        }


def normal_card_id():
    with SessionLocal() as session:
        return str(
            session.scalar(select(CardType.id).where(CardType.rarity == Rarity.NORMAL).limit(1))
        )


def create_gift(client: TestClient, sender_id: str) -> str:
    response = client.post(
        "/api/v1/gift-links",
        headers={
            "X-Demo-User-Id": sender_id,
            "Idempotency-Key": f"create-{uuid4()}",
        },
        json={"card_type_id": normal_card_id()},
    )
    assert response.status_code == 201, response.text
    return response.json()["share_url"].rsplit("/", 1)[-1]


def claim(client: TestClient, token: str, recipient_id: str):
    return client.post(
        f"/api/v1/gift-links/{token}/claims",
        headers={
            "X-Demo-User-Id": recipient_id,
            "Idempotency-Key": f"claim-{uuid4()}",
        },
    )


def test_draw_is_idempotent_and_daily_limit_is_hard(client: TestClient):
    user_id = users_by_role()[Role.USER][0]
    headers = {"X-Demo-User-Id": user_id, "Idempotency-Key": "same-draw-key"}

    first = client.post("/api/v1/draws", headers=headers)
    replay = client.post("/api/v1/draws", headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert replay.json()["draws_remaining_today"] == 2

    assert client.post(
        "/api/v1/draws",
        headers={"X-Demo-User-Id": user_id, "Idempotency-Key": "draw-key-two"},
    ).status_code == 200
    assert client.post(
        "/api/v1/draws",
        headers={"X-Demo-User-Id": user_id, "Idempotency-Key": "draw-key-three"},
    ).status_code == 200
    exhausted = client.post(
        "/api/v1/draws",
        headers={"X-Demo-User-Id": user_id, "Idempotency-Key": "draw-key-four"},
    )
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "DRAW_LIMIT_REACHED"


def test_concurrent_claims_on_one_link_only_issue_one_card(client: TestClient):
    role_users = users_by_role()
    token = create_gift(client, role_users[Role.MASTER][0])
    recipients = role_users[Role.USER]

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda user_id: claim(client, token, user_id), recipients))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert next(response for response in responses if response.status_code == 409).json()["error"][
        "code"
    ] == "GIFT_ALREADY_CLAIMED"


def test_concurrent_links_compete_for_senders_last_quota(client: TestClient):
    role_users = users_by_role()
    sender_id = role_users[Role.MASTER][0]
    with SessionLocal.begin() as session:
        session.add(
            QuotaUsage(
                user_id=sender_id,
                kind=QuotaKind.GIFT_NORMAL,
                period_start=local_period_start(QuotaKind.GIFT_NORMAL),
                used=2,
            )
        )
    tokens = [create_gift(client, sender_id), create_gift(client, sender_id)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda pair: claim(client, pair[0], pair[1]),
                zip(tokens, role_users[Role.USER], strict=True),
            )
        )

    assert sorted(response.status_code for response in responses) == [200, 429]
    failure = next(response for response in responses if response.status_code == 429)
    assert failure.json()["error"]["code"] == "SENDER_QUOTA_EXHAUSTED"
    with SessionLocal() as session:
        usage = session.get(
            QuotaUsage,
            (sender_id, QuotaKind.GIFT_NORMAL, local_period_start(QuotaKind.GIFT_NORMAL)),
        )
        assert usage.used == 3
        assert sum(session.scalars(select(CardBalance.quantity)).all()) >= 1
