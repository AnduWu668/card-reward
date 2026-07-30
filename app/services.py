import hashlib
import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError
from app.models import (
    CardBalance,
    CardTransaction,
    CardType,
    GiftLink,
    GiftStatus,
    IdempotencyRecord,
    QuotaKind,
    QuotaUsage,
    Rarity,
    Role,
    User,
)

ROLE_RANK = {Role.USER: 0, Role.MASTER: 1, Role.INHERITOR: 2}
GIFT_LIMITS = {
    Role.USER: {Rarity.NORMAL: 0, Rarity.RARE: 0},
    Role.MASTER: {Rarity.NORMAL: 3, Rarity.RARE: 1},
    Role.INHERITOR: {Rarity.NORMAL: 8, Rarity.RARE: 2},
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def current_user(session: Session, raw_user_id: str) -> User:
    try:
        user_id = uuid.UUID(raw_user_id)
    except ValueError as exc:
        raise AppError(400, "INVALID_DEMO_USER", "X-Demo-User-Id 格式不正确") from exc
    user = session.get(User, user_id)
    if not user:
        raise AppError(404, "DEMO_USER_NOT_FOUND", "演示用户不存在")
    return user


def local_period_start(kind: QuotaKind, at: datetime | None = None) -> date:
    local_day = (at or now_utc()).astimezone(ZoneInfo(settings.activity_timezone)).date()
    if kind in {QuotaKind.GIFT_RARE, QuotaKind.RECEIVE_RARE}:
        return local_day - timedelta(days=local_day.weekday())
    return local_day


def quota_kind(prefix: str, rarity: Rarity) -> QuotaKind:
    return QuotaKind[f"{prefix}_{rarity.value}"]


def quota_limit(user: User, kind: QuotaKind) -> int:
    if kind == QuotaKind.DRAW:
        return 3
    if kind == QuotaKind.RECEIVE_NORMAL:
        return 1
    if kind == QuotaKind.RECEIVE_RARE:
        return 1
    rarity = Rarity.RARE if kind == QuotaKind.GIFT_RARE else Rarity.NORMAL
    return GIFT_LIMITS[user.role][rarity]


def lock_idempotency(session: Session, actor: User, operation: str, key: str) -> None:
    lock_key = f"{actor.id}:{operation}:{key}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )


def request_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def replay_idempotency(
    session: Session, actor: User, operation: str, key: str, digest: str
) -> dict | None:
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor.id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if not record:
        return None
    if record.request_hash != digest:
        raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "该幂等键已用于不同请求")
    if record.http_status >= 400:
        error = record.response_body["error"]
        raise AppError(record.http_status, error["code"], error["message"])
    return record.response_body


def save_idempotency(
    session: Session,
    actor: User,
    operation: str,
    key: str,
    digest: str,
    response: dict,
    http_status: int = 200,
) -> None:
    session.add(
        IdempotencyRecord(
            actor_id=actor.id,
            operation=operation,
            key=key,
            request_hash=digest,
            response_body=response,
            http_status=http_status,
        )
    )


def cache_app_error(
    session: Session,
    actor: User,
    operation: str,
    key: str,
    digest: str,
    error: AppError,
) -> None:
    """Persist a deterministic business failure after rolling back command side effects."""
    actor_id = actor.id
    session.rollback()
    lock_key = f"{actor_id}:{operation}:{key}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    existing = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if not existing:
        session.add(
            IdempotencyRecord(
                actor_id=actor_id,
                operation=operation,
                key=key,
                request_hash=digest,
                response_body={"error": {"code": error.code, "message": error.message}},
                http_status=error.status_code,
            )
        )
        session.commit()


def lock_quota(session: Session, user: User, kind: QuotaKind) -> QuotaUsage:
    period_start = local_period_start(kind)
    session.execute(
        insert(QuotaUsage)
        .values(user_id=user.id, kind=kind, period_start=period_start, used=0)
        .on_conflict_do_nothing()
    )
    return session.scalar(
        select(QuotaUsage)
        .where(
            QuotaUsage.user_id == user.id,
            QuotaUsage.kind == kind,
            QuotaUsage.period_start == period_start,
        )
        .with_for_update()
    )


