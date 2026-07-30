from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import services
from app.db import SessionLocal
from app.models import (
    CardBalance,
    CardTransaction,
    CardType,
    GiftLink,
    GiftStatus,
    QuotaKind,
    QuotaUsage,
    Rarity,
    Role,
    User,
)


def users_by_role() -> dict[Role, list[str]]:
    with SessionLocal() as session:
        users = session.scalars(select(User)).all()
        return {
            role: [str(user.id) for user in users if user.role == role]
            for role in Role
        }


def card_ids() -> dict[Rarity, list[str]]:
    with SessionLocal() as session:
        cards = session.scalars(select(CardType).order_by(CardType.display_order)).all()
        return {
            rarity: [str(card.id) for card in cards if card.rarity == rarity]
            for rarity in Rarity
        }


def create_gift(
    client: TestClient,
    sender_id: str,
    card_type_id: str,
    *,
    key: str | None = None,
):
    return client.post(
        "/api/v1/gift-links",
        headers={
            "X-Demo-User-Id": sender_id,
            "Idempotency-Key": key or f"create-{uuid4()}",
        },
        json={"card_type_id": card_type_id},
    )


def token_from(response) -> str:
    assert response.status_code == 201, response.text
    return response.json()["share_url"].rsplit("/", 1)[-1]


def claim(client: TestClient, token: str, recipient_id: str, *, key: str | None = None):
    return client.post(
        f"/api/v1/gift-links/{token}/claims",
        headers={
            "X-Demo-User-Id": recipient_id,
            "Idempotency-Key": key or f"claim-{uuid4()}",
        },
    )


def gift_for_token(token: str) -> GiftLink:
    with SessionLocal() as session:
        gift = session.scalar(
            select(GiftLink).where(GiftLink.token_hash == services.token_hash(token))
        )
        assert gift is not None
        session.expunge(gift)
        return gift


def quota_used(user_id: str, kind: QuotaKind) -> int:
    with SessionLocal() as session:
        return (
            session.scalar(
                select(func.coalesce(func.sum(QuotaUsage.used), 0)).where(
                    QuotaUsage.user_id == user_id,
                    QuotaUsage.kind == kind,
                )
            )
            or 0
        )


def test_seed_data_and_card_pack_contract(client: TestClient):
    users = client.get("/api/v1/demo/users")
    assert users.status_code == 200
    assert len(users.json()) == 4
    assert {user["role"] for user in users.json()} == {"USER", "MASTER", "INHERITOR"}

    user_id = next(user["id"] for user in users.json() if user["role"] == "USER")
    cards = client.get("/api/v1/cards", headers={"X-Demo-User-Id": user_id})
    assert cards.status_code == 200
    assert len(cards.json()) == 5
    assert [card["rarity"] for card in cards.json()].count("NORMAL") == 4
    assert [card["rarity"] for card in cards.json()].count("RARE") == 1
    assert all(card["question"] and card["interpretation"] for card in cards.json())
    assert all(card["quantity"] == 0 for card in cards.json())

    with SessionLocal() as session:
        weights = session.scalars(select(CardType.draw_weight).order_by(CardType.display_order)).all()
        assert weights == [2475, 2475, 2475, 2475, 100]
        assert sum(weights) == 10_000


def test_health_h5_and_share_routes_are_available(client: TestClient):
    health = client.get("/health")
    index = client.get("/")
    share_page = client.get("/g/gt_demo-token")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert index.status_code == share_page.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert 'id="cardList"' in index.text
    assert 'id="claimUserSelect"' in share_page.text
    assert 'id="openGiftButton"' in share_page.text


def test_activity_status_tracks_draws_remaining_today(client: TestClient):
    user_id = users_by_role()[Role.USER][0]
    headers = {"X-Demo-User-Id": user_id}

    initial = client.get("/api/v1/activity-status", headers=headers)
    draw = client.post(
        "/api/v1/draws",
        headers={**headers, "Idempotency-Key": "status-draw-key"},
    )
    updated = client.get("/api/v1/activity-status", headers=headers)

    assert initial.status_code == draw.status_code == updated.status_code == 200
    assert initial.json()["draws_remaining_today"] == 3
    assert draw.json()["draws_remaining_today"] == 2
    assert updated.json()["draws_remaining_today"] == 2


def test_invalid_user_unknown_card_and_unknown_gift_have_stable_errors(client: TestClient):
    malformed_user = client.get(
        "/api/v1/cards", headers={"X-Demo-User-Id": "not-a-uuid"}
    )
    missing_card = create_gift(
        client,
        users_by_role()[Role.MASTER][0],
        str(uuid4()),
    )
    missing_gift = client.get("/api/v1/gift-links/gt_does-not-exist")

    assert malformed_user.status_code == 400
    assert malformed_user.json()["error"]["code"] == "INVALID_DEMO_USER"
    assert missing_card.status_code == 404
    assert missing_card.json()["error"]["code"] == "CARD_TYPE_NOT_FOUND"
    assert missing_gift.status_code == 404
    assert missing_gift.json()["error"]["code"] == "GIFT_LINK_NOT_FOUND"


def test_draw_idempotency_only_adds_one_card_and_one_quota_use(client: TestClient):
    user_id = users_by_role()[Role.USER][0]
    headers = {"X-Demo-User-Id": user_id, "Idempotency-Key": "stable-draw-key"}

    first = client.post("/api/v1/draws", headers=headers)
    replay = client.post("/api/v1/draws", headers=headers)

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert quota_used(user_id, QuotaKind.DRAW) == 1
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(CardTransaction.id)).where(
                CardTransaction.user_id == user_id,
                CardTransaction.source_type == "DRAW",
            )
        ) == 1
        assert session.scalar(
            select(CardBalance.quantity).where(
                CardBalance.user_id == user_id,
                CardBalance.card_type_id == first.json()["card"]["id"],
            )
        ) == 1


def test_draw_daily_limit_resets_without_accumulating(client: TestClient, monkeypatch):
    user_id = users_by_role()[Role.USER][0]
    day_one = datetime(2026, 7, 30, 15, 59, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: day_one)

    for index in range(3):
        response = client.post(
            "/api/v1/draws",
            headers={"X-Demo-User-Id": user_id, "Idempotency-Key": f"day-one-{index}"},
        )
        assert response.status_code == 200
    assert client.post(
        "/api/v1/draws",
        headers={"X-Demo-User-Id": user_id, "Idempotency-Key": "day-one-exhausted"},
    ).status_code == 429

    day_two = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: day_two)
    reset = client.post(
        "/api/v1/draws",
        headers={"X-Demo-User-Id": user_id, "Idempotency-Key": "day-two-first"},
    )

    assert reset.status_code == 200
    assert reset.json()["draws_remaining_today"] == 2
    with SessionLocal() as session:
        usages = session.scalars(
            select(QuotaUsage).where(
                QuotaUsage.user_id == user_id,
                QuotaUsage.kind == QuotaKind.DRAW,
            )
        ).all()
        assert sorted(usage.used for usage in usages) == [1, 3]


def test_gift_creation_is_idempotent_and_does_not_consume_quota_or_inventory(
    client: TestClient,
):
    sender_id = users_by_role()[Role.MASTER][0]
    normal_id = card_ids()[Rarity.NORMAL][0]
    with SessionLocal() as session:
        quantity_before = session.scalar(
            select(CardBalance.quantity).where(
                CardBalance.user_id == sender_id,
                CardBalance.card_type_id == normal_id,
            )
        )

    first = create_gift(client, sender_id, normal_id, key="stable-create-key")
    replay = create_gift(client, sender_id, normal_id, key="stable-create-key")

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 0
    with SessionLocal() as session:
        assert session.scalar(select(func.count(GiftLink.id))) == 1
        assert session.scalar(
            select(CardBalance.quantity).where(
                CardBalance.user_id == sender_id,
                CardBalance.card_type_id == normal_id,
            )
        ) == quantity_before