def consume_quota(
    session: Session, user: User, kind: QuotaKind, error_code: str, error_message: str
) -> int:
    usage = lock_quota(session, user, kind)
    limit = quota_limit(user, kind)
    if usage.used >= limit:
        raise AppError(429, error_code, error_message)
    usage.used += 1
    return limit - usage.used


def card_dict(card: CardType, quantity: int) -> dict:
    return {
        "id": str(card.id),
        "code": card.code,
        "name": card.name,
        "rarity": card.rarity.value,
        "quantity": quantity,
        "question": card.question,
        "interpretation": card.interpretation,
        "display_order": card.display_order,
    }


def increment_card(
    session: Session, user: User, card: CardType, source_type: str, source_id: str
) -> int:
    session.execute(
        insert(CardBalance)
        .values(user_id=user.id, card_type_id=card.id, quantity=1)
        .on_conflict_do_update(
            index_elements=[CardBalance.user_id, CardBalance.card_type_id],
            set_={"quantity": CardBalance.quantity + 1, "updated_at": now_utc()},
        )
    )
    quantity = session.scalar(
        select(CardBalance.quantity).where(
            CardBalance.user_id == user.id, CardBalance.card_type_id == card.id
        )
    )
    session.add(
        CardTransaction(
            user_id=user.id,
            card_type_id=card.id,
            delta=1,
            source_type=source_type,
            source_id=source_id,
        )
    )
    return quantity


def list_cards(session: Session, user: User) -> list[dict]:
    rows = session.execute(
        select(CardType, func.coalesce(CardBalance.quantity, 0))
        .outerjoin(
            CardBalance,
            (CardBalance.card_type_id == CardType.id) & (CardBalance.user_id == user.id),
        )
        .order_by(CardType.display_order)
    ).all()
    return [card_dict(card, quantity) for card, quantity in rows]


def activity_status(session: Session, user: User) -> dict:
    used = session.scalar(
        select(QuotaUsage.used).where(
            QuotaUsage.user_id == user.id,
            QuotaUsage.kind == QuotaKind.DRAW,
            QuotaUsage.period_start == local_period_start(QuotaKind.DRAW),
        )
    )
    return {"draws_remaining_today": max(0, quota_limit(user, QuotaKind.DRAW) - (used or 0))}


def draw_card(session: Session, user: User, key: str) -> dict:
    operation = "draw"
    digest = request_digest({})
    lock_idempotency(session, user, operation, key)
    if replay := replay_idempotency(session, user, operation, key, digest):
        return replay

    remaining = consume_quota(
        session, user, QuotaKind.DRAW, "DRAW_LIMIT_REACHED", "今日 3 次抽卡机会已用完"
    )
    cards = session.scalars(select(CardType).order_by(CardType.display_order)).all()
    ticket = secrets.randbelow(sum(card.draw_weight for card in cards))
    cursor = 0
    selected = cards[-1]
    for card in cards:
        cursor += card.draw_weight
        if ticket < cursor:
            selected = card
            break
    draw_id = uuid.uuid4()
    quantity = increment_card(session, user, selected, "DRAW", str(draw_id))
    response = {
        "draw_id": str(draw_id),
        "card": card_dict(selected, quantity),
        "draws_remaining_today": remaining,
    }
    save_idempotency(session, user, operation, key, digest, response)
    return response


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_gift(session: Session, sender: User, card_type_id: uuid.UUID, key: str) -> dict:
    operation = "create_gift"
    digest = request_digest({"card_type_id": card_type_id})
    lock_idempotency(session, sender, operation, key)
    if replay := replay_idempotency(session, sender, operation, key, digest):
        return replay
    if sender.role == Role.USER:
        raise AppError(403, "SENDER_ROLE_FORBIDDEN", "普通用户不能赠卡")
    card = session.get(CardType, card_type_id)
    if not card:
        raise AppError(404, "CARD_TYPE_NOT_FOUND", "卡片不存在")

    local_now = now_utc().astimezone(ZoneInfo(settings.activity_timezone))
    local_midnight = datetime.combine(local_now.date(), datetime.min.time(), local_now.tzinfo)
    created_today = session.scalar(
        select(func.count(GiftLink.id)).where(
            GiftLink.sender_id == sender.id,
            GiftLink.created_at >= local_midnight.astimezone(UTC),
        )
    )
    if created_today >= settings.gift_link_daily_creation_limit:
        raise AppError(429, "GIFT_LINK_CREATE_LIMIT_REACHED", "今日分享链接创建次数已达上限")

    raw_token = f"gt_{secrets.token_urlsafe(24)}"
    gift = GiftLink(
        token_hash=token_hash(raw_token),
        sender_id=sender.id,
        card_type_id=card.id,
        expires_at=now_utc() + timedelta(days=settings.gift_link_ttl_days),
    )
    session.add(gift)
    session.flush()
    response = {
        "gift_id": str(gift.id),
        "share_url": f"{settings.app_base_url}/g/{raw_token}",
        "expires_at": gift.expires_at.isoformat(),
        "status": gift.status.value,
        "card": card_dict(card, 0),
    }
    save_idempotency(session, sender, operation, key, digest, response, 201)
    return response