def test_reusing_idempotency_key_with_different_payload_is_rejected(client: TestClient):
    sender_id = users_by_role()[Role.MASTER][0]
    normal_ids = card_ids()[Rarity.NORMAL]
    assert create_gift(client, sender_id, normal_ids[0], key="reused-create-key").status_code == 201

    conflict = create_gift(client, sender_id, normal_ids[1], key="reused-create-key")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with SessionLocal() as session:
        assert session.scalar(select(func.count(GiftLink.id))) == 1


def test_normal_user_cannot_create_gift_link(client: TestClient):
    user_id = users_by_role()[Role.USER][0]
    response = create_gift(client, user_id, card_ids()[Rarity.NORMAL][0])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SENDER_ROLE_FORBIDDEN"
    with SessionLocal() as session:
        assert session.scalar(select(func.count(GiftLink.id))) == 0


def test_sender_cannot_claim_own_link_and_link_stays_available(client: TestClient):
    sender_id = users_by_role()[Role.MASTER][0]
    token = token_from(create_gift(client, sender_id, card_ids()[Rarity.NORMAL][0]))

    response = claim(client, token, sender_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANNOT_CLAIM_OWN_GIFT"
    assert gift_for_token(token).status == GiftStatus.AVAILABLE
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 0


def test_same_level_and_upward_claims_are_rejected_without_consuming_link(
    client: TestClient,
):
    role_users = users_by_role()
    master_id = role_users[Role.MASTER][0]
    inheritor_id = role_users[Role.INHERITOR][0]
    normal_id = card_ids()[Rarity.NORMAL][0]
    with SessionLocal.begin() as session:
        peer = User(nickname="另一位普通大师", role=Role.MASTER)
        session.add(peer)
        session.flush()
        peer_id = str(peer.id)

    same_level_token = token_from(create_gift(client, master_id, normal_id))
    same_level = claim(client, same_level_token, peer_id)
    upward_token = token_from(create_gift(client, master_id, normal_id))
    upward = claim(client, upward_token, inheritor_id)

    assert same_level.status_code == upward.status_code == 403
    assert same_level.json()["error"]["code"] == "RECIPIENT_ROLE_FORBIDDEN"
    assert upward.json()["error"]["code"] == "RECIPIENT_ROLE_FORBIDDEN"
    assert gift_for_token(same_level_token).status == GiftStatus.AVAILABLE
    assert gift_for_token(upward_token).status == GiftStatus.AVAILABLE
    assert quota_used(master_id, QuotaKind.GIFT_NORMAL) == 0


def test_allowed_role_matrix_succeeds(client: TestClient):
    role_users = users_by_role()
    normal_id = card_ids()[Rarity.NORMAL][0]

    master_to_user = token_from(
        create_gift(client, role_users[Role.MASTER][0], normal_id)
    )
    inheritor_to_master = token_from(
        create_gift(client, role_users[Role.INHERITOR][0], normal_id)
    )

    assert claim(client, master_to_user, role_users[Role.USER][0]).status_code == 200
    assert claim(client, inheritor_to_master, role_users[Role.MASTER][0]).status_code == 200


def test_recipient_daily_limit_failure_keeps_link_for_another_user(client: TestClient):
    role_users = users_by_role()
    sender_id = role_users[Role.MASTER][0]
    first_user, second_user = role_users[Role.USER]
    normal_id = card_ids()[Rarity.NORMAL][0]
    first_token = token_from(create_gift(client, sender_id, normal_id))
    second_token = token_from(create_gift(client, sender_id, normal_id))

    assert claim(client, first_token, first_user).status_code == 200
    limited = claim(client, second_token, first_user)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RECIPIENT_DAILY_LIMIT_REACHED"
    assert gift_for_token(second_token).status == GiftStatus.AVAILABLE
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 1
    assert claim(client, second_token, second_user).status_code == 200
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 2


def test_rare_weekly_receive_limit_resets_on_monday(client: TestClient, monkeypatch):
    role_users = users_by_role()
    recipient_id = role_users[Role.USER][0]
    rare_id = card_ids()[Rarity.RARE][0]
    sunday = datetime(2026, 8, 2, 15, 59, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: sunday)
    first_token = token_from(
        create_gift(client, role_users[Role.MASTER][0], rare_id)
    )
    assert claim(client, first_token, recipient_id).status_code == 200

    same_week_token = token_from(
        create_gift(client, role_users[Role.INHERITOR][0], rare_id)
    )
    limited = claim(client, same_week_token, recipient_id)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RECIPIENT_WEEKLY_LIMIT_REACHED"
    assert gift_for_token(same_week_token).status == GiftStatus.AVAILABLE
    assert quota_used(role_users[Role.INHERITOR][0], QuotaKind.GIFT_RARE) == 0

    monday = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: monday)
    reset = claim(client, same_week_token, recipient_id)

    assert reset.status_code == 200
    with SessionLocal() as session:
        receive_usages = session.scalars(
            select(QuotaUsage).where(
                QuotaUsage.user_id == recipient_id,
                QuotaUsage.kind == QuotaKind.RECEIVE_RARE,
            )
        ).all()
        assert sorted(usage.used for usage in receive_usages) == [1, 1]


def test_rare_sender_weekly_quota_resets_on_monday(client: TestClient, monkeypatch):
    role_users = users_by_role()
    sender_id = role_users[Role.MASTER][0]
    first_user, second_user = role_users[Role.USER]
    rare_id = card_ids()[Rarity.RARE][0]
    sunday = datetime(2026, 8, 2, 15, 59, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: sunday)
    first_token = token_from(create_gift(client, sender_id, rare_id))
    second_token = token_from(create_gift(client, sender_id, rare_id))

    assert claim(client, first_token, first_user).status_code == 200
    exhausted = claim(client, second_token, second_user)
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "SENDER_QUOTA_EXHAUSTED"
    assert gift_for_token(second_token).status == GiftStatus.AVAILABLE
    assert quota_used(second_user, QuotaKind.RECEIVE_RARE) == 0

    monday = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(services, "now_utc", lambda: monday)
    reset = claim(client, second_token, second_user)

    assert reset.status_code == 200
    with SessionLocal() as session:
        gift_usages = session.scalars(
            select(QuotaUsage).where(
                QuotaUsage.user_id == sender_id,
                QuotaUsage.kind == QuotaKind.GIFT_RARE,
            )
        ).all()
        assert sorted(usage.used for usage in gift_usages) == [1, 1]


def test_claim_idempotency_does_not_duplicate_card_or_quotas(client: TestClient):
    role_users = users_by_role()
    sender_id = role_users[Role.MASTER][0]
    recipient_id = role_users[Role.USER][0]
    token = token_from(create_gift(client, sender_id, card_ids()[Rarity.NORMAL][0]))

    first = claim(client, token, recipient_id, key="stable-claim-key")
    replay = claim(client, token, recipient_id, key="stable-claim-key")

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 1
    assert quota_used(recipient_id, QuotaKind.RECEIVE_NORMAL) == 1
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(CardTransaction.id)).where(
                CardTransaction.source_type == "GIFT_CLAIM"
            )
        ) == 1


def test_expired_link_does_not_consume_quotas_or_issue_card(client: TestClient):
    role_users = users_by_role()
    sender_id = role_users[Role.MASTER][0]
    recipient_id = role_users[Role.USER][0]
    token = token_from(create_gift(client, sender_id, card_ids()[Rarity.NORMAL][0]))
    gift = gift_for_token(token)
    with SessionLocal.begin() as session:
        stored = session.get(GiftLink, gift.id)
        stored.expires_at = services.now_utc() - timedelta(seconds=1)

    response = claim(client, token, recipient_id)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "GIFT_LINK_EXPIRED"
    assert gift_for_token(token).status == GiftStatus.AVAILABLE
    assert quota_used(sender_id, QuotaKind.GIFT_NORMAL) == 0
    assert quota_used(recipient_id, QuotaKind.RECEIVE_NORMAL) == 0
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(CardTransaction.id)).where(
                CardTransaction.source_type == "GIFT_CLAIM"
            )
        ) == 0