def find_gift(session: Session, raw_token: str, for_update: bool = False) -> GiftLink:
    statement = select(GiftLink).where(GiftLink.token_hash == token_hash(raw_token))
    if for_update:
        statement = statement.with_for_update()
    gift = session.scalar(statement)
    if not gift:
        raise AppError(404, "GIFT_LINK_NOT_FOUND", "赠卡链接不存在")
    return gift


def gift_preview(session: Session, raw_token: str) -> dict:
    gift = find_gift(session, raw_token)
    if gift.expires_at <= now_utc() and gift.status == GiftStatus.AVAILABLE:
        raise AppError(410, "GIFT_LINK_EXPIRED", "赠卡链接已过期")
    return {
        "sender_id": str(gift.sender.id),
        "sender_nickname": gift.sender.nickname,
        "sender_role": gift.sender.role.value,
        "card": card_dict(gift.card_type, 0),
        "status": gift.status.value,
        "expires_at": gift.expires_at.isoformat(),
    }


def claim_gift(session: Session, recipient: User, raw_token: str, key: str) -> dict:
    operation = "claim_gift"
    digest = request_digest({"token_hash": token_hash(raw_token)})
    lock_idempotency(session, recipient, operation, key)
    if replay := replay_idempotency(session, recipient, operation, key, digest):
        return replay

    gift = find_gift(session, raw_token, for_update=True)
    if gift.status == GiftStatus.CLAIMED:
        raise AppError(409, "GIFT_ALREADY_CLAIMED", "这张卡已被领取")
    if gift.expires_at <= now_utc():
        raise AppError(410, "GIFT_LINK_EXPIRED", "赠卡链接已过期")
    sender = session.get(User, gift.sender_id)
    if sender.id == recipient.id:
        raise AppError(409, "CANNOT_CLAIM_OWN_GIFT", "不能领取自己发出的卡")
    if ROLE_RANK[sender.role] <= ROLE_RANK[recipient.role]:
        raise AppError(403, "RECIPIENT_ROLE_FORBIDDEN", "只能由高级身份向低级身份赠卡")

    gift_kind = quota_kind("GIFT", gift.card_type.rarity)
    receive_kind = quota_kind("RECEIVE", gift.card_type.rarity)
    consume_quota(
        session, sender, gift_kind, "SENDER_QUOTA_EXHAUSTED", "赠送者当前周期额度已用完"
    )
    receive_error = (
        ("RECIPIENT_WEEKLY_LIMIT_REACHED", "本周麒麟卡领取次数已用完")
        if gift.card_type.rarity == Rarity.RARE
        else ("RECIPIENT_DAILY_LIMIT_REACHED", "今日普通卡领取次数已用完")
    )
    consume_quota(session, recipient, receive_kind, *receive_error)

    claimed_at = now_utc()
    gift.status = GiftStatus.CLAIMED
    gift.claimed_by = recipient.id
    gift.claimed_at = claimed_at
    quantity = increment_card(session, recipient, gift.card_type, "GIFT_CLAIM", str(gift.id))
    response = {
        "gift_id": str(gift.id),
        "claimed_at": claimed_at.isoformat(),
        "card": card_dict(gift.card_type, quantity),
    }
    save_idempotency(session, recipient, operation, key, digest, response)
    return response
